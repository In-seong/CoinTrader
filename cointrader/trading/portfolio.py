"""멀티코인 포트폴리오 봇.

하나의 봇 = 전략 1개(앙상블 가능) + 코인 유니버스 + 독립 계좌.
매 사이클마다 유니버스 전체의 시그널을 계산해
 - 보유 중인 코인: 청산 시그널/손절/익절 체크
 - 미보유 코인: 진입 시그널이 뜬 것들을 max_positions 한도 내에서 매수
한다. 거래/자산 곡선은 jsonl 로 남겨 나중에 봇끼리 비교한다.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from cointrader.config import PROJECT_ROOT
from cointrader.notify import notify
from cointrader.strategies import get as get_strategy
from cointrader.strategies.base import Strategy
from cointrader.strategies.ensemble import signals_to_state

from .broker import Broker, Fill, LiveSubBroker, PaperBroker

log = logging.getLogger(__name__)
BOT_DATA_DIR = PROJECT_ROOT / "data" / "bots"


@dataclass
class BotConfig:
    name: str
    strategy: str
    tickers: list[str]
    params: dict[str, Any] = field(default_factory=dict)
    interval: str = "minute60"
    cash: float = 1_000_000.0
    max_positions: int = 1
    position_size: float = 1.0      # 슬롯당 예산 비율
    stop_loss: float | None = None
    take_profit: float | None = None
    enabled: bool = True
    #: paper(모의) | live(실거래). live 는 .env 의 업비트 키 + 실계좌 잔고 필요.
    mode: str = "paper"
    #: 봇 시작(또는 새 코인 추가) 시점에 전략이 이미 '보유 구간'이면 바로 진입.
    #: 추세 전략(ma_cross 등)은 크로스 봉에서만 시그널을 내므로, 이게 없으면 다음 크로스까지 대기만 한다.
    enter_on_start: bool = True

    def build_strategy(self) -> Strategy:
        return get_strategy(self.strategy, **self.params)


class PortfolioBot:
    def __init__(self, cfg: BotConfig, broker: Broker | None = None):
        self.cfg = cfg
        self.strategy = cfg.build_strategy()
        self.dir = BOT_DATA_DIR / cfg.name
        self.dir.mkdir(parents=True, exist_ok=True)
        if broker is not None:
            self.broker = broker
        elif cfg.mode == "live":
            self.broker = LiveSubBroker(initial_cash=cfg.cash, state_file=self.dir / "account.json")
        else:
            self.broker = PaperBroker(initial_cash=cfg.cash, state_file=self.dir / "account.json")
        self.state_file = self.dir / "state.json"
        self.trades_file = self.dir / "trades.jsonl"
        self.equity_file = self.dir / "equity.jsonl"
        self.state: dict[str, Any] = self._load_state()

    # ---- 영속 상태 ----------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"last_bar": {}, "entries": {}}  # entries: ticker -> {price, time, reason}

    def _save_state(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=2, default=str))

    def _append(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a") as f:
            f.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")

    # ---- 주문 ---------------------------------------------------------
    def _buy(self, ticker: str, krw: float, reason: str, price_hint: float) -> Fill | None:
        fill = self.broker.buy_market(ticker, krw)
        if not fill:
            return None
        now = datetime.now().isoformat(timespec="seconds")
        self.state["entries"][ticker] = {"price": fill.price, "time": now, "reason": reason,
                                         "krw": fill.krw, "qty": fill.qty}
        self._save_state()
        self._append(self.trades_file, {"time": now, "ticker": ticker, "side": "buy", "price": fill.price,
                                        "qty": fill.qty, "krw": fill.krw, "fee": fill.fee, "reason": reason})
        self._report(fill, reason)
        return fill

    def _sell(self, ticker: str, reason: str) -> Fill | None:
        pos = self.broker.get_position(ticker)
        if not pos.is_open:
            return None
        qty_before, avg_before = pos.qty, pos.avg_price  # sell 이후 pos 객체가 비워지므로 먼저 보관
        fill = self.broker.sell_market(ticker, qty_before)
        if not fill:
            return None
        now = datetime.now().isoformat(timespec="seconds")
        entry = self.state["entries"].pop(ticker, None) or {}
        entry_price = entry.get("price") or avg_before
        # 손익 = 매도 순수령액 - 매수 투입액 (양쪽 수수료·슬리피지 모두 반영)
        cost = entry.get("krw") or qty_before * entry_price
        pnl = fill.krw - cost if cost else 0.0
        pnl_pct = fill.krw / cost - 1 if cost else 0.0
        self._save_state()
        self._append(self.trades_file, {"time": now, "ticker": ticker, "side": "sell", "price": fill.price,
                                        "qty": fill.qty, "krw": fill.krw, "fee": fill.fee, "reason": reason,
                                        "entry_price": entry_price, "entry_time": entry.get("time"),
                                        "pnl": pnl, "pnl_pct": pnl_pct})
        self._report(fill, f"{reason} ({pnl_pct * 100:+.2f}%)")
        return fill

    def _report(self, fill: Fill, reason: str) -> None:
        side = "매수" if fill.side == "buy" else "매도"
        tag = "🔴 LIVE " if self.cfg.mode == "live" else ""
        msg = (f"<b>{tag}[{self.cfg.name}] {side} {fill.ticker}</b>\n"
               f"가격 {fill.price:,.0f} / 금액 {fill.krw:,.0f}원\n사유: {reason}\n"
               f"전략: {self.strategy!r}")
        log.info("%s", msg.replace("<b>", "").replace("</b>", "").replace("\n", " | "))
        notify(msg)

    # ---- 평가 ---------------------------------------------------------
    def equity(self, prices: dict[str, float]) -> float:
        total = self.broker.get_krw_balance()
        for tk in self.cfg.tickers:
            pos = self.broker.get_position(tk)
            if pos.is_open and tk in prices:
                total += pos.qty * prices[tk]
        return total

    def open_positions(self) -> list[str]:
        return [tk for tk in self.cfg.tickers if self.broker.get_position(tk).is_open]

    def _check_stops(self, prices: dict[str, float], actions: list[str]) -> None:
        cfg = self.cfg
        for tk in self.open_positions():
            price = prices.get(tk)
            entry = (self.state["entries"].get(tk) or {}).get("price") or self.broker.get_position(tk).avg_price
            if not price or not entry:
                continue
            chg = price / entry - 1
            if cfg.stop_loss is not None and chg <= -cfg.stop_loss:
                if self._sell(tk, "stop_loss"):
                    actions.append(f"stop {tk}")
            elif cfg.take_profit is not None and chg >= cfg.take_profit:
                if self._sell(tk, "take_profit"):
                    actions.append(f"take {tk}")

    # ---- 한 사이클 ----------------------------------------------------
    def step(self, dfs: dict[str, pd.DataFrame], prices: dict[str, float]) -> list[str]:
        """dfs/prices 는 러너가 유니버스 전체를 한 번에 받아 넘겨준다. 실행한 액션 목록 반환."""
        cfg = self.cfg
        actions: list[str] = []
        sigs: dict[str, pd.DataFrame] = {}
        new_bar: dict[str, bool] = {}
        first_seen: set[str] = set()
        for tk in cfg.tickers:
            df = dfs.get(tk)
            if df is None or len(df) < self.strategy.warmup + 2:
                continue
            sigs[tk] = self.strategy.signals(df)
            bar = str(df.index[-1])
            if tk not in self.state["last_bar"]:
                first_seen.add(tk)
            new_bar[tk] = self.state["last_bar"].get(tk) != bar
            self.state["last_bar"][tk] = bar

        # 코인 간 비교 전략(로테이션): 보유 집합을 통째로 결정
        if getattr(self.strategy, "cross_sectional", False):
            if any(new_bar.values()) or first_seen:
                closes = pd.concat({tk: dfs[tk]["close"].iloc[:-1] for tk in sigs}, axis=1).ffill()
                desired = self.strategy.target_set(closes).iloc[-1]
                desired = {tk for tk in sigs if bool(desired.get(tk, False))}
                for tk in self.open_positions():
                    if tk not in desired and self._sell(tk, "rotation"):
                        actions.append(f"sell {tk}")
                held = set(self.open_positions())
                to_buy = [tk for tk in sigs if tk in desired and tk not in held and tk in prices]
                slots = cfg.max_positions - len(held)
                for tk in to_buy[:max(slots, 0)]:
                    budget = self.broker.get_krw_balance() / slots * cfg.position_size
                    if self._buy(tk, budget, "rotation", prices[tk]):
                        actions.append(f"buy {tk}")
                        slots -= 1
            self._check_stops(prices, actions)
            self._save_state()
            self._append(self.equity_file, {"time": datetime.now().isoformat(timespec="seconds"),
                                            "equity": self.equity(prices), "cash": self.broker.get_krw_balance(),
                                            "positions": self.open_positions()})
            return actions

        # 1) 청산 (시그널 / 손절 / 익절)
        for tk in self.open_positions():
            if tk not in sigs:
                continue
            price = prices.get(tk)
            entry = (self.state["entries"].get(tk) or {}).get("price") or self.broker.get_position(tk).avg_price
            if new_bar[tk] and bool(sigs[tk].iloc[-2]["long_exit"]):
                if self._sell(tk, "signal"):
                    actions.append(f"sell {tk}")
            elif price and entry:
                chg = price / entry - 1
                if cfg.stop_loss is not None and chg <= -cfg.stop_loss:
                    if self._sell(tk, "stop_loss"):
                        actions.append(f"stop {tk}")
                elif cfg.take_profit is not None and chg >= cfg.take_profit:
                    if self._sell(tk, "take_profit"):
                        actions.append(f"take {tk}")

        # 2) 진입 후보
        candidates: list[tuple[str, str]] = []
        held = set(self.open_positions())
        for tk, sig in sigs.items():
            if tk in held or tk not in prices:
                continue
            if new_bar[tk] and bool(sig.iloc[-2]["long_entry"]):
                candidates.append((tk, "signal"))
                continue
            if tk in first_seen and cfg.enter_on_start:
                # 직전 완성 봉까지의 시그널을 상태로 환산해 이미 보유 구간이면 진입
                state = signals_to_state(sig.iloc[:-1], dfs[tk]["high"].iloc[:-1])
                if bool(state.iloc[-1]):
                    candidates.append((tk, "start_in_position"))
                    continue
            target = float(sig.iloc[-1]["entry_price"])
            if not math.isnan(target) and prices[tk] >= target:
                candidates.append((tk, f"breakout ≥ {target:,.0f}"))

        slots = cfg.max_positions - len(held)
        for tk, reason in candidates[:max(slots, 0)]:
            budget = self.broker.get_krw_balance() / slots * cfg.position_size
            if self._buy(tk, budget, reason, prices[tk]):
                actions.append(f"buy {tk}")
                slots -= 1

        self._save_state()
        self._append(self.equity_file, {"time": datetime.now().isoformat(timespec="seconds"),
                                        "equity": self.equity(prices), "cash": self.broker.get_krw_balance(),
                                        "positions": self.open_positions()})
        return actions

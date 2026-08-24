"""실시간 매매 루프.

매 poll 마다:
 1. 최신 봉 데이터 조회 → 전략 시그널 생성
 2. 새 봉이 열렸으면 직전 완성 봉의 long_entry/long_exit 를 시가(현재가)에 실행
 3. 현재 봉의 entry_price 가 있고 현재가가 그 이상이면 즉시 매수
 4. 손절/익절 체크
백테스트 엔진과 동일한 시그널 규약을 사용한다.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from cointrader.config import PROJECT_ROOT
from cointrader.data import load_ohlcv
from cointrader.notify import notify
from cointrader.strategies.base import Strategy

from .broker import Broker, Fill

log = logging.getLogger(__name__)


@dataclass
class RunnerConfig:
    ticker: str = "KRW-BTC"
    interval: str = "day"
    poll_seconds: int = 60
    position_size: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None
    lookback: int = 200


class LiveRunner:
    def __init__(self, strategy: Strategy, broker: Broker, cfg: RunnerConfig,
                 state_file: Path | None = None):
        self.strategy = strategy
        self.broker = broker
        self.cfg = cfg
        self.state_file = state_file or PROJECT_ROOT / "data" / f"runner_{cfg.ticker}_{cfg.interval}.json"
        self.state = self._load_state()

    # ---- 상태 ---------------------------------------------------------
    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"last_bar": None, "entry_price": None}

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, default=str))

    # ---- 주문 ---------------------------------------------------------
    def _buy(self, reason: str) -> Fill | None:
        krw = self.broker.get_krw_balance() * self.cfg.position_size
        fill = self.broker.buy_market(self.cfg.ticker, krw)
        if fill:
            self.state["entry_price"] = fill.price
            self._save_state()
            self._report(fill, reason)
        return fill

    def _sell(self, reason: str) -> Fill | None:
        pos = self.broker.get_position(self.cfg.ticker)
        if not pos.is_open:
            return None
        fill = self.broker.sell_market(self.cfg.ticker, pos.qty)
        if fill:
            entry = self.state.get("entry_price") or pos.avg_price
            pnl_pct = (fill.price / entry - 1) * 100 if entry else 0
            self.state["entry_price"] = None
            self._save_state()
            self._report(fill, f"{reason} ({pnl_pct:+.2f}%)")
        return fill

    def _report(self, fill: Fill, reason: str) -> None:
        side = "매수" if fill.side == "buy" else "매도"
        msg = (f"<b>[{side}] {fill.ticker}</b>\n"
               f"가격 {fill.price:,.0f} / 수량 {fill.qty:.6f} / 금액 {fill.krw:,.0f}원\n"
               f"사유: {reason}\n전략: {self.strategy!r}\n"
               f"잔고(KRW) {self.broker.get_krw_balance():,.0f}원")
        log.info(msg.replace("<b>", "").replace("</b>", ""))
        notify(msg)

    # ---- 한 사이클 ----------------------------------------------------
    def step(self) -> dict:
        cfg = self.cfg
        df = load_ohlcv(cfg.ticker, cfg.interval, count=max(cfg.lookback, self.strategy.warmup + 5))
        sig = self.strategy.signals(df)
        price = self.broker.get_price(cfg.ticker)
        pos = self.broker.get_position(cfg.ticker)

        current_bar = df.index[-1]
        completed = sig.iloc[-2]  # 직전 완성 봉 시그널
        current = sig.iloc[-1]
        info = {"time": datetime.now().isoformat(timespec="seconds"), "bar": str(current_bar),
                "price": price, "in_position": pos.is_open, "action": None}

        # 1) 새 봉 시작 → 직전 봉 종가 시그널 실행
        new_bar = self.state.get("last_bar") != str(current_bar)
        if new_bar:
            if pos.is_open and bool(completed["long_exit"]):
                self._sell("signal"); info["action"] = "sell"
                pos = self.broker.get_position(cfg.ticker)
            elif not pos.is_open and bool(completed["long_entry"]):
                self._buy("signal"); info["action"] = "buy"
                pos = self.broker.get_position(cfg.ticker)
            self.state["last_bar"] = str(current_bar)
            self._save_state()

        # 2) 봉 내 돌파 진입
        target = float(current["entry_price"])
        if not pos.is_open and not math.isnan(target) and price >= target:
            self._buy(f"breakout ≥ {target:,.0f}"); info["action"] = "buy"
            pos = self.broker.get_position(cfg.ticker)

        # 3) 손절/익절
        if pos.is_open:
            entry = self.state.get("entry_price") or pos.avg_price
            if entry:
                chg = price / entry - 1
                if cfg.stop_loss is not None and chg <= -cfg.stop_loss:
                    self._sell("stop_loss"); info["action"] = "sell"
                elif cfg.take_profit is not None and chg >= cfg.take_profit:
                    self._sell("take_profit"); info["action"] = "sell"

        info["target"] = None if math.isnan(target) else target
        info["in_position"] = self.broker.get_position(cfg.ticker).is_open
        return info

    def run(self, once: bool = False) -> None:
        notify(f"🤖 CoinTrader 시작: {self.cfg.ticker} [{self.cfg.interval}] {self.strategy!r}", silent=True)
        while True:
            try:
                info = self.step()
                log.info("%s price=%s pos=%s target=%s action=%s", info["bar"], f"{info['price']:,.0f}",
                         info["in_position"], info["target"] and f"{info['target']:,.0f}", info["action"])
            except KeyboardInterrupt:
                raise
            except Exception as e:  # 네트워크 오류 등 → 알림 후 계속
                log.exception("step failed")
                notify(f"⚠️ CoinTrader 오류: {e}")
            if once:
                break
            time.sleep(self.cfg.poll_seconds)

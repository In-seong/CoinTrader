"""bots.yaml 에 정의된 여러 봇을 한 프로세스에서 돌린다.

- 유니버스 전체 시세/캔들은 사이클당 한 번만 받아 모든 봇이 공유 (API 호출 최소화)
- 봇은 각자 독립 계좌(paper) 와 기록을 가진다
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pyupbit
import yaml

from cointrader.config import PROJECT_ROOT
from cointrader.data import load_ohlcv
from cointrader.notify import notify

from .broker import PaperBroker
from .portfolio import BotConfig, PortfolioBot

log = logging.getLogger(__name__)
DEFAULT_BOTS_FILE = PROJECT_ROOT / "bots.yaml"


def load_bot_configs(path: Path | None = None) -> tuple[list[BotConfig], dict[str, Any]]:
    path = path or DEFAULT_BOTS_FILE
    raw = yaml.safe_load(path.read_text()) or {}
    defaults = raw.get("defaults", {})
    universe: list[str] = raw.get("universe", [])
    bots: list[BotConfig] = []
    for b in raw.get("bots", []):
        merged = {**defaults, **b}
        tickers = merged.get("tickers", "universe")
        if tickers == "universe":
            tickers = universe
        elif isinstance(tickers, str):
            tickers = [tickers]
        merged["tickers"] = list(tickers)
        merged.pop("poll", None)
        bots.append(BotConfig(**merged))
    names = [b.name for b in bots]
    if len(names) != len(set(names)):
        raise ValueError(f"bots.yaml: 봇 이름 중복 {names}")
    return bots, {"poll": defaults.get("poll", 60), "universe": universe}


class MultiRunner:
    def __init__(self, bots: list[BotConfig], poll_seconds: int = 60):
        self.bots = [PortfolioBot(b) for b in bots if b.enabled]
        self.poll = poll_seconds
        if not self.bots:
            raise ValueError("활성화된 봇이 없습니다")
        self._check_live_budgets()

    def _check_live_budgets(self) -> None:
        """live 봇들의 가상 예산 합이 실계좌 KRW 를 넘으면 경고 (주문이 스킵될 수 있음)."""
        from .broker import LiveSubBroker

        live = [b for b in self.bots if isinstance(b.broker, LiveSubBroker)]
        if not live:
            return
        total = sum(b.broker.get_krw_balance() for b in live)
        real = float(live[0].broker.api.get_balance("KRW") or 0)
        names = ", ".join(b.cfg.name for b in live)
        msg = f"🔴 실거래 봇 {len(live)}개 활성: {names} · 가상예산 합 {total:,.0f}원 / 실계좌 KRW {real:,.0f}원"
        log.warning(msg)
        notify(msg)
        if total > real:
            warn = f"⚠️ 실거래 예산 합({total:,.0f})이 실계좌 잔고({real:,.0f})보다 큽니다. 일부 주문이 스킵됩니다."
            log.warning(warn)
            notify(warn)

    def _needs(self) -> dict[tuple[str, str], int]:
        """(ticker, interval) -> 필요한 봉 수."""
        need: dict[tuple[str, str], int] = {}
        for bot in self.bots:
            n = max(200, bot.strategy.warmup + 20)
            for tk in bot.cfg.tickers:
                key = (tk, bot.cfg.interval)
                need[key] = max(need.get(key, 0), n)
        return need

    def step(self) -> dict[str, list[str]]:
        need = self._needs()
        dfs: dict[tuple[str, str], Any] = {}
        for (tk, iv), n in need.items():
            try:
                dfs[(tk, iv)] = load_ohlcv(tk, iv, count=n)
            except Exception as e:
                log.warning("ohlcv fail %s %s: %s", tk, iv, e)
            time.sleep(0.12)

        tickers = sorted({tk for tk, _ in need})
        raw = pyupbit.get_current_price(tickers)
        if raw is None:
            raise RuntimeError("현재가 조회 실패")
        prices = {tickers[0]: float(raw)} if isinstance(raw, (int, float)) else {k: float(v) for k, v in raw.items()}

        results: dict[str, list[str]] = {}
        for bot in self.bots:
            if isinstance(bot.broker, PaperBroker):
                bot.broker.price_cache = prices
            bot_dfs = {tk: dfs.get((tk, bot.cfg.interval)) for tk in bot.cfg.tickers}
            try:
                results[bot.cfg.name] = bot.step(bot_dfs, prices)
            except Exception as e:
                log.exception("bot %s failed", bot.cfg.name)
                notify(f"⚠️ [{bot.cfg.name}] 오류: {e}")
                results[bot.cfg.name] = [f"error: {e}"]
        return results

    def run(self, once: bool = False) -> None:
        names = ", ".join(b.cfg.name for b in self.bots)
        log.info("bots: %s (poll %ss)", names, self.poll)
        notify(f"🤖 CoinTrader 멀티봇 시작 ({len(self.bots)}개): {names}", silent=True)
        while True:
            t0 = time.time()
            try:
                res = self.step()
                acted = {k: v for k, v in res.items() if v}
                log.info("cycle %.1fs %s", time.time() - t0, acted or "no action")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.exception("cycle failed")
                notify(f"⚠️ 멀티봇 사이클 오류: {e}")
            if once:
                break
            time.sleep(max(1, self.poll - (time.time() - t0)))

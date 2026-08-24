"""멀티코인 봇 시뮬레이터.

PortfolioBot(실시간 러너)의 규칙을 그대로 과거 데이터에 적용한다.
- 코인별 시그널(long_entry/long_exit/entry_price) 또는 코인 간 비교(target_set)
- max_positions 슬롯, 슬롯당 예산 = 현금 / 남은 슬롯 × position_size
- 청산이 진입보다 먼저 처리돼 같은 봉에서 슬롯이 돈다
- 손절/익절은 봉 내 저가/고가 기준
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from cointrader.strategies.base import Strategy

from .engine import Trade
from .metrics import compute_metrics


@dataclass
class MultiConfig:
    initial_cash: float = 1_000_000.0
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0005
    max_positions: int = 1
    position_size: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass
class MultiResult:
    ticker: str
    interval: str
    strategy: str
    config: MultiConfig
    equity: pd.Series
    trades: list[Trade]
    metrics: dict[str, Any] = field(default_factory=dict)
    fees: float = 0.0
    per_ticker: dict[str, int] = field(default_factory=dict)


class _Book:
    def __init__(self, cfg: MultiConfig):
        self.cfg = cfg
        self.cash = cfg.initial_cash
        self.pos: dict[str, Trade] = {}   # ticker -> 진행 중 거래 (qty 포함)
        self.entry_bar: dict[str, int] = {}
        self.trades: list[Trade] = []
        self.fees = 0.0

    def buy(self, tk: str, t: pd.Timestamp, price: float, bar: int, budget: float) -> None:
        fill = price * (1 + self.cfg.slippage_rate)
        budget = min(budget, self.cash)
        if budget < 5_000:
            return
        qty = budget / (fill * (1 + self.cfg.fee_rate))
        self.cash -= budget
        self.fees += qty * fill * self.cfg.fee_rate
        self.pos[tk] = Trade(entry_time=t, entry_price=fill, qty=qty, ticker=tk)
        self.entry_bar[tk] = bar

    def sell(self, tk: str, t: pd.Timestamp, price: float, bar: int, reason: str) -> None:
        tr = self.pos.pop(tk)
        fill = price * (1 - self.cfg.slippage_rate)
        gross = tr.qty * fill
        proceeds = gross * (1 - self.cfg.fee_rate)
        self.fees += gross * self.cfg.fee_rate
        cost = tr.qty * tr.entry_price * (1 + self.cfg.fee_rate)
        tr.exit_time, tr.exit_price, tr.reason = t, fill, reason
        tr.bars_held = bar - self.entry_bar.pop(tk)
        tr.pnl, tr.pnl_pct = proceeds - cost, proceeds / cost - 1
        self.trades.append(tr)
        self.cash += proceeds

    def equity(self, closes: dict[str, float]) -> float:
        return self.cash + sum(tr.qty * closes.get(tk, tr.entry_price) for tk, tr in self.pos.items())


def run_multi_backtest(dfs: dict[str, pd.DataFrame], strategy: Strategy, cfg: MultiConfig | None = None,
                       interval: str = "day", name: str = "") -> MultiResult:
    cfg = cfg or MultiConfig()
    tickers = list(dfs)
    index = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in dfs.values()])))
    n = len(index)
    al = {tk: df.reindex(index) for tk, df in dfs.items()}
    O = {tk: al[tk]["open"].to_numpy(float) for tk in tickers}
    H = {tk: al[tk]["high"].to_numpy(float) for tk in tickers}
    L = {tk: al[tk]["low"].to_numpy(float) for tk in tickers}
    C = {tk: al[tk]["close"].to_numpy(float) for tk in tickers}
    Cff = {tk: al[tk]["close"].ffill().to_numpy(float) for tk in tickers}

    cross = getattr(strategy, "cross_sectional", False)
    if cross:
        closes = pd.DataFrame({tk: al[tk]["close"] for tk in tickers}).ffill()
        targets = strategy.target_set(closes).reindex(columns=tickers).to_numpy(bool)
        ENTRY = EXIT = EP = None
    else:
        sig = {tk: strategy.signals(dfs[tk]).reindex(index) for tk in tickers}
        ENTRY = {tk: sig[tk]["long_entry"].fillna(False).to_numpy(bool) for tk in tickers}
        EXIT = {tk: sig[tk]["long_exit"].fillna(False).to_numpy(bool) for tk in tickers}
        EP = {tk: sig[tk]["entry_price"].to_numpy(float) for tk in tickers}

    book = _Book(cfg)
    equity = np.empty(n)
    pending_exit: set[str] = set()
    pending_entry: list[str] = []
    start = strategy.warmup

    for i in range(n):
        t = index[i]
        if i >= start:
            valid = {tk for tk in tickers if not math.isnan(O[tk][i])}

            # 1) 청산 (직전 봉 시그널 / 로테이션 이탈) → 시가
            if cross and i >= 1:
                desired = {tk for j, tk in enumerate(tickers) if targets[i - 1][j]}
                pending_exit = {tk for tk in book.pos if tk not in desired}
                pending_entry = [tk for tk in tickers if tk in desired and tk not in book.pos]
            for tk in list(book.pos):
                if tk in pending_exit and tk in valid:
                    book.sell(tk, t, O[tk][i], i, "rotation" if cross else "signal")

            # 2) 진입 (직전 봉 시그널) → 시가
            slots = cfg.max_positions - len(book.pos)
            for tk in pending_entry:
                if slots <= 0:
                    break
                if tk in valid and tk not in book.pos:
                    book.buy(tk, t, O[tk][i], i, book.cash / slots * cfg.position_size)
                    slots -= 1
            pending_exit, pending_entry = set(), []

            # 3) 봉 내 돌파 진입
            if not cross:
                for tk in tickers:
                    if slots <= 0:
                        break
                    ep = EP[tk][i]
                    if tk in valid and tk not in book.pos and not math.isnan(ep) and H[tk][i] >= ep:
                        book.buy(tk, t, max(ep, O[tk][i]), i, book.cash / slots * cfg.position_size)
                        slots -= 1

            # 4) 손절 / 익절 (봉 내)
            for tk in list(book.pos):
                if tk not in valid:
                    continue
                ep = book.pos[tk].entry_price
                same_bar = book.entry_bar[tk] == i
                if cfg.stop_loss is not None and L[tk][i] <= ep * (1 - cfg.stop_loss):
                    px = ep * (1 - cfg.stop_loss)
                    book.sell(tk, t, px if same_bar else min(px, O[tk][i]), i, "stop_loss")
                elif cfg.take_profit is not None and H[tk][i] >= ep * (1 + cfg.take_profit):
                    px = ep * (1 + cfg.take_profit)
                    book.sell(tk, t, px if same_bar else max(px, O[tk][i]), i, "take_profit")

            # 5) 이번 봉 종가 시그널 → 다음 봉 예약
            if not cross:
                for tk in tickers:
                    if tk not in valid:
                        continue
                    if tk in book.pos:
                        if EXIT[tk][i]:
                            pending_exit.add(tk)
                    elif ENTRY[tk][i]:
                        pending_entry.append(tk)

        equity[i] = book.equity({tk: Cff[tk][i] for tk in tickers if not math.isnan(Cff[tk][i])})

    for tk in list(book.pos):  # 평가 청산
        book.sell(tk, index[-1], Cff[tk][-1], n - 1, "end")
    equity[-1] = book.cash

    # 워밍업 구간(전략이 거래할 수 없는 기간)은 성과·B&H 비교에서 제외해 같은 기간을 비교한다
    s0 = min(start, n - 2)
    eq = pd.Series(equity[s0:], index=index[s0:], name="equity")
    # Buy&Hold 비교: 유니버스 동일가중
    norm = pd.DataFrame({tk: al[tk]["close"].ffill() for tk in tickers}).iloc[s0:]
    norm = norm / norm.bfill().iloc[0]
    bh_df = pd.DataFrame({"close": norm.mean(axis=1)}, index=index[s0:])

    res = MultiResult(ticker="+".join(t.replace("KRW-", "") for t in tickers), interval=interval,
                      strategy=name or repr(strategy), config=cfg, equity=eq, trades=book.trades, fees=book.fees)
    res.metrics = compute_metrics(res, bh_df)  # type: ignore[arg-type]
    res.metrics["fees"] = book.fees
    for tr in book.trades:
        res.per_ticker[tr.ticker] = res.per_ticker.get(tr.ticker, 0) + 1
    return res

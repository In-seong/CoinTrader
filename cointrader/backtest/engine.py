"""봉 단위 이벤트 루프 백테스트 엔진 (현물 롱 온리).

체결 규칙
- long_entry / long_exit : 봉 종가에 판단 → 다음 봉 시가에 체결
- entry_price            : 해당 봉의 고가가 목표가 이상이면 목표가(또는 시가 갭상승 시 시가)에 체결
- stop_loss / take_profit: 보유 중 봉의 저가/고가가 기준에 닿으면 그 가격에 체결
- 수수료·슬리피지는 매수/매도 양쪽에 적용
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from cointrader.strategies.base import Strategy


@dataclass
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0005
    position_size: float = 1.0        # 진입 시 자산 대비 비중 (0~1)
    stop_loss: float | None = None    # 예: 0.03 → 진입가 대비 -3%에 손절
    take_profit: float | None = None  # 예: 0.10 → 진입가 대비 +10%에 익절


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    qty: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    reason: str = ""
    ticker: str = ""

    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class BacktestResult:
    ticker: str
    interval: str
    strategy: str
    config: BacktestConfig
    equity: pd.Series
    trades: list[Trade]
    signals: pd.DataFrame
    metrics: dict[str, Any] = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=[f.name for f in Trade.__dataclass_fields__.values()])
        return pd.DataFrame([t.__dict__ for t in self.trades])


class _Portfolio:
    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.cash = cfg.initial_cash
        self.qty = 0.0
        self.trade: Trade | None = None
        self.trades: list[Trade] = []

    @property
    def in_position(self) -> bool:
        return self.qty > 0

    def buy(self, time: pd.Timestamp, price: float) -> None:
        fill = price * (1 + self.cfg.slippage_rate)
        budget = self.cash * self.cfg.position_size
        qty = budget / (fill * (1 + self.cfg.fee_rate))
        if qty <= 0:
            return
        self.cash -= qty * fill * (1 + self.cfg.fee_rate)
        self.qty = qty
        self.trade = Trade(entry_time=time, entry_price=fill, qty=qty)

    def sell(self, time: pd.Timestamp, price: float, reason: str, bars_held: int) -> None:
        fill = price * (1 - self.cfg.slippage_rate)
        proceeds = self.qty * fill * (1 - self.cfg.fee_rate)
        t = self.trade
        assert t is not None
        cost = t.qty * t.entry_price * (1 + self.cfg.fee_rate)
        t.exit_time, t.exit_price, t.reason, t.bars_held = time, fill, reason, bars_held
        t.pnl = proceeds - cost
        t.pnl_pct = proceeds / cost - 1
        self.trades.append(t)
        self.cash += proceeds
        self.qty = 0.0
        self.trade = None

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price


def run_backtest(df: pd.DataFrame, strategy: Strategy, cfg: BacktestConfig | None = None,
                 ticker: str = "", interval: str = "day") -> BacktestResult:
    cfg = cfg or BacktestConfig()
    sig = strategy.signals(df)

    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    entry_sig = sig["long_entry"].to_numpy(bool)
    exit_sig = sig["long_exit"].to_numpy(bool)
    entry_px = sig["entry_price"].to_numpy(float)
    idx = df.index

    pf = _Portfolio(cfg)
    equity = np.empty(len(df))
    pending_entry = False
    pending_exit = False
    entry_bar = -1
    start = strategy.warmup

    for i in range(len(df)):
        t = idx[i]
        if i >= start:
            # 1) 직전 봉 종가 시그널 → 이번 봉 시가 체결
            if pending_exit and pf.in_position:
                pf.sell(t, o[i], "signal", i - entry_bar)
            if pending_entry and not pf.in_position:
                pf.buy(t, o[i])
                entry_bar = i
            pending_entry = pending_exit = False

            # 2) 봉 내 돌파 진입
            if not pf.in_position and not math.isnan(entry_px[i]) and h[i] >= entry_px[i]:
                pf.buy(t, max(entry_px[i], o[i]))
                entry_bar = i

            # 3) 손절/익절 (봉 내)
            if pf.in_position and pf.trade is not None:
                ep = pf.trade.entry_price
                if cfg.stop_loss is not None and l[i] <= ep * (1 - cfg.stop_loss):
                    pf.sell(t, min(ep * (1 - cfg.stop_loss), o[i]) if entry_bar != i else ep * (1 - cfg.stop_loss),
                            "stop_loss", i - entry_bar)
                elif cfg.take_profit is not None and h[i] >= ep * (1 + cfg.take_profit):
                    pf.sell(t, max(ep * (1 + cfg.take_profit), o[i]) if entry_bar != i else ep * (1 + cfg.take_profit),
                            "take_profit", i - entry_bar)

            # 4) 이번 봉 종가 시그널 → 다음 봉 예약
            if pf.in_position:
                pending_exit = bool(exit_sig[i])
            else:
                pending_entry = bool(entry_sig[i])

        equity[i] = pf.equity(c[i])

    # 마지막까지 보유 중이면 종가로 평가 청산 (통계용)
    if pf.in_position:
        pf.sell(idx[-1], c[-1], "end", len(df) - 1 - entry_bar)
        equity[-1] = pf.equity(c[-1])

    from .metrics import compute_metrics

    eq = pd.Series(equity, index=idx, name="equity")
    result = BacktestResult(ticker=ticker, interval=interval, strategy=repr(strategy),
                            config=cfg, equity=eq, trades=pf.trades, signals=sig)
    result.metrics = compute_metrics(result, df)
    return result

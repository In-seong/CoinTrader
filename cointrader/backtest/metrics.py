"""성과 지표 계산."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from cointrader.data.upbit_client import bars_per_year

if TYPE_CHECKING:
    from .engine import BacktestResult


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1
    return float(dd.min())


def compute_metrics(result: "BacktestResult", df: pd.DataFrame) -> dict[str, Any]:
    eq = result.equity
    cfg = result.config
    trades = result.trades
    start_eq = cfg.initial_cash
    end_eq = float(eq.iloc[-1])

    days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400, 1e-9)
    years = days / 365
    total_return = end_eq / start_eq - 1
    cagr = (end_eq / start_eq) ** (1 / years) - 1 if years > 0 and end_eq > 0 else float("nan")

    rets = eq.pct_change().dropna()
    bpy = bars_per_year(result.interval) if result.interval else 365
    sharpe = float(rets.mean() / rets.std() * np.sqrt(bpy)) if rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = float(rets.mean() / downside.std() * np.sqrt(bpy)) if len(downside) > 1 and downside.std() > 0 else 0.0

    mdd = max_drawdown(eq)
    bh = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)

    closed = [t for t in trades if not t.is_open]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)

    # 최대 연속 손실
    max_consec_loss = streak = 0
    for t in closed:
        streak = streak + 1 if t.pnl <= 0 else 0
        max_consec_loss = max(max_consec_loss, streak)

    bars_in_market = sum(t.bars_held for t in closed)

    return {
        "start": eq.index[0],
        "end": eq.index[-1],
        "days": round(days, 1),
        "initial_cash": start_eq,
        "final_equity": end_eq,
        "total_return": total_return,
        "cagr": cagr,
        "buy_hold_return": bh,
        "mdd": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": (cagr / abs(mdd)) if mdd < 0 and not np.isnan(cagr) else 0.0,
        "n_trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "avg_win_pct": float(np.mean([t.pnl_pct for t in wins])) if wins else 0.0,
        "avg_loss_pct": float(np.mean([t.pnl_pct for t in losses])) if losses else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        "max_consecutive_losses": max_consec_loss,
        "avg_bars_held": bars_in_market / len(closed) if closed else 0.0,
        "exposure": bars_in_market / len(eq) if len(eq) else 0.0,
    }

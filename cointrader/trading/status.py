"""봇별 모의투자 현황 집계 (data/bots/<name>/ 의 기록을 읽는다)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .portfolio import BOT_DATA_DIR


@dataclass
class BotStatus:
    name: str
    initial_cash: float
    cash: float
    equity: float
    positions: dict[str, dict[str, float]]   # ticker -> {qty, avg_price, price, pnl_pct}
    n_trades: int
    wins: int
    realized_pnl: float
    fees: float
    last_trade: str | None
    started: str | None
    equity_curve: pd.Series = field(default_factory=pd.Series)

    @property
    def total_return(self) -> float:
        return self.equity / self.initial_cash - 1 if self.initial_cash else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.n_trades if self.n_trades else 0.0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect(initial_cash_by_bot: dict[str, float], prices: dict[str, float]) -> list[BotStatus]:
    out: list[BotStatus] = []
    if not BOT_DATA_DIR.exists():
        return out
    for d in sorted(BOT_DATA_DIR.iterdir()):
        acct_file = d / "account.json"
        if not d.is_dir() or not acct_file.exists():
            continue
        acct = json.loads(acct_file.read_text())
        trades = _read_jsonl(d / "trades.jsonl")
        eq_rows = _read_jsonl(d / "equity.jsonl")

        positions: dict[str, dict[str, float]] = {}
        equity = acct["cash"]
        for tk, p in acct.get("positions", {}).items():
            if p["qty"] <= 0:
                continue
            price = prices.get(tk, p["avg_price"])
            equity += p["qty"] * price
            positions[tk] = {"qty": p["qty"], "avg_price": p["avg_price"], "price": price,
                             "pnl_pct": price / p["avg_price"] - 1 if p["avg_price"] else 0.0}

        sells = [t for t in trades if t["side"] == "sell"]
        curve = pd.Series(dtype=float)
        if eq_rows:
            curve = pd.Series([r["equity"] for r in eq_rows],
                              index=pd.to_datetime([r["time"] for r in eq_rows]))
        out.append(BotStatus(
            name=d.name,
            initial_cash=initial_cash_by_bot.get(d.name, eq_rows[0]["equity"] if eq_rows else acct["cash"]),
            cash=acct["cash"], equity=equity, positions=positions,
            n_trades=len(sells), wins=sum(1 for t in sells if t.get("pnl", 0) > 0),
            realized_pnl=sum(t.get("pnl", 0) for t in sells),
            fees=sum(t.get("fee", 0) for t in trades),
            last_trade=trades[-1]["time"] if trades else None,
            started=eq_rows[0]["time"] if eq_rows else None,
            equity_curve=curve,
        ))
    return out


def save_equity_chart(statuses: list[BotStatus], path: Path) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = [(s.name, s.equity_curve / s.initial_cash) for s in statuses if len(s.equity_curve) > 1]
    if not curves:
        return None
    fig, ax = plt.subplots(figsize=(12, 6))
    for name, c in curves:
        ax.plot(c.index, (c - 1) * 100, label=name, lw=1.2)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_ylabel("Return %")
    ax.set_title("Paper trading — bots")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path

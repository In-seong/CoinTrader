"""백테스트 결과 출력(rich 테이블) 및 차트/CSV 저장."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from cointrader.config import REPORT_DIR

from .engine import BacktestResult

console = Console()


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _ratio(x: float) -> str:
    return f"{x * 100:.1f}%"


def _krw(x: float) -> str:
    return f"{x:,.0f}원"


def print_summary(r: BacktestResult) -> None:
    m = r.metrics
    t = Table(title=escape(f"{r.ticker} [{r.interval}]  {r.strategy}"), show_header=False, title_style="bold cyan")
    t.add_column("지표", style="bold")
    t.add_column("값", justify="right")
    t.add_row("기간", f"{m['start']:%Y-%m-%d} ~ {m['end']:%Y-%m-%d} ({m['days']}일)")
    t.add_row("초기/최종 자산", f"{_krw(m['initial_cash'])} → {_krw(m['final_equity'])}")
    t.add_row("총 수익률", _pct(m["total_return"]))
    t.add_row("CAGR", _pct(m["cagr"]))
    t.add_row("Buy & Hold", _pct(m["buy_hold_return"]))
    t.add_row("MDD", _pct(m["mdd"]))
    t.add_row("Sharpe / Sortino / Calmar", f"{m['sharpe']:.2f} / {m['sortino']:.2f} / {m['calmar']:.2f}")
    t.add_row("거래 횟수", f"{m['n_trades']}")
    t.add_row("승률", _ratio(m["win_rate"]))
    t.add_row("평균 수익 / 평균 손실", f"{_pct(m['avg_win_pct'])} / {_pct(m['avg_loss_pct'])}")
    t.add_row("Profit Factor", f"{m['profit_factor']:.2f}")
    t.add_row("최대 연속 손실", f"{m['max_consecutive_losses']}회")
    t.add_row("평균 보유 봉 / 시장 노출", f"{m['avg_bars_held']:.1f} / {_ratio(m['exposure'])}")
    console.print(t)


def print_comparison(results: list[BacktestResult], sort_by: str = "total_return") -> None:
    t = Table(title="전략 비교", title_style="bold cyan")
    t.add_column("전략", style="bold green", no_wrap=True)
    t.add_column("파라미터", overflow="ellipsis", no_wrap=True, max_width=40, style="dim")
    for col in ["수익률", "CAGR", "MDD", "Sharpe", "거래", "승률", "PF", "B&H"]:
        t.add_column(col, justify="right", no_wrap=True)
    for r in sorted(results, key=lambda x: x.metrics[sort_by], reverse=True):
        m = r.metrics
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        name, _, params = r.strategy.partition("(")
        t.add_row(name, escape(params.rstrip(")")), _pct(m["total_return"]), _pct(m["cagr"]), _pct(m["mdd"]),
                  f"{m['sharpe']:.2f}", str(m["n_trades"]), _ratio(m["win_rate"]), pf, _pct(m["buy_hold_return"]))
    console.print(t)


def print_trades(r: BacktestResult, limit: int = 20) -> None:
    trades = r.trades[-limit:]
    if not trades:
        console.print("[yellow]거래 없음[/]")
        return
    t = Table(title=f"최근 거래 {len(trades)}건", title_style="bold cyan")
    for col in ["진입", "진입가", "청산", "청산가", "손익", "수익률", "봉", "사유"]:
        t.add_column(col, justify="right")
    for tr in trades:
        color = "green" if tr.pnl > 0 else "red"
        t.add_row(f"{tr.entry_time:%m-%d %H:%M}", f"{tr.entry_price:,.0f}",
                  f"{tr.exit_time:%m-%d %H:%M}" if tr.exit_time is not None else "-",
                  f"{tr.exit_price:,.0f}" if tr.exit_price else "-",
                  f"[{color}]{tr.pnl:+,.0f}[/]", f"[{color}]{_pct(tr.pnl_pct)}[/]",
                  str(tr.bars_held), tr.reason)
    console.print(t)


def save_report(r: BacktestResult, df: pd.DataFrame, name: str | None = None) -> list[Path]:
    """equity 곡선 PNG + 거래 CSV 저장. 저장된 경로 목록 반환."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = name or f"{r.ticker}_{r.interval}_{r.strategy.split('(')[0]}"
    png = REPORT_DIR / f"{stem}.png"
    csv = REPORT_DIR / f"{stem}_trades.csv"

    eq = r.equity / r.config.initial_cash
    bh = df["close"] / df["close"].iloc[0]
    dd = r.equity / r.equity.cummax() - 1

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(eq.index, eq, label="Strategy", lw=1.5)
    ax1.plot(bh.index, bh, label="Buy & Hold", lw=1, alpha=0.6)
    for tr in r.trades:
        ax1.axvspan(tr.entry_time, tr.exit_time or eq.index[-1],
                    color="green" if tr.pnl > 0 else "red", alpha=0.08)
    ax1.set_title(f"{r.ticker} [{r.interval}] {r.strategy}")
    ax1.set_ylabel("Equity (x)")
    ax1.legend()
    ax1.grid(alpha=0.3)
    ax2.fill_between(dd.index, dd * 100, 0, color="red", alpha=0.4)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(png, dpi=110)
    plt.close(fig)

    r.trades_frame().to_csv(csv, index=False)
    return [png, csv]

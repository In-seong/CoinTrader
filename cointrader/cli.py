"""CoinTrader CLI.

  cointrader strategies                         전략 목록
  cointrader fetch KRW-BTC --interval day       데이터 수집/캐시
  cointrader backtest KRW-BTC -s rsi -p period=10
  cointrader compare KRW-BTC                    모든 전략 기본값 비교
  cointrader optimize KRW-BTC -s volatility_breakout -p k=0.3,0.4,0.5,0.6
  cointrader run -s volatility_breakout --mode paper
"""

from __future__ import annotations

import itertools
import logging
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.table import Table

from cointrader import strategies as S
from cointrader.backtest import BacktestConfig, run_backtest
from cointrader.backtest.report import print_comparison, print_summary, print_trades, save_report
from cointrader.config import settings
from cointrader.data import INTERVALS, load_ohlcv

app = typer.Typer(help="업비트 자동매매 / 백테스트 CLI", no_args_is_help=True, rich_markup_mode="rich")
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s",
                        datefmt="%H:%M:%S", handlers=[RichHandler(rich_tracebacks=True, show_path=False)])


def _parse_params(params: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in params or []:
        if "=" not in item:
            raise typer.BadParameter(f"'{item}' 는 key=value 형식이어야 합니다")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _check_interval(interval: str) -> str:
    if interval not in INTERVALS:
        raise typer.BadParameter(f"interval 은 {list(INTERVALS)} 중 하나")
    return interval


Ticker = Annotated[str, typer.Argument(help="마켓 코드, 예: KRW-BTC")]
Interval = Annotated[str, typer.Option("--interval", "-i", help="봉 간격", callback=_check_interval)]
Count = Annotated[int, typer.Option("--count", "-n", help="조회할 봉 개수")]
StrategyOpt = Annotated[str, typer.Option("--strategy", "-s", help="전략 이름 (strategies 명령으로 확인)")]
Params = Annotated[list[str] | None, typer.Option("--param", "-p", help="전략 파라미터 key=value (반복 가능)")]
Cash = Annotated[float, typer.Option("--cash", help="초기 자본(원)")]
StopLoss = Annotated[float | None, typer.Option("--stop-loss", help="손절 비율, 예: 0.03")]
TakeProfit = Annotated[float | None, typer.Option("--take-profit", help="익절 비율, 예: 0.1")]
PosSize = Annotated[float, typer.Option("--size", help="진입 비중 0~1")]


@app.command()
def strategies() -> None:
    """사용 가능한 전략과 기본 파라미터."""
    t = Table(title="전략 목록", title_style="bold cyan")
    t.add_column("이름", style="bold green")
    t.add_column("설명")
    t.add_column("기본 파라미터")
    for name, cls in sorted(S.available().items()):
        t.add_row(name, cls.description, ", ".join(f"{k}={v}" for k, v in cls.params.items()))
    console.print(t)


@app.command()
def fetch(ticker: Ticker = settings.default_ticker, interval: Interval = settings.default_interval,
          count: Count = 1000) -> None:
    """업비트에서 OHLCV 를 받아 data/cache 에 저장."""
    with console.status(f"{ticker} {interval} {count}봉 수집 중..."):
        df = load_ohlcv(ticker, interval, count)
    console.print(f"[green]✓[/] {len(df)}봉  {df.index[0]} ~ {df.index[-1]}")
    console.print(df.tail())


@app.command("import-csv")
def import_csv(path: Annotated[str, typer.Argument(help="CSV 경로 (time,open,high,low,close,volume[,value])")],
               ticker: Ticker = settings.default_ticker, interval: Interval = settings.default_interval) -> None:
    """외부 CSV 를 캐시에 넣는다. 거래소 API 가 막힌 환경에서 백테스트용."""
    import pandas as pd

    from cointrader.config import CACHE_DIR
    from cointrader.data.upbit_client import COLUMNS

    df = pd.read_csv(path)
    tcol = next((c for c in df.columns if c.lower() in ("time", "timestamp", "date", "datetime")), df.columns[0])
    df[tcol] = pd.to_datetime(df[tcol])
    df = df.set_index(tcol).sort_index()
    df.index.name = "time"
    df.columns = [c.lower() for c in df.columns]
    if "value" not in df.columns:
        df["value"] = df["close"] * df.get("volume", 0)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise typer.BadParameter(f"CSV 에 컬럼이 없습니다: {missing}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{ticker}_{interval}.parquet"
    df[COLUMNS].to_parquet(out)
    console.print(f"[green]✓[/] {len(df)}봉 → {out}  (--no-refresh 로 사용)")


@app.command()
def backtest(ticker: Ticker = settings.default_ticker, strategy: StrategyOpt = "volatility_breakout",
             interval: Interval = settings.default_interval, count: Count = 1000,
             param: Params = None, cash: Cash = 1_000_000, stop_loss: StopLoss = None,
             take_profit: TakeProfit = None, size: PosSize = 1.0,
             trades: Annotated[int, typer.Option(help="출력할 최근 거래 수")] = 15,
             plot: Annotated[bool, typer.Option(help="equity 차트 PNG + 거래 CSV 저장")] = True,
             refresh: Annotated[bool, typer.Option(help="캐시 대신 최신 데이터 갱신")] = True) -> None:
    """단일 전략 백테스트."""
    strat = S.get(strategy, **_parse_params(param))
    cfg = BacktestConfig(initial_cash=cash, fee_rate=settings.fee_rate, slippage_rate=settings.slippage_rate,
                         position_size=size, stop_loss=stop_loss, take_profit=take_profit)
    with console.status("데이터 로드 중..."):
        df = load_ohlcv(ticker, interval, count, refresh=refresh)
    r = run_backtest(df, strat, cfg, ticker=ticker, interval=interval)
    print_summary(r)
    if trades:
        print_trades(r, trades)
    if plot:
        for p in save_report(r, df):
            console.print(f"[dim]saved → {p}[/]")


@app.command()
def compare(ticker: Ticker = settings.default_ticker, interval: Interval = settings.default_interval,
            count: Count = 1000, cash: Cash = 1_000_000, stop_loss: StopLoss = None,
            take_profit: TakeProfit = None,
            sort: Annotated[str, typer.Option(help="정렬 기준 metric")] = "total_return",
            refresh: Annotated[bool, typer.Option(help="캐시 대신 최신 데이터 갱신")] = True) -> None:
    """모든 전략을 기본 파라미터로 돌려 비교."""
    cfg = BacktestConfig(initial_cash=cash, fee_rate=settings.fee_rate, slippage_rate=settings.slippage_rate,
                         stop_loss=stop_loss, take_profit=take_profit)
    with console.status("데이터 로드 중..."):
        df = load_ohlcv(ticker, interval, count, refresh=refresh)
    results = [run_backtest(df, cls(), cfg, ticker=ticker, interval=interval) for cls in S.available().values()]
    print_comparison(results, sort_by=sort)


@app.command()
def optimize(ticker: Ticker = settings.default_ticker, strategy: StrategyOpt = "volatility_breakout",
             interval: Interval = settings.default_interval, count: Count = 1000,
             param: Annotated[list[str] | None, typer.Option("--param", "-p",
                              help="그리드: key=v1,v2,v3 (반복 가능)")] = None,
             cash: Cash = 1_000_000, stop_loss: StopLoss = None, take_profit: TakeProfit = None,
             sort: Annotated[str, typer.Option(help="정렬 기준 metric")] = "sharpe",
             top: Annotated[int, typer.Option(help="상위 N개 출력")] = 15,
             refresh: Annotated[bool, typer.Option(help="캐시 대신 최신 데이터 갱신")] = True) -> None:
    """파라미터 그리드 서치."""
    grid: dict[str, list[str]] = {k: v.split(",") for k, v in _parse_params(param).items()}
    if not grid:
        raise typer.BadParameter("-p key=v1,v2,... 형태로 탐색할 파라미터를 지정하세요")
    cfg = BacktestConfig(initial_cash=cash, fee_rate=settings.fee_rate, slippage_rate=settings.slippage_rate,
                         stop_loss=stop_loss, take_profit=take_profit)
    with console.status("데이터 로드 중..."):
        df = load_ohlcv(ticker, interval, count, refresh=refresh)

    keys = list(grid)
    combos = list(itertools.product(*grid.values()))
    results = []
    with typer.progressbar(combos, label=f"{len(combos)}개 조합 탐색") as bar:
        for combo in bar:
            strat = S.get(strategy, **dict(zip(keys, combo)))
            results.append(run_backtest(df, strat, cfg, ticker=ticker, interval=interval))
    results.sort(key=lambda r: r.metrics[sort], reverse=True)
    print_comparison(results[:top], sort_by=sort)
    console.print("\n[dim]※ 그리드 서치 결과는 과최적화 가능성이 큽니다. 다른 기간/코인으로 검증하세요.[/]")


@app.command()
def run(strategy: StrategyOpt = "volatility_breakout", ticker: Annotated[str, typer.Option("--ticker", "-t")] = settings.default_ticker,
        interval: Interval = settings.default_interval, param: Params = None,
        mode: Annotated[str, typer.Option(help="paper | live")] = "paper",
        poll: Annotated[int, typer.Option(help="폴링 주기(초)")] = 60,
        size: PosSize = 1.0, stop_loss: StopLoss = None, take_profit: TakeProfit = None,
        cash: Annotated[float, typer.Option(help="paper 모드 초기 자본")] = 1_000_000,
        once: Annotated[bool, typer.Option(help="한 사이클만 실행 후 종료 (테스트용)")] = False,
        verbose: Annotated[bool, typer.Option("-v")] = False) -> None:
    """실시간 매매 루프 (paper=모의, live=실거래)."""
    from cointrader.trading import LiveRunner, PaperBroker, RunnerConfig, UpbitBroker

    _setup_logging(verbose)
    strat = S.get(strategy, **_parse_params(param))
    if mode == "live":
        if not settings.has_upbit_keys:
            console.print("[red].env 에 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 를 설정하세요[/]")
            raise typer.Exit(1)
        console.print("[bold red]⚠ 실거래 모드입니다. 실제 주문이 나갑니다.[/]")
        if not once and not typer.confirm("계속할까요?"):
            raise typer.Exit()
        broker = UpbitBroker()
    elif mode == "paper":
        broker = PaperBroker(initial_cash=cash)
    else:
        raise typer.BadParameter("mode 는 paper 또는 live")

    cfg = RunnerConfig(ticker=ticker, interval=interval, poll_seconds=poll, position_size=size,
                       stop_loss=stop_loss, take_profit=take_profit)
    console.print(f"[cyan]{mode.upper()}[/] {ticker} [{interval}] {strat!r}  poll={poll}s  "
                  f"KRW={broker.get_krw_balance():,.0f}")
    runner = LiveRunner(strat, broker, cfg)
    try:
        runner.run(once=once)
    except KeyboardInterrupt:
        console.print("\n[yellow]중지됨[/]")


@app.command("paper-reset")
def paper_reset(cash: Annotated[float, typer.Option(help="초기 자본")] = 1_000_000) -> None:
    """모의 계좌 초기화."""
    from cointrader.trading import PaperBroker

    PaperBroker(initial_cash=cash).reset(cash)
    console.print(f"[green]✓[/] 모의 계좌 초기화: {cash:,.0f}원")


@app.command("paper-status")
def paper_status() -> None:
    """모의 계좌 잔고/포지션."""
    from cointrader.trading import PaperBroker

    b = PaperBroker()
    console.print(f"KRW: {b.cash:,.0f}원")
    total = b.cash
    for tk, pos in b.positions.items():
        if pos.is_open:
            price = b.get_price(tk)
            val = pos.qty * price
            total += val
            console.print(f"{tk}: {pos.qty:.6f} @ {pos.avg_price:,.0f} → 현재 {price:,.0f} "
                          f"({(price / pos.avg_price - 1) * 100:+.2f}%) 평가 {val:,.0f}원")
    console.print(f"[bold]총 평가: {total:,.0f}원[/]")


@app.command()
def bots(config: Annotated[str, typer.Option("--config", "-c", help="bots.yaml 경로")] = "bots.yaml",
         once: Annotated[bool, typer.Option(help="한 사이클만 실행 (테스트용)")] = False,
         verbose: Annotated[bool, typer.Option("-v")] = False) -> None:
    """bots.yaml 의 모든 봇을 모의투자로 동시에 돌린다."""
    from pathlib import Path

    from cointrader.trading import MultiRunner, load_bot_configs

    _setup_logging(verbose)
    cfgs, meta = load_bot_configs(Path(config))
    t = Table(title=f"봇 {len(cfgs)}개", title_style="bold cyan")
    for col in ["이름", "전략", "파라미터", "봉", "코인", "최대보유", "손절/익절", "자본"]:
        t.add_column(col)
    for b in cfgs:
        if not b.enabled:
            continue
        t.add_row(b.name, b.strategy, ", ".join(f"{k}={v}" for k, v in b.params.items()) or "-", b.interval,
                  ",".join(tk.replace("KRW-", "") for tk in b.tickers), str(b.max_positions),
                  f"{b.stop_loss or '-'} / {b.take_profit or '-'}", f"{b.cash:,.0f}")
    console.print(t)
    runner = MultiRunner(cfgs, poll_seconds=int(meta["poll"]))
    try:
        runner.run(once=once)
    except KeyboardInterrupt:
        console.print("\n[yellow]중지됨[/]")


@app.command("bots-status")
def bots_status(config: Annotated[str, typer.Option("--config", "-c")] = "bots.yaml",
                chart: Annotated[bool, typer.Option(help="reports/bots_equity.png 저장")] = True) -> None:
    """봇별 모의투자 성과 비교."""
    from pathlib import Path

    import pyupbit

    from cointrader.config import REPORT_DIR
    from cointrader.trading import load_bot_configs
    from cointrader.trading.status import collect, save_equity_chart

    cfgs, meta = load_bot_configs(Path(config))
    tickers = sorted({tk for b in cfgs for tk in b.tickers})
    raw = pyupbit.get_current_price(tickers) or {}
    prices = {tickers[0]: float(raw)} if isinstance(raw, (int, float)) else {k: float(v) for k, v in raw.items()}
    statuses = collect({b.name: b.cash for b in cfgs}, prices)
    if not statuses:
        console.print("[yellow]기록이 없습니다. 먼저 `cointrader bots` 를 실행하세요.[/]")
        return

    t = Table(title="모의투자 현황", title_style="bold cyan")
    for col in ["봇", "수익률", "평가자산", "실현손익", "수수료", "거래", "승률", "보유", "마지막 거래"]:
        t.add_column(col, justify="right" if col not in ("봇", "보유") else "left")
    for s in sorted(statuses, key=lambda x: x.total_return, reverse=True):
        color = "green" if s.total_return > 0 else ("red" if s.total_return < 0 else "white")
        held = ", ".join(f"{tk.replace('KRW-', '')}({p['pnl_pct'] * 100:+.1f}%)" for tk, p in s.positions.items()) or "-"
        t.add_row(s.name, f"[{color}]{s.total_return * 100:+.2f}%[/]", f"{s.equity:,.0f}",
                  f"{s.realized_pnl:+,.0f}", f"{s.fees:,.0f}", str(s.n_trades),
                  f"{s.win_rate * 100:.0f}%" if s.n_trades else "-", held,
                  (s.last_trade or "-")[:16])
    console.print(t)
    if chart:
        p = save_equity_chart(statuses, REPORT_DIR / "bots_equity.png")
        if p:
            console.print(f"[dim]chart → {p}[/]")


@app.command("bots-why")
def bots_why(config: Annotated[str, typer.Option("--config", "-c")] = "bots.yaml",
             hours: Annotated[int, typer.Option(help="최근 N시간 내 진입 시그널 횟수")] = 24) -> None:
    """봇별로 지금 지표값이 진입 조건에서 얼마나 떨어져 있는지, 최근 시그널이 몇 번 떴는지."""
    from pathlib import Path

    import pandas as pd

    from cointrader.trading import load_bot_configs

    cfgs, _ = load_bot_configs(Path(config))
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    t = Table(title=f"왜 거래가 없나 — 최근 {hours}시간", title_style="bold cyan")
    for col in ["봇", "코인", "봉", f"{hours}h 진입시그널", "마지막 시그널", "현재 지표 / 조건"]:
        t.add_column(col, overflow="fold")
    for b in cfgs:
        if not b.enabled:
            continue
        strat = b.build_strategy()
        for tk in b.tickers:
            key = (tk, b.interval)
            if key not in cache:
                cache[key] = load_ohlcv(tk, b.interval, count=max(200, strat.warmup + 50))
            df = cache[key]
            sig = strat.signals(df)
            since = df.index[-1] - pd.Timedelta(hours=hours)
            recent = sig.loc[sig.index >= since]
            fired = recent["long_entry"] | (recent["entry_price"].notna() & (df.loc[recent.index, "high"] >= recent["entry_price"]))
            any_fired = sig["long_entry"] | (sig["entry_price"].notna() & (df["high"] >= sig["entry_price"]))
            last = str(sig.index[any_fired][-1])[:16] if any_fired.any() else "없음(조회범위 내)"
            snap = ", ".join(f"{k} {v}" for k, v in strat.snapshot(df).items())
            n = int(fired.sum())
            t.add_row(b.name, tk.replace("KRW-", ""), b.interval, f"[{'green' if n else 'dim'}]{n}[/]", last, escape(snap))
    console.print(t)


@app.command()
def simulate(config: Annotated[str, typer.Option("--config", "-c")] = "bots.yaml",
             days: Annotated[int, typer.Option(help="최근 N일")] = 365,
             bot: Annotated[list[str] | None, typer.Option("--bot", "-b", help="특정 봇만 (반복 가능)")] = None,
             minute1_days: Annotated[int, typer.Option(help="1분봉 봇은 데이터량 때문에 이 일수로 제한")] = 90,
             sort: Annotated[str, typer.Option(help="정렬 기준 metric")] = "total_return",
             chart: Annotated[bool, typer.Option(help="reports/simulate_<days>d.png 저장")] = True) -> None:
    """bots.yaml 의 봇 세트를 과거 N일 데이터로 그대로 시뮬레이션 (멀티코인·슬롯·손절 규칙 동일)."""
    from pathlib import Path

    import pandas as pd

    from cointrader.backtest.multi_engine import MultiConfig, run_multi_backtest
    from cointrader.config import REPORT_DIR
    from cointrader.trading import load_bot_configs

    cfgs, _ = load_bot_configs(Path(config))
    if bot:
        cfgs = [c for c in cfgs if c.name in set(bot)]
    cache: dict[tuple[str, str, int], pd.DataFrame] = {}
    results = []
    for b in cfgs:
        if not b.enabled:
            continue
        strat = b.build_strategy()
        d = min(days, minute1_days) if b.interval == "minute1" else days
        bars = int(d * 1440 / INTERVALS[b.interval]) + strat.warmup + 5
        dfs = {}
        try:
            with console.status(f"[{b.name}] {b.interval} {len(b.tickers)}개 코인 × {bars:,}봉 로드..."):
                for tk in b.tickers:
                    key = (tk, b.interval, bars)
                    if key not in cache:
                        cache[key] = load_ohlcv(tk, b.interval, count=bars)
                    dfs[tk] = cache[key]
            mc = MultiConfig(initial_cash=b.cash, fee_rate=settings.fee_rate, slippage_rate=settings.slippage_rate,
                             max_positions=b.max_positions, position_size=b.position_size,
                             stop_loss=b.stop_loss, take_profit=b.take_profit)
            r = run_multi_backtest(dfs, strat, mc, interval=b.interval, name=b.name)
        except Exception as e:  # 한 봇 실패해도 나머지는 계속
            console.print(f"  [red]{b.name}: 실패 — {e}[/]")
            continue
        r.meta = {"days": d, "strategy": repr(strat), "interval": b.interval}  # type: ignore[attr-defined]
        results.append(r)
        console.print(f"  [dim]{b.name}: {r.metrics['total_return'] * 100:+.2f}%  거래 {r.metrics['n_trades']}[/]")

    results.sort(key=lambda r: r.metrics[sort], reverse=True)
    t = Table(title=f"봇 세트 시뮬레이션 — 최근 {days}일 (수수료·슬리피지 반영)", title_style="bold cyan")
    for col in ["봇", "전략", "봉", "일수", "수익률", "CAGR", "MDD", "Sharpe", "거래", "승률", "PF", "수수료", "B&H(동일가중)"]:
        t.add_column(col, justify="right" if col not in ("봇", "전략", "봉") else "left", no_wrap=col != "전략",
                     overflow="ellipsis", max_width=38 if col == "전략" else None)
    for r in results:
        m = r.metrics
        color = "red" if m["total_return"] > 0 else "blue"
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        t.add_row(r.strategy, escape(r.meta["strategy"]), r.meta["interval"], str(r.meta["days"]),
                  f"[{color}]{m['total_return'] * 100:+.2f}%[/]", f"{m['cagr'] * 100:+.1f}%", f"{m['mdd'] * 100:.1f}%",
                  f"{m['sharpe']:.2f}", str(m["n_trades"]), f"{m['win_rate'] * 100:.0f}%" if m["n_trades"] else "-", pf,
                  f"{r.fees:,.0f}", f"{m['buy_hold_return'] * 100:+.1f}%")
    console.print(t)
    console.print("[dim]※ 수익률 빨강=이익, 파랑=손실. 1분봉 봇은 데이터량 때문에 일수가 짧음. 과거 성과는 미래를 보장하지 않음.[/]")

    if chart and results:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(13, 7))
        for r in results:
            eq = r.equity / r.config.initial_cash * 100 - 100
            ax.plot(eq.index, eq, lw=1.2, label=f"{r.strategy} ({r.metrics['total_return'] * 100:+.1f}%)")
        ax.axhline(0, color="gray", lw=.8)
        ax.set_ylabel("Return %"); ax.set_title(f"Bot set simulation — last {days} days"); ax.grid(alpha=.3)
        ax.legend(fontsize=7, ncol=2)
        fig.tight_layout()
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"simulate_{days}d.png"
        fig.savefig(out, dpi=110); plt.close(fig)
        console.print(f"[dim]chart → {out}[/]")


@app.command("bots-reset")
def bots_reset(name: Annotated[str | None, typer.Argument(help="봇 이름 (생략 시 전체)")] = None,
               yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    """봇 기록(계좌·거래·자산곡선) 삭제 후 처음부터."""
    import shutil

    from cointrader.trading.portfolio import BOT_DATA_DIR

    targets = [BOT_DATA_DIR / name] if name else (list(BOT_DATA_DIR.iterdir()) if BOT_DATA_DIR.exists() else [])
    targets = [t for t in targets if t.is_dir()]
    if not targets:
        console.print("[yellow]삭제할 기록이 없습니다[/]")
        return
    if not yes and not typer.confirm(f"{[t.name for t in targets]} 기록을 삭제할까요?"):
        raise typer.Exit()
    for t in targets:
        shutil.rmtree(t)
    console.print(f"[green]✓[/] {len(targets)}개 봇 초기화")


@app.command()
def report(config: Annotated[str, typer.Option("--config", "-c")] = "bots.yaml",
           hours: Annotated[int, typer.Option(help="비교 구간(시간)")] = 24,
           send: Annotated[bool, typer.Option(help="텔레그램 전송 (미설정 시 자동 스킵)")] = True) -> None:
    """일일 리포트: 봇 순위·최근 24h 변화·거래·시장(B&H) 대비. cron 으로 매일 실행."""
    import json as _json
    from datetime import datetime, timedelta
    from pathlib import Path

    import pyupbit

    from cointrader.notify import notify
    from cointrader.trading import load_bot_configs
    from cointrader.trading.portfolio import BOT_DATA_DIR
    from cointrader.trading.status import collect

    cfgs, meta = load_bot_configs(Path(config))
    tickers = sorted({tk for b in cfgs for tk in b.tickers})
    raw = pyupbit.get_current_price(tickers) or {}
    prices = {tickers[0]: float(raw)} if isinstance(raw, (int, float)) else {k: float(v) for k, v in raw.items()}
    statuses = collect({b.name: b.cash for b in cfgs}, prices)
    cutoff = datetime.now() - timedelta(hours=hours)

    # 시장 기준: 유니버스 동일가중 24h 변화
    market = []
    for tk in meta.get("universe", []):
        try:
            df = load_ohlcv(tk, "minute60", 30, refresh=True)
            past = df[df.index <= cutoff]
            if len(past) and tk in prices:
                market.append(prices[tk] / float(past["close"].iloc[-1]) - 1)
        except Exception:
            pass
    bh = sum(market) / len(market) if market else None

    rows = []
    for s in statuses:
        day = None
        curve = s.equity_curve
        if len(curve):
            past = curve[curve.index <= cutoff]
            if len(past) and past.iloc[-1] > 0:
                day = s.equity / float(past.iloc[-1]) - 1
        trades24 = 0
        pnl24 = 0.0
        tf = BOT_DATA_DIR / s.name / "trades.jsonl"
        if tf.exists():
            for line in tf.read_text().splitlines():
                r = _json.loads(line)
                if r["time"] >= cutoff.isoformat(timespec="seconds"):
                    trades24 += 1
                    pnl24 += r.get("pnl", 0) or 0
        rows.append((s, day, trades24, pnl24))
    rows.sort(key=lambda r: (r[1] if r[1] is not None else -9), reverse=True)

    live_cfgs = {b.name for b in cfgs if b.mode == "live"}
    lines = [f"📊 <b>CoinTrader 일일 리포트</b> {datetime.now():%m-%d %H:%M}",
             f"시장(동일가중 {hours}h): {bh * 100:+.2f}%" if bh is not None else "시장: 조회 실패", ""]
    for s, day, t24, pnl24 in rows:
        tag = "🔴" if s.name in live_cfgs else ""
        d = f"{day * 100:+.2f}%" if day is not None else "  n/a "
        star = ""
        if day is not None and bh is not None:
            star = " ⭐" if day > bh else ""
        lines.append(f"{tag}{s.name}: {hours}h {d} · 누적 {s.total_return * 100:+.2f}% · 거래 {t24}건"
                     + (f" ({pnl24:+,.0f}원)" if t24 else "") + star)
    lines.append("")
    lines.append("⭐=시장 초과 · https://coin.revuplan.com")
    text = "\n".join(lines)
    console.print(text.replace("<b>", "").replace("</b>", ""))
    if send:
        ok = notify(text, silent=True)
        console.print(f"[dim]telegram: {'sent' if ok else 'skipped(미설정)'}[/]")


@app.command("notify-test")
def notify_test(text: str = "CoinTrader 텔레그램 테스트 ✅") -> None:
    """텔레그램 설정 확인."""
    from cointrader.notify import notify

    ok = notify(text)
    console.print("[green]전송 성공[/]" if ok else "[yellow]전송 실패 또는 미설정 (.env 확인)[/]")


if __name__ == "__main__":
    app()

import numpy as np
import pandas as pd
import pytest

from cointrader import strategies as S
from cointrader.strategies.ensemble import signals_to_state
from cointrader.trading.broker import PaperBroker
from cointrader.trading.portfolio import BotConfig, PortfolioBot


# ---------- ensemble ----------

def test_signals_to_state_tracks_entry_exit():
    idx = pd.date_range("2024-01-01", periods=6, freq="h")
    sig = pd.DataFrame({"long_entry": [False, True, False, False, False, True],
                        "long_exit": [False, False, False, True, False, False],
                        "entry_price": np.nan}, index=idx)
    state = signals_to_state(sig, pd.Series(100.0, index=idx))
    assert state.tolist() == [False, True, True, False, False, True]


def test_ensemble_modes(ohlcv):
    subs = [S.get("ma_cross"), S.get("rsi")]
    states = pd.concat([signals_to_state(s.signals(ohlcv), ohlcv["high"]) for s in subs], axis=1)
    for mode, expect in (("all", states.all(axis=1)), ("any", states.any(axis=1)),
                         ("majority", states.sum(axis=1) > 1)):
        ens = S.get("ensemble", strategies="ma_cross,rsi", mode=mode)
        sig = ens.signals(ohlcv)
        got = signals_to_state(sig, ohlcv["high"])
        pd.testing.assert_series_equal(got, expect, check_names=False)


def test_ensemble_sub_params_and_errors():
    ens = S.get("ensemble", strategies=["ma_cross"], mode="all", sub_params={"ma_cross": {"fast": 3}})
    assert ens.subs[0].p["fast"] == 3
    with pytest.raises(ValueError):
        S.get("ensemble", strategies="ma_cross", mode="nope")


# ---------- portfolio bot ----------

class FakeBroker(PaperBroker):
    """네트워크 없이 price_cache 만 쓰는 모의 브로커."""

    def __init__(self, cash, tmp):
        super().__init__(initial_cash=cash, state_file=tmp / "acct.json", fee_rate=0.0, slippage_rate=0.0)

    def get_price(self, ticker):
        return self.price_cache[ticker]


def _df(prices, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="h")
    p = np.array(prices, float)
    return pd.DataFrame({"open": p, "high": p * 1.01, "low": p * 0.99, "close": p,
                         "volume": 1.0, "value": p}, index=idx)


class EntryOnLastCompleted(S.Strategy):
    name = "_test_entry"
    params = {}

    def generate_signals(self, df):
        e = pd.Series(False, index=df.index)
        e.iloc[-2] = True  # 직전 완성 봉에 진입 시그널
        return pd.DataFrame({"long_entry": e, "long_exit": False})


def test_multi_ticker_respects_max_positions(tmp_path, monkeypatch):
    monkeypatch.setattr("cointrader.trading.portfolio.BOT_DATA_DIR", tmp_path)
    monkeypatch.setattr("cointrader.trading.portfolio.notify", lambda *a, **k: False)
    S._REGISTRY[EntryOnLastCompleted.name] = EntryOnLastCompleted

    cfg = BotConfig(name="t", strategy="_test_entry", tickers=["A", "B", "C"], cash=900_000, max_positions=2)
    broker = FakeBroker(900_000, tmp_path)
    bot = PortfolioBot(cfg, broker=broker)
    prices = {"A": 100.0, "B": 200.0, "C": 300.0}
    broker.price_cache = prices
    dfs = {tk: _df([100] * 10) for tk in prices}

    actions = bot.step(dfs, prices)
    assert actions == ["buy A", "buy B"]           # C 는 슬롯 부족으로 제외
    assert broker.get_krw_balance() == pytest.approx(0)
    assert broker.get_position("A").qty == pytest.approx(450_000 / 100)
    assert broker.get_position("B").qty == pytest.approx(450_000 / 200)

    # 같은 봉에서 다시 step → new_bar 아님 → 추가 매수 없음
    assert bot.step(dfs, prices) == []

    # 손절: A 가 -5% 로 떨어지면 stop_loss 0.03 봇이면 매도 (여기선 설정 없음 → 유지)
    prices2 = {**prices, "A": 95.0}
    broker.price_cache = prices2
    assert bot.step(dfs, prices2) == []

    trades = (tmp_path / "t" / "trades.jsonl").read_text().splitlines()
    assert len(trades) == 2
    equity = (tmp_path / "t" / "equity.jsonl").read_text().splitlines()
    assert len(equity) == 3


def test_stop_loss_sells(tmp_path, monkeypatch):
    monkeypatch.setattr("cointrader.trading.portfolio.BOT_DATA_DIR", tmp_path)
    monkeypatch.setattr("cointrader.trading.portfolio.notify", lambda *a, **k: False)
    S._REGISTRY[EntryOnLastCompleted.name] = EntryOnLastCompleted

    cfg = BotConfig(name="s", strategy="_test_entry", tickers=["A"], cash=100_000, stop_loss=0.03)
    broker = FakeBroker(100_000, tmp_path)
    bot = PortfolioBot(cfg, broker=broker)
    broker.price_cache = {"A": 100.0}
    dfs = {"A": _df([100] * 10)}
    assert bot.step(dfs, {"A": 100.0}) == ["buy A"]
    broker.price_cache = {"A": 96.0}
    assert bot.step(dfs, {"A": 96.0}) == ["stop A"]
    assert broker.get_krw_balance() == pytest.approx(96_000)
    last = (tmp_path / "s" / "trades.jsonl").read_text().splitlines()[-1]
    assert '"reason": "stop_loss"' in last and '"pnl_pct": -0.04' in last

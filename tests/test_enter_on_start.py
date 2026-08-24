"""봇 시작 시 전략이 이미 보유 구간이면 진입하는지 (추세 전략이 크로스를 놓친 경우)."""

import pandas as pd

from cointrader import strategies as S
from cointrader.trading.broker import PaperBroker
from cointrader.trading.portfolio import BotConfig, PortfolioBot


class CrossedLongAgo(S.Strategy):
    """조회 범위 한가운데서 진입 시그널이 한 번 떴고 그 뒤로 청산 없음 → 현재 '보유 구간'."""
    name = "_test_crossed"
    params = {}

    def generate_signals(self, df):
        e = pd.Series(False, index=df.index)
        e.iloc[len(df) // 2] = True
        return pd.DataFrame({"long_entry": e, "long_exit": False})


def _bot(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr("cointrader.trading.portfolio.BOT_DATA_DIR", tmp_path)
    monkeypatch.setattr("cointrader.trading.portfolio.notify", lambda *a, **k: False)
    S._REGISTRY[CrossedLongAgo.name] = CrossedLongAgo
    broker = PaperBroker(initial_cash=100_000, state_file=tmp_path / "a.json", fee_rate=0, slippage_rate=0)
    broker.get_price = lambda tk: 100.0
    return PortfolioBot(BotConfig(name="c", strategy="_test_crossed", tickers=["A"], cash=100_000, **kw), broker=broker)


def _df():
    idx = pd.date_range("2024-01-01", periods=30, freq="h")
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0, "value": 1.0}, index=idx)


def test_enters_on_start_when_already_long(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch)
    assert bot.step({"A": _df()}, {"A": 100.0}) == ["buy A"]
    assert "start_in_position" in (tmp_path / "c" / "trades.jsonl").read_text()
    # 두 번째 사이클에서는 first_seen 이 아니므로 중복 진입 없음
    assert bot.step({"A": _df()}, {"A": 100.0}) == []


def test_enter_on_start_can_be_disabled(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch, enter_on_start=False)
    assert bot.step({"A": _df()}, {"A": 100.0}) == []

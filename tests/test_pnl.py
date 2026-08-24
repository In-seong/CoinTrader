"""매도 손익이 매수·매도 양쪽 수수료를 모두 반영하는지."""

import json

import pandas as pd
import pytest

from cointrader import strategies as S
from cointrader.trading.broker import PaperBroker
from cointrader.trading.portfolio import BotConfig, PortfolioBot


class ExitNext(S.Strategy):
    name = "_test_exit_next"
    params = {}

    def generate_signals(self, df):
        # 특정 봉(08:00)에서만 진입 → 첫 step 에서 매수, 다음 step 에서 매도 후 재매수 없음
        e = pd.Series(df.index == pd.Timestamp("2024-01-01 08:00"), index=df.index)
        return pd.DataFrame({"long_entry": e, "long_exit": True})


def test_roundtrip_pnl_includes_both_fees(tmp_path, monkeypatch):
    monkeypatch.setattr("cointrader.trading.portfolio.BOT_DATA_DIR", tmp_path)
    monkeypatch.setattr("cointrader.trading.portfolio.notify", lambda *a, **k: False)
    S._REGISTRY[ExitNext.name] = ExitNext

    broker = PaperBroker(initial_cash=100_000, state_file=tmp_path / "a.json", fee_rate=0.001, slippage_rate=0.0)
    broker.get_price = lambda tk: broker.price_cache[tk]
    bot = PortfolioBot(BotConfig(name="p", strategy="_test_exit_next", tickers=["A"], cash=100_000), broker=broker)

    idx = pd.date_range("2024-01-01", periods=10, freq="h")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0, "value": 1.0}, index=idx)
    broker.price_cache = {"A": 100.0}
    assert bot.step({"A": df}, {"A": 100.0}) == ["buy A"]

    # 새 봉 → 직전 봉 long_exit → 같은 가격에 매도. 가격 변동 0 이어도 수수료 0.1%×2 만큼 손실이어야 한다
    df2 = df.shift(freq="h")
    assert bot.step({"A": df2}, {"A": 100.0}) == ["sell A"]
    sell = json.loads((tmp_path / "p" / "trades.jsonl").read_text().splitlines()[-1])
    assert sell["pnl"] < 0
    assert sell["pnl_pct"] == pytest.approx(0.999 * 0.999 - 1, rel=1e-6)
    assert broker.get_krw_balance() == pytest.approx(100_000 * 0.999 * 0.999)

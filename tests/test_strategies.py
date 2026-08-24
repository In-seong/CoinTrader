import numpy as np
import pandas as pd
import pytest

from cointrader import strategies as S
from cointrader.strategies.indicators import rsi, sma


def test_registry_has_builtins():
    names = set(S.available())
    assert {"volatility_breakout", "rsi", "ma_cross", "bollinger"} <= names


@pytest.mark.parametrize("name", sorted(S.available()))
def test_signal_contract(name, ohlcv):
    strat = S.get(name)
    sig = strat.signals(ohlcv)
    assert list(sig.columns) == ["long_entry", "long_exit", "entry_price"]
    assert len(sig) == len(ohlcv)
    assert sig["long_entry"].dtype == bool and sig["long_exit"].dtype == bool
    assert sig["entry_price"].dtype == float


def test_param_override_and_casting():
    s = S.get("volatility_breakout", k="0.7", ma_filter="5")
    assert s.p["k"] == 0.7 and isinstance(s.p["ma_filter"], int) and s.p["ma_filter"] == 5
    with pytest.raises(ValueError):
        S.get("rsi", nope=1)
    with pytest.raises(KeyError):
        S.get("does_not_exist")


def test_volatility_breakout_target(ohlcv):
    s = S.get("volatility_breakout", k=0.5)
    sig = s.signals(ohlcv)
    i = 10
    expected = ohlcv["open"].iloc[i] + (ohlcv["high"].iloc[i - 1] - ohlcv["low"].iloc[i - 1]) * 0.5
    assert sig["entry_price"].iloc[i] == pytest.approx(expected)
    assert np.isnan(sig["entry_price"].iloc[0])
    assert sig["long_exit"].all()


def test_rsi_bounds(ohlcv):
    r = rsi(ohlcv["close"], 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_ma_cross_only_fires_on_cross(ohlcv):
    sig = S.get("ma_cross", fast=3, slow=10).signals(ohlcv)
    fast, slow = sma(ohlcv["close"], 3), sma(ohlcv["close"], 10)
    above = (fast > slow)
    crosses_up = above & ~above.shift(1, fill_value=False)
    pd.testing.assert_series_equal(sig["long_entry"], crosses_up, check_names=False)

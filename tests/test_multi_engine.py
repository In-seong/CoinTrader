import numpy as np
import pandas as pd
import pytest

from cointrader import strategies as S
from cointrader.backtest.multi_engine import MultiConfig, run_multi_backtest
from cointrader.strategies.base import Strategy


def _df(prices, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="D")
    p = np.array(prices, float)
    return pd.DataFrame({"open": p, "high": p * 1.01, "low": p * 0.99, "close": p,
                         "volume": 1.0, "value": p}, index=idx)


class EnterAt2ExitAt4(Strategy):
    name = "_m_fixed"
    params = {}

    def generate_signals(self, df):
        e = pd.Series(False, index=df.index); x = pd.Series(False, index=df.index)
        e.iloc[2] = True; x.iloc[4] = True
        return pd.DataFrame({"long_entry": e, "long_exit": x})


def test_slots_limit_and_pnl():
    dfs = {"A": _df([100, 100, 100, 100, 100, 110, 110, 110]),
           "B": _df([50, 50, 50, 50, 50, 40, 40, 40])}
    cfg = MultiConfig(fee_rate=0, slippage_rate=0, max_positions=1)
    r = run_multi_backtest(dfs, EnterAt2ExitAt4(), cfg)
    # 슬롯 1개 → A 만 진입 (idx3 시가 100), idx5 시가 110 청산
    assert [t.ticker for t in r.trades] == ["A"]
    assert r.trades[0].entry_price == 100 and r.trades[0].exit_price == 110
    assert r.metrics["final_equity"] == pytest.approx(1_100_000)

    cfg2 = MultiConfig(fee_rate=0, slippage_rate=0, max_positions=2)
    r2 = run_multi_backtest(dfs, EnterAt2ExitAt4(), cfg2)
    assert sorted(t.ticker for t in r2.trades) == ["A", "B"]
    # 각 50만: A +10% → 55만, B -20% → 40만 = 95만
    assert r2.metrics["final_equity"] == pytest.approx(950_000)


def test_rotation_target_set_and_backtest():
    up = list(np.linspace(100, 200, 40))
    down = list(np.linspace(100, 60, 40))
    flat = [100.0] * 40
    dfs = {"UP": _df(up), "DOWN": _df(down), "FLAT": _df(flat)}
    strat = S.get("momentum", lookback=5, top=1, min_momentum=0.0)
    closes = pd.DataFrame({k: v["close"] for k, v in dfs.items()})
    tgt = strat.target_set(closes)
    assert tgt["UP"].iloc[-1] and not tgt["DOWN"].iloc[-1] and not tgt["FLAT"].iloc[-1]
    assert (tgt.sum(axis=1) <= 1).all()

    r = run_multi_backtest(dfs, strat, MultiConfig(fee_rate=0, slippage_rate=0, max_positions=1))
    assert r.trades and all(t.ticker == "UP" for t in r.trades)
    assert r.metrics["total_return"] > 0.5


@pytest.mark.parametrize("name", ["vb_plus", "ma_atr", "momentum"])
def test_new_strategies_follow_contract(name, ohlcv):
    sig = S.get(name).signals(ohlcv)
    assert list(sig.columns) == ["long_entry", "long_exit", "entry_price"] and len(sig) == len(ohlcv)


def test_ma_atr_trailing_exits_before_dead_cross():
    # 급등 후 급락: 데드크로스 전에 ATR 스탑으로 먼저 나가야 한다
    p = [100] * 40 + list(np.linspace(100, 160, 30)) + [120] * 5 + [118] * 30
    df = _df(p)
    sig = S.get("ma_atr", fast=5, slow=20, atr_n=10, atr_mult=2.0).signals(df)
    exits = list(sig.index[sig.long_exit])
    assert exits, "no exit"
    first_exit = exits[0]
    from cointrader.strategies.indicators import ema
    dead = ema(df.close, 5) < ema(df.close, 20)
    first_dead = dead[dead & (df.index > first_exit - pd.Timedelta(days=1))].index.min() if dead.any() else None
    assert first_dead is None or first_exit <= first_dead

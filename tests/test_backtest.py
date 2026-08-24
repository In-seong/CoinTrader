import numpy as np
import pandas as pd
import pytest

from cointrader import strategies as S
from cointrader.backtest import BacktestConfig, run_backtest
from cointrader.strategies.base import Strategy


class FixedStrategy(Strategy):
    """지정한 봉 인덱스에서 진입/청산 시그널을 내는 테스트용 전략."""
    name = "fixed"
    params = {"entry_at": 2, "exit_at": 4}

    def generate_signals(self, df):
        e = pd.Series(False, index=df.index)
        x = pd.Series(False, index=df.index)
        e.iloc[self.p["entry_at"]] = True
        x.iloc[self.p["exit_at"]] = True
        return pd.DataFrame({"long_entry": e, "long_exit": x})


def _flat_df(prices):
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    p = np.array(prices, float)
    return pd.DataFrame({"open": p, "high": p * 1.01, "low": p * 0.99, "close": p,
                         "volume": 1.0, "value": p}, index=idx)


def test_single_trade_pnl_exact():
    # 시그널 봉 다음 봉 시가 체결: 진입 idx3 (open=110), 청산 idx5 (open=121)
    df = _flat_df([100, 105, 100, 110, 115, 121, 120, 119])
    cfg = BacktestConfig(initial_cash=1_000_000, fee_rate=0.001, slippage_rate=0.0)
    r = run_backtest(df, FixedStrategy(entry_at=2, exit_at=4), cfg)

    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.entry_price == 110 and t.exit_price == 121
    qty = 1_000_000 / (110 * 1.001)
    expected_final = qty * 121 * (1 - 0.001)
    assert r.metrics["final_equity"] == pytest.approx(expected_final)
    assert t.pnl_pct == pytest.approx(121 * 0.999 / (110 * 1.001) - 1)
    assert r.metrics["n_trades"] == 1 and r.metrics["win_rate"] == 1.0


def test_no_trade_keeps_cash():
    df = _flat_df([100] * 20)
    r = run_backtest(df, FixedStrategy(entry_at=19, exit_at=19), BacktestConfig())
    assert r.metrics["n_trades"] == 0
    assert r.metrics["final_equity"] == BacktestConfig().initial_cash


def test_stop_loss_triggers_intrabar():
    df = _flat_df([100, 100, 100, 100, 90, 80, 80, 80])
    # idx3 시가 100 진입, idx4 저가 89.1 → -5% 손절가 95 에 체결
    cfg = BacktestConfig(fee_rate=0, slippage_rate=0, stop_loss=0.05)
    r = run_backtest(df, FixedStrategy(entry_at=2, exit_at=7), cfg)
    t = r.trades[0]
    assert t.reason == "stop_loss"
    assert t.exit_price == pytest.approx(90)  # 갭하락: 시가(90) < 손절가(95) → 시가 체결


def test_breakout_fills_at_target():
    idx = pd.date_range("2024-01-01", periods=6, freq="D")
    df = pd.DataFrame({"open": [100, 100, 100, 100, 100, 100],
                       "high": [110, 110, 130, 110, 110, 110],
                       "low":  [90, 90, 95, 90, 90, 90],
                       "close": [100, 100, 120, 100, 100, 100],
                       "volume": 1.0, "value": 1.0}, index=idx)
    cfg = BacktestConfig(fee_rate=0, slippage_rate=0)
    r = run_backtest(df, S.get("volatility_breakout", k=0.5), cfg)
    # idx2: target = 100 + (110-90)*0.5 = 110, high 130 → 110 체결, idx3 시가 100 청산
    t = r.trades[0]
    assert t.entry_price == pytest.approx(110) and t.exit_price == pytest.approx(100)
    assert t.entry_time == idx[2] and t.exit_time == idx[3]


def test_equity_never_nan_and_mdd_nonpositive(ohlcv):
    for name in S.available():
        r = run_backtest(ohlcv, S.get(name), BacktestConfig(), ticker="TEST", interval="day")
        assert not r.equity.isna().any()
        assert r.metrics["mdd"] <= 0
        assert r.metrics["final_equity"] == pytest.approx(r.equity.iloc[-1])

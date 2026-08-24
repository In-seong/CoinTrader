import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    """결정론적 합성 일봉 300개 (사인파 + 추세)."""
    rng = np.random.default_rng(42)
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    base = 1000 + 200 * np.sin(np.arange(n) / 15) + np.arange(n) * 0.5
    close = base + rng.normal(0, 5, n)
    open_ = close + rng.normal(0, 5, n)
    high = np.maximum(open_, close) + rng.uniform(2, 15, n)
    low = np.minimum(open_, close) - rng.uniform(2, 15, n)
    vol = rng.uniform(100, 1000, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": vol, "value": vol * close}, index=idx)

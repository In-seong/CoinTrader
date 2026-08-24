"""래리 윌리엄스 변동성 돌파.

목표가 = 당일 시가 + (전일 고가 - 전일 저가) * k
봉 안에서 목표가에 닿으면 매수, 다음 봉 시가에 매도.
ma_filter > 0 이면 시가가 N일 이동평균 위에 있을 때만 진입(상승장 필터).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import register
from .base import Strategy
from .indicators import sma


@register
class VolatilityBreakout(Strategy):
    name = "volatility_breakout"
    description = "변동성 돌파 (시가 + 전일변동폭*k 돌파 시 매수, 다음 봉 시가 매도)"
    params = {"k": 0.5, "ma_filter": 0}

    @property
    def warmup(self) -> int:
        return max(2, int(self.p["ma_filter"]) + 1)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        prev_range = (df["high"] - df["low"]).shift(1)
        target = df["open"] + prev_range * self.p["k"]

        if self.p["ma_filter"] > 0:
            ma = sma(df["close"], int(self.p["ma_filter"])).shift(1)
            target = target.where(df["open"] > ma, np.nan)

        return pd.DataFrame({
            "long_entry": False,
            "long_exit": True,          # 보유 중이면 항상 다음 봉 시가에 청산
            "entry_price": target,
        }, index=df.index)

    def snapshot(self, df):
        t = self.signals(df)["entry_price"].iloc[-1]
        c = float(df["close"].iloc[-1])
        if t != t:  # NaN → 이평 필터에 걸림
            return {"목표가": "-", "상태": "이평 필터로 진입 차단(시가 < MA)"}
        return {"목표가": f"{t:,.0f}", "현재가": f"{c:,.0f}", "목표까지": f"{(t / c - 1) * 100:+.2f}%"}

"""볼린저 밴드 평균회귀: 하단 밴드 이탈 시 매수, 중심선 회복 시 매도."""

from __future__ import annotations

import pandas as pd

from . import register
from .base import Strategy
from .indicators import bollinger


@register
class BollingerReversion(Strategy):
    name = "bollinger"
    description = "볼린저 하단 이탈 매수, 중심선(또는 상단) 회복 매도"
    params = {"period": 20, "k": 2.0, "exit_at": "mid"}  # exit_at: mid | upper

    @property
    def warmup(self) -> int:
        return int(self.p["period"]) + 1

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        lower, mid, upper = bollinger(df["close"], int(self.p["period"]), float(self.p["k"]))
        exit_line = upper if self.p["exit_at"] == "upper" else mid
        return pd.DataFrame({
            "long_entry": df["close"] < lower,
            "long_exit": df["close"] > exit_line,
        }, index=df.index)

    def snapshot(self, df):
        lower, mid, upper = bollinger(df["close"], int(self.p["period"]), float(self.p["k"]))
        c = float(df["close"].iloc[-1])
        return {"종가": f"{c:,.0f}", "하단밴드": f"{float(lower.iloc[-1]):,.0f}",
                "하단까지": f"{(c / float(lower.iloc[-1]) - 1) * 100:+.2f}%", "매수조건": "종가 < 하단"}

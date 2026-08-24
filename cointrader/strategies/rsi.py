"""RSI 역추세: 과매도 구간 진입 시 매수, 과매수 구간 도달 시 매도."""

from __future__ import annotations

import pandas as pd

from . import register
from .base import Strategy
from .indicators import rsi


@register
class RsiReversal(Strategy):
    name = "rsi"
    description = "RSI 과매도(<buy_below) 매수, 과매수(>sell_above) 매도"
    params = {"period": 14, "buy_below": 30.0, "sell_above": 70.0}

    @property
    def warmup(self) -> int:
        return int(self.p["period"]) * 3

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        r = rsi(df["close"], int(self.p["period"]))
        return pd.DataFrame({
            "long_entry": r < self.p["buy_below"],
            "long_exit": r > self.p["sell_above"],
        }, index=df.index)

    def snapshot(self, df):
        r = rsi(df["close"], int(self.p["period"]))
        return {"RSI": round(float(r.iloc[-1]), 1), "매수조건": f"< {self.p['buy_below']}",
                "24h최저RSI": round(float(r.tail(self._bars_24h(df)).min()), 1)}

    @staticmethod
    def _bars_24h(df):
        step = (df.index[-1] - df.index[-2]).total_seconds() or 60
        return max(2, int(86400 / step))

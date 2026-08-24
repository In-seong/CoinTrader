"""이동평균 골든/데드 크로스 추세추종."""

from __future__ import annotations

import pandas as pd

from . import register
from .base import Strategy
from .indicators import ema, sma


@register
class MaCross(Strategy):
    name = "ma_cross"
    description = "단기 이평이 장기 이평 위면 보유, 아래면 청산 (골든/데드크로스)"
    params = {"fast": 5, "slow": 20, "use_ema": False}

    @property
    def warmup(self) -> int:
        return int(self.p["slow"]) + 1

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        f = ema if self.p["use_ema"] else sma
        fast = f(df["close"], int(self.p["fast"]))
        slow = f(df["close"], int(self.p["slow"]))
        above = fast > slow
        return pd.DataFrame({
            "long_entry": above & ~above.shift(1, fill_value=False),
            "long_exit": ~above & above.shift(1, fill_value=False),
        }, index=df.index)

    def snapshot(self, df):
        f = ema if self.p["use_ema"] else sma
        fast = f(df["close"], int(self.p["fast"])); slow = f(df["close"], int(self.p["slow"]))
        above = (fast > slow)
        flips = above.ne(above.shift(1)) & above.shift(1).notna()
        last = df.index[flips][-1] if flips.any() else None
        return {"단기-장기": f"{(float(fast.iloc[-1]) / float(slow.iloc[-1]) - 1) * 100:+.2f}%",
                "상태": "골든(보유구간)" if bool(above.iloc[-1]) else "데드(현금구간)",
                "마지막크로스": str(last)[:16] if last is not None else "-", "매수조건": "단기가 장기를 상향 돌파하는 봉"}

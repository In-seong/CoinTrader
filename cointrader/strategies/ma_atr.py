"""EMA 크로스 추세추종 + ATR 트레일링 스탑.

진입: 단기 EMA 가 장기 EMA 위 (골든 상태)
청산: 데드크로스  또는  종가 < (진입 후 최고 종가 - atr_mult × ATR)
트레일링 스탑이 있어 휩소 구간에서 손실을 잘라내고, 큰 추세에선 끝까지 따라간다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import register
from .base import Strategy
from .indicators import atr, ema


@register
class MaAtrTrend(Strategy):
    name = "ma_atr"
    description = "EMA 골든/데드크로스 + ATR 트레일링 스탑"
    params = {"fast": 10, "slow": 30, "atr_n": 14, "atr_mult": 3.0}

    @property
    def warmup(self) -> int:
        return max(int(self.p["slow"]), int(self.p["atr_n"])) * 2

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = ema(df["close"], int(self.p["fast"])).to_numpy()
        slow = ema(df["close"], int(self.p["slow"])).to_numpy()
        a = atr(df, int(self.p["atr_n"])).to_numpy()
        close = df["close"].to_numpy()
        mult = float(self.p["atr_mult"])

        n = len(df)
        entry = np.zeros(n, bool)
        exit_ = np.zeros(n, bool)
        long = False
        peak = 0.0
        for i in range(n):
            if np.isnan(fast[i]) or np.isnan(slow[i]) or np.isnan(a[i]):
                continue
            golden = fast[i] > slow[i]
            if long:
                peak = max(peak, close[i])
                if not golden or close[i] < peak - mult * a[i]:
                    exit_[i] = True
                    long = False
            elif golden:
                entry[i] = True
                long = True
                peak = close[i]
        return pd.DataFrame({"long_entry": entry, "long_exit": exit_}, index=df.index)

    def snapshot(self, df):
        fast = ema(df["close"], int(self.p["fast"])); slow = ema(df["close"], int(self.p["slow"]))
        a = atr(df, int(self.p["atr_n"]))
        return {"단기-장기": f"{(float(fast.iloc[-1]) / float(slow.iloc[-1]) - 1) * 100:+.2f}%",
                "상태": "골든" if float(fast.iloc[-1]) > float(slow.iloc[-1]) else "데드",
                "ATR스탑폭": f"{float(a.iloc[-1]) * float(self.p['atr_mult']) / float(df['close'].iloc[-1]) * 100:.2f}%"}

"""변동성 돌파 개선판.

기본 VB 에 국내 퀀트 커뮤니티에서 검증된 필터들을 얹는다.
- k 자동: 최근 noise_n 봉의 노이즈 비율(1 - |시가-종가|/(고가-저가)) 평균을 k 로 사용.
  노이즈가 크면(꼬리만 긴 장) 목표가를 높여 가짜 돌파를 거른다. k_fixed>0 이면 고정 k.
- 추세 필터: 시가가 ma_filter 이평 위일 때만 진입.
- 거래량 필터: 전 봉 거래대금이 vol_n 봉 평균 이상일 때만 진입 (관심 없는 장 제외).
- 보유 연장: hold_ma>0 이면 종가가 hold_ma 이평 위인 동안 다음 봉에도 계속 보유(추세 태우기).
  0 이면 기본 VB 처럼 다음 봉 시가에 무조건 청산.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import register
from .base import Strategy
from .indicators import sma


@register
class VolatilityBreakoutPlus(Strategy):
    name = "vb_plus"
    description = "변동성돌파+ (노이즈 k, 추세·거래량 필터, 이평 위 보유 연장)"
    params = {"k_fixed": 0.0, "noise_n": 20, "ma_filter": 10, "vol_n": 20, "hold_ma": 5}

    @property
    def warmup(self) -> int:
        return max(int(self.p["noise_n"]), int(self.p["ma_filter"]), int(self.p["vol_n"]), int(self.p["hold_ma"])) + 2

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        if self.p["k_fixed"] > 0:
            k = pd.Series(float(self.p["k_fixed"]), index=df.index)
        else:
            noise = 1 - (df["open"] - df["close"]).abs() / rng
            k = noise.rolling(int(self.p["noise_n"]), min_periods=int(self.p["noise_n"])).mean().shift(1)
        target = df["open"] + rng.shift(1) * k

        ok = pd.Series(True, index=df.index)
        if self.p["ma_filter"] > 0:
            ok &= df["open"] > sma(df["close"], int(self.p["ma_filter"])).shift(1)
        if self.p["vol_n"] > 0:
            value = df["value"] if "value" in df else df["volume"] * df["close"]
            ok &= value.shift(1) >= value.rolling(int(self.p["vol_n"]), min_periods=int(self.p["vol_n"])).mean().shift(1)
        target = target.where(ok, np.nan)

        if self.p["hold_ma"] > 0:
            exit_ = df["close"] < sma(df["close"], int(self.p["hold_ma"]))
        else:
            exit_ = pd.Series(True, index=df.index)
        return pd.DataFrame({"long_entry": False, "long_exit": exit_, "entry_price": target}, index=df.index)

    def snapshot(self, df):
        sig = self.signals(df)
        t = sig["entry_price"].iloc[-1]
        c = float(df["close"].iloc[-1])
        if t != t:
            return {"목표가": "-", "상태": "필터(추세/거래량)로 진입 차단"}
        return {"목표가": f"{t:,.0f}", "현재가": f"{c:,.0f}", "목표까지": f"{(t / c - 1) * 100:+.2f}%"}

"""상대 모멘텀 로테이션 (코인 간 비교 전략).

유니버스 전체의 종가를 놓고 매 봉 종가에
  점수 = 최근 lookback 봉 수익률
  → 점수 > min_momentum 인 것 중 상위 top 개를 보유 (절대 모멘텀 필터: 전부 약세면 현금)
다음 봉 시가에 교체. 보유 집합이 바뀔 때만 거래하므로 회전율이 낮다.

다른 전략과 달리 코인 하나의 df 로는 판단할 수 없어 cross_sectional=True 로 표시하고,
target_set(closes) 로 '어느 코인을 들고 있을지' 를 돌려준다.
"""

from __future__ import annotations

import pandas as pd

from . import register
from .base import Strategy


@register
class MomentumRotation(Strategy):
    name = "momentum"
    description = "상대모멘텀 로테이션: 최근 수익률 상위 N개 보유, 전부 약세면 현금"
    params = {"lookback": 20, "top": 2, "min_momentum": 0.0, "ma_filter": 0}
    cross_sectional = True

    @property
    def warmup(self) -> int:
        return max(int(self.p["lookback"]), int(self.p["ma_filter"])) + 2

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # 단일 코인 규약: 모멘텀 > 기준이면 보유, 아니면 청산 (유니버스 없이 쓸 때의 퇴화형)
        score = df["close"].pct_change(int(self.p["lookback"]))
        state = score > float(self.p["min_momentum"])
        prev = state.shift(1, fill_value=False)
        return pd.DataFrame({"long_entry": state & ~prev, "long_exit": ~state & prev}, index=df.index)

    def target_set(self, closes: pd.DataFrame) -> pd.DataFrame:
        """closes: index=시각, columns=티커. 반환: 같은 모양의 bool (이 봉 종가 기준 보유 대상)."""
        score = closes.pct_change(int(self.p["lookback"]))
        ok = score > float(self.p["min_momentum"])
        if self.p["ma_filter"] > 0:
            ok &= closes > closes.rolling(int(self.p["ma_filter"])).mean()
        ranked = score.where(ok).rank(axis=1, ascending=False, method="first")
        return (ranked <= int(self.p["top"])).fillna(False)

    def snapshot_universe(self, closes: pd.DataFrame) -> dict:
        score = closes.pct_change(int(self.p["lookback"])).iloc[-1].sort_values(ascending=False)
        tgt = self.target_set(closes).iloc[-1]
        return {c.replace("KRW-", ""): f"{s * 100:+.1f}%{'★' if tgt[c] else ''}" for c, s in score.items()}

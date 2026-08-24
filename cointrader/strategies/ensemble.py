"""앙상블: 여러 전략의 '포지션 상태'를 합성한다.

각 하위 전략의 시그널을 상태(보유=1/현금=0)로 바꾼 뒤
- all      : 모든 전략이 보유 상태일 때만 보유
- majority : 과반이 보유 상태일 때 보유
- any      : 하나라도 보유 상태면 보유
로 합성하고, 상태가 0→1 바뀌는 봉에 long_entry, 1→0 바뀌는 봉에 long_exit 을 낸다.

변동성 돌파처럼 entry_price(봉 내 돌파)를 쓰는 전략은 '그 봉 고가가 목표가 이상'이면
진입한 것으로 간주해 상태화한다(다음 봉 시가 체결로 근사).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import available, register
from .base import Strategy


def signals_to_state(sig: pd.DataFrame, high: pd.Series) -> pd.Series:
    """시그널 DataFrame → 보유 상태(bool) Series."""
    entry = sig["long_entry"].to_numpy(bool)
    exit_ = sig["long_exit"].to_numpy(bool)
    ep = sig["entry_price"].to_numpy(float)
    h = high.to_numpy(float)
    breakout = ~np.isnan(ep) & (h >= ep)

    state = np.zeros(len(sig), dtype=bool)
    long = False
    for i in range(len(sig)):
        if long and exit_[i]:
            long = False
        elif not long and (entry[i] or breakout[i]):
            long = True
        state[i] = long
    return pd.Series(state, index=sig.index)


@register
class Ensemble(Strategy):
    name = "ensemble"
    description = "여러 전략 합성 (mode: all=전부 동의, majority=과반, any=하나라도)"
    params: dict[str, Any] = {
        "strategies": "ma_cross,rsi",   # 쉼표 구분 또는 리스트
        "mode": "all",
        "sub_params": {},                # {"ma_cross": {"fast": 10}, ...}
    }

    def __init__(self, **overrides: Any):
        super().__init__(**overrides)
        names = self.p["strategies"]
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",") if n.strip()]
        if not names:
            raise ValueError("ensemble: strategies 가 비어 있습니다")
        if self.p["mode"] not in ("all", "majority", "any"):
            raise ValueError("ensemble: mode 는 all | majority | any")
        reg = available()
        sub_params = self.p.get("sub_params") or {}
        self.subs: list[Strategy] = [reg[n](**sub_params.get(n, {})) for n in names]

    @property
    def warmup(self) -> int:
        return max(s.warmup for s in self.subs) + 1

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        states = pd.concat([signals_to_state(s.signals(df), df["high"]) for s in self.subs], axis=1)
        votes = states.sum(axis=1)
        n = len(self.subs)
        if self.p["mode"] == "all":
            state = votes == n
        elif self.p["mode"] == "any":
            state = votes >= 1
        else:
            state = votes > n / 2
        prev = state.shift(1, fill_value=False)
        return pd.DataFrame({
            "long_entry": state & ~prev,
            "long_exit": ~state & prev,
        }, index=df.index)

    def snapshot(self, df):
        out = {}
        for s in self.subs:
            st = signals_to_state(s.signals(df), df["high"])
            out[s.name] = "보유" if bool(st.iloc[-1]) else "현금"
        out["합성"] = f"{self.p['mode']} → " + ("보유" if bool(signals_to_state(self.signals(df), df["high"]).iloc[-1]) else "현금")
        return out

    def describe(self) -> str:
        return f"{self.p['mode']}[{'+'.join(s.name for s in self.subs)}]"

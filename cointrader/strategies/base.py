"""전략 인터페이스.

모든 전략은 OHLCV DataFrame을 받아 아래 컬럼을 가진 DataFrame을 돌려준다.

- long_entry  (bool)  : 이 봉의 종가 시점에 매수 결정 → 다음 봉 시가에 체결
- long_exit   (bool)  : 이 봉의 종가 시점에 매도 결정 → 다음 봉 시가에 체결
- entry_price (float) : (선택) 봉 안에서 가격이 이 값에 닿으면 즉시 매수 (변동성 돌파용).
                        사용하지 않으면 NaN.

백테스트 엔진과 실시간 러너가 같은 시그널 규약을 공유하므로
전략 코드는 한 벌만 작성하면 된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import numpy as np
import pandas as pd

SIGNAL_COLUMNS = ["long_entry", "long_exit", "entry_price"]


class Strategy(ABC):
    #: 레지스트리 키 (CLI에서 --strategy 로 지정)
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    #: 기본 파라미터. 생성자 키워드로 덮어쓸 수 있다.
    params: ClassVar[dict[str, Any]] = {}
    #: True 면 코인 간 비교 전략 (target_set(closes) 제공). 러너/시뮬레이터가 다르게 다룬다.
    cross_sectional: ClassVar[bool] = False

    def __init__(self, **overrides: Any):
        unknown = set(overrides) - set(self.params)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {sorted(unknown)} "
                             f"(available: {sorted(self.params)})")
        self.p: dict[str, Any] = {**self.params}
        for k, v in overrides.items():
            # 기본값 타입에 맞춰 캐스팅 (CLI 문자열 입력 대응)
            default = self.params[k]
            if isinstance(default, bool):
                v = str(v).lower() in ("1", "true", "yes", "y") if isinstance(v, str) else bool(v)
            elif isinstance(default, int) and not isinstance(v, bool):
                v = int(float(v))
            elif isinstance(default, float):
                v = float(v)
            self.p[k] = v

    @property
    def warmup(self) -> int:
        """시그널이 유효해지기까지 필요한 최소 봉 수."""
        return 1

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        ...

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """generate_signals 결과를 규약에 맞게 정규화한다."""
        out = self.generate_signals(df)
        sig = pd.DataFrame(index=df.index)
        sig["long_entry"] = out.get("long_entry", False).astype(bool) if "long_entry" in out else False
        sig["long_exit"] = out.get("long_exit", False).astype(bool) if "long_exit" in out else False
        sig["entry_price"] = out["entry_price"].astype(float) if "entry_price" in out else np.nan
        sig["long_entry"] = sig["long_entry"].fillna(False).astype(bool)
        sig["long_exit"] = sig["long_exit"].fillna(False).astype(bool)
        return sig

    def snapshot(self, df: pd.DataFrame) -> dict[str, Any]:
        """마지막 봉 기준 지표값/조건 거리 (진단용). 전략이 선택적으로 구현."""
        return {}

    def describe(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.p.items())

    def __repr__(self) -> str:
        return f"{self.name}({self.describe()})"

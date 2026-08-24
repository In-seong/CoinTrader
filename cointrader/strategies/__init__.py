"""전략 레지스트리. 새 전략은 이 패키지에 모듈을 추가하고 @register 를 붙이면 된다."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from .base import SIGNAL_COLUMNS, Strategy

_REGISTRY: dict[str, type[Strategy]] = {}
_discovered = False


def register(cls: type[Strategy]) -> type[Strategy]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a `name`")
    _REGISTRY[cls.name] = cls
    return cls


def _discover() -> None:
    """패키지 내 모든 모듈을 import 해 @register 데코레이터가 실행되게 한다."""
    global _discovered
    _discovered = True
    pkg = importlib.import_module(__name__)
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name not in ("base", "indicators"):
            importlib.import_module(f"{__name__}.{mod.name}")


def available() -> dict[str, type[Strategy]]:
    if not _discovered:
        _discover()
    return dict(_REGISTRY)


def get(name: str, **params: Any) -> Strategy:
    reg = available()
    if name not in reg:
        raise KeyError(f"unknown strategy '{name}'. available: {sorted(reg)}")
    return reg[name](**params)


__all__ = ["SIGNAL_COLUMNS", "Strategy", "available", "get", "register"]

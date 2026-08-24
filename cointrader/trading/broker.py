"""주문 실행 추상화. PaperBroker(모의) / UpbitBroker(실거래)."""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path

import pyupbit

from cointrader.config import PROJECT_ROOT, settings

log = logging.getLogger(__name__)

MIN_ORDER_KRW = 5_000  # 업비트 최소 주문 금액


@dataclass
class Position:
    qty: float = 0.0
    avg_price: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.qty > 0


@dataclass
class Fill:
    side: str          # buy | sell
    ticker: str
    price: float
    qty: float
    krw: float
    fee: float


class Broker(ABC):
    #: 러너가 사이클당 한 번 받은 시세를 넣어두면 get_price 가 API 대신 이걸 쓴다
    price_cache: dict[str, float] | None = None

    @abstractmethod
    def get_krw_balance(self) -> float: ...

    @abstractmethod
    def get_position(self, ticker: str) -> Position: ...

    @abstractmethod
    def buy_market(self, ticker: str, krw: float) -> Fill | None: ...

    @abstractmethod
    def sell_market(self, ticker: str, qty: float) -> Fill | None: ...

    def get_price(self, ticker: str) -> float:
        if self.price_cache and ticker in self.price_cache:
            return self.price_cache[ticker]
        p = pyupbit.get_current_price(ticker)
        if p is None:
            raise RuntimeError(f"failed to get price for {ticker}")
        return float(p)


class PaperBroker(Broker):
    """현재가 기준 즉시 체결 가정. 상태는 JSON 파일에 저장해 재시작 후에도 유지."""

    def __init__(self, initial_cash: float = 1_000_000.0,
                 state_file: Path | None = None, fee_rate: float | None = None,
                 slippage_rate: float | None = None):
        self.state_file = state_file or PROJECT_ROOT / "data" / "paper_state.json"
        self.fee_rate = settings.fee_rate if fee_rate is None else fee_rate
        self.slippage_rate = settings.slippage_rate if slippage_rate is None else slippage_rate
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self._load()

    def _load(self) -> None:
        if self.state_file.exists():
            st = json.loads(self.state_file.read_text())
            self.cash = st["cash"]
            self.positions = {k: Position(**v) for k, v in st["positions"].items()}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({
            "cash": self.cash,
            "positions": {k: asdict(v) for k, v in self.positions.items() if v.is_open},
        }, indent=2))

    def reset(self, initial_cash: float) -> None:
        self.cash = initial_cash
        self.positions = {}
        self._save()

    def get_krw_balance(self) -> float:
        return self.cash

    def get_position(self, ticker: str) -> Position:
        return self.positions.get(ticker, Position())

    def buy_market(self, ticker: str, krw: float) -> Fill | None:
        krw = min(krw, self.cash)
        if krw < MIN_ORDER_KRW:
            log.warning("paper buy skipped: %.0f KRW < min order", krw)
            return None
        price = self.get_price(ticker) * (1 + self.slippage_rate)
        fee = krw * self.fee_rate
        qty = (krw - fee) / price
        self.cash -= krw
        pos = self.get_position(ticker)
        total_qty = pos.qty + qty
        pos.avg_price = (pos.avg_price * pos.qty + price * qty) / total_qty
        pos.qty = total_qty
        self.positions[ticker] = pos
        self._save()
        return Fill("buy", ticker, price, qty, krw, fee)

    def sell_market(self, ticker: str, qty: float) -> Fill | None:
        pos = self.get_position(ticker)
        qty = min(qty, pos.qty)
        if qty <= 0:
            return None
        price = self.get_price(ticker) * (1 - self.slippage_rate)
        gross = qty * price
        if gross < MIN_ORDER_KRW:
            log.warning("paper sell skipped: %.0f KRW < min order", gross)
            return None
        fee = gross * self.fee_rate
        self.cash += gross - fee
        pos.qty -= qty
        if pos.qty <= 1e-12:
            pos.qty, pos.avg_price = 0.0, 0.0
        self.positions[ticker] = pos
        self._save()
        return Fill("sell", ticker, price, qty, gross - fee, fee)


class UpbitBroker(Broker):
    """pyupbit 실거래. 시장가 주문만 사용한다."""

    def __init__(self):
        if not settings.has_upbit_keys:
            raise RuntimeError("UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 가 .env 에 필요합니다")
        self.api = pyupbit.Upbit(settings.upbit_access_key, settings.upbit_secret_key)

    @staticmethod
    def _coin(ticker: str) -> str:
        return ticker.split("-")[1]

    def get_krw_balance(self) -> float:
        return float(self.api.get_balance("KRW") or 0)

    def get_position(self, ticker: str) -> Position:
        coin = self._coin(ticker)
        qty = float(self.api.get_balance(coin) or 0)
        avg = float(self.api.get_avg_buy_price(coin) or 0)
        return Position(qty=qty, avg_price=avg)

    def buy_market(self, ticker: str, krw: float) -> Fill | None:
        krw = min(krw, self.get_krw_balance())
        if krw < MIN_ORDER_KRW:
            log.warning("buy skipped: %.0f KRW < min order", krw)
            return None
        resp = self.api.buy_market_order(ticker, krw)
        if not resp or "uuid" not in resp:
            raise RuntimeError(f"buy order failed: {resp}")
        fill_price = self.get_price(ticker)
        fee = krw * settings.fee_rate
        return Fill("buy", ticker, fill_price, (krw - fee) / fill_price, krw, fee)

    def sell_market(self, ticker: str, qty: float) -> Fill | None:
        if qty <= 0:
            return None
        resp = self.api.sell_market_order(ticker, qty)
        if not resp or "uuid" not in resp:
            raise RuntimeError(f"sell order failed: {resp}")
        price = self.get_price(ticker)
        gross = qty * price
        return Fill("sell", ticker, price, qty, gross, gross * settings.fee_rate)


def _order_fill(api, resp: dict, timeout: float = 20.0) -> tuple[float, float, float]:
    """주문 uuid 를 폴링해 (체결수량, 체결금액 KRW, 수수료) 를 돌려준다. 시장가는 보통 즉시 체결."""
    uuid = (resp or {}).get("uuid")
    if not uuid:
        raise RuntimeError(f"order rejected: {resp}")
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        od = api.get_order(uuid)
        last = od
        if od and od.get("state") in ("done", "cancel"):
            trades = od.get("trades") or []
            vol = sum(float(t["volume"]) for t in trades)
            funds = sum(float(t["funds"]) for t in trades)
            fee = float(od.get("paid_fee") or 0)
            if vol > 0:
                return vol, funds, fee
            break
        time.sleep(0.5)
    raise RuntimeError(f"order not filled: {uuid} state={last and last.get('state')}")


_SHARED_API = None


def shared_upbit_api():
    global _SHARED_API
    if _SHARED_API is None:
        if not settings.has_upbit_keys:
            raise RuntimeError("UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 가 .env 에 필요합니다")
        _SHARED_API = pyupbit.Upbit(settings.upbit_access_key, settings.upbit_secret_key)
    return _SHARED_API


class LiveSubBroker(Broker):
    """실계좌 하나를 여러 봇이 나눠 쓰는 가상 서브계좌.

    - cash: 이 봇에게 배정된 가상 KRW 예산 (실계좌 잔고와 별개로 추적)
    - positions: 이 봇이 직접 산 수량만 자기 것으로 간주 (같은 코인을 다른 봇이 들어도 침범 없음)
    - 주문은 실제 시장가로 나가고, 체결 결과(수량·금액·수수료)로 가상 장부를 갱신한다
    """

    def __init__(self, initial_cash: float, state_file: Path, api=None):
        self.api = api or shared_upbit_api()
        self.state_file = state_file
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self._load()

    def _load(self) -> None:
        if self.state_file.exists():
            st = json.loads(self.state_file.read_text())
            if st.get("mode") != "live":
                raise RuntimeError(
                    f"{self.state_file} 는 paper 기록입니다. 실거래 봇은 새 이름을 쓰거나 bots-reset 후 시작하세요")
            self.cash = st["cash"]
            self.positions = {k: Position(**v) for k, v in st["positions"].items()}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({
            "mode": "live",
            "cash": self.cash,
            "positions": {k: asdict(v) for k, v in self.positions.items() if v.is_open},
        }, indent=2))

    def get_krw_balance(self) -> float:
        return self.cash

    def get_position(self, ticker: str) -> Position:
        return self.positions.get(ticker, Position())

    def buy_market(self, ticker: str, krw: float) -> Fill | None:
        real_krw = float(self.api.get_balance("KRW") or 0)
        krw = min(krw, self.cash, real_krw)
        if krw < MIN_ORDER_KRW:
            log.warning("live buy skipped: %.0f KRW < min order (virtual=%.0f real=%.0f)",
                        krw, self.cash, real_krw)
            return None
        resp = self.api.buy_market_order(ticker, krw)
        vol, funds, fee = _order_fill(self.api, resp)
        spent = funds + fee
        price = funds / vol
        self.cash -= spent
        pos = self.get_position(ticker)
        total = pos.qty + vol
        pos.avg_price = (pos.avg_price * pos.qty + price * vol) / total
        pos.qty = total
        self.positions[ticker] = pos
        self._save()
        return Fill("buy", ticker, price, vol, spent, fee)

    def sell_market(self, ticker: str, qty: float) -> Fill | None:
        pos = self.get_position(ticker)
        coin = ticker.split("-")[1]
        real_qty = float(self.api.get_balance(coin) or 0)
        qty = min(qty, pos.qty, real_qty)
        if qty <= 0:
            return None
        resp = self.api.sell_market_order(ticker, qty)
        vol, funds, fee = _order_fill(self.api, resp)
        proceeds = funds - fee
        self.cash += proceeds
        pos.qty -= vol
        if pos.qty <= 1e-12:
            pos.qty, pos.avg_price = 0.0, 0.0
        self.positions[ticker] = pos
        self._save()
        return Fill("sell", ticker, funds / vol, vol, proceeds, fee)

"""LiveSubBroker: 실계좌 하나를 여러 봇이 가상 서브계좌로 나눠 쓰는 회계 검증 (네트워크 없음)."""

import pytest

from cointrader.trading.broker import MIN_ORDER_KRW, LiveSubBroker


class FakeUpbitAPI:
    """시장가 주문이 즉시 전량 체결되는 가짜 업비트. 실계좌 잔고를 흉내낸다."""

    def __init__(self, krw: float, price: float = 100.0, fee_rate: float = 0.0005):
        self.krw = krw
        self.coins: dict[str, float] = {}
        self.price = price
        self.fee_rate = fee_rate
        self._orders: dict[str, dict] = {}
        self._n = 0

    def get_balance(self, currency):
        return self.krw if currency == "KRW" else self.coins.get(currency, 0.0)

    def buy_market_order(self, ticker, krw):
        self._n += 1
        uuid = f"o{self._n}"
        fee = krw * self.fee_rate / (1 + self.fee_rate)
        funds = krw - fee
        vol = funds / self.price
        self.krw -= krw
        coin = ticker.split("-")[1]
        self.coins[coin] = self.coins.get(coin, 0.0) + vol
        self._orders[uuid] = {"state": "done", "paid_fee": fee,
                              "trades": [{"volume": vol, "funds": funds}]}
        return {"uuid": uuid}

    def sell_market_order(self, ticker, qty):
        self._n += 1
        uuid = f"o{self._n}"
        funds = qty * self.price
        fee = funds * self.fee_rate
        coin = ticker.split("-")[1]
        self.coins[coin] -= qty
        self.krw += funds - fee
        self._orders[uuid] = {"state": "done", "paid_fee": fee,
                              "trades": [{"volume": qty, "funds": funds}]}
        return {"uuid": uuid}

    def get_order(self, uuid):
        return self._orders[uuid]


def test_two_bots_partition_one_account(tmp_path):
    api = FakeUpbitAPI(krw=300_000, price=100.0)
    a = LiveSubBroker(initial_cash=100_000, state_file=tmp_path / "a.json", api=api)
    b = LiveSubBroker(initial_cash=200_000, state_file=tmp_path / "b.json", api=api)

    fa = a.buy_market("KRW-BTC", 100_000)
    fb = b.buy_market("KRW-BTC", 50_000)
    assert fa and fb
    # 가상 장부: 각자 자기 예산만 소진
    assert a.cash == pytest.approx(0) and b.cash == pytest.approx(150_000)
    # 실계좌에는 두 봇의 합이 반영
    assert api.get_balance("KRW") == pytest.approx(150_000)
    assert api.get_balance("BTC") == pytest.approx(a.get_position("KRW-BTC").qty + b.get_position("KRW-BTC").qty)

    # a 가 전량 매도해도 b 의 수량은 침범하지 않는다
    a.sell_market("KRW-BTC", a.get_position("KRW-BTC").qty)
    assert a.get_position("KRW-BTC").qty == 0
    assert b.get_position("KRW-BTC").qty > 0
    assert api.get_balance("BTC") == pytest.approx(b.get_position("KRW-BTC").qty)
    # 왕복 수수료만큼만 예산이 줄었다
    assert a.cash == pytest.approx(100_000 * (1 - 0.0005 / (1 + 0.0005)) * (1 - 0.0005), rel=1e-6)


def test_budget_capped_by_real_balance(tmp_path):
    api = FakeUpbitAPI(krw=30_000)
    a = LiveSubBroker(initial_cash=100_000, state_file=tmp_path / "a.json", api=api)
    fill = a.buy_market("KRW-BTC", 100_000)   # 실계좌엔 3만원뿐 → 3만원만 체결
    assert fill and fill.krw == pytest.approx(30_000)
    assert a.cash == pytest.approx(70_000)


def test_min_order_skip_and_state_persist(tmp_path):
    api = FakeUpbitAPI(krw=1_000_000)
    a = LiveSubBroker(initial_cash=MIN_ORDER_KRW - 1, state_file=tmp_path / "a.json", api=api)
    assert a.buy_market("KRW-BTC", 4_000) is None

    b = LiveSubBroker(initial_cash=100_000, state_file=tmp_path / "b.json", api=api)
    b.buy_market("KRW-ETH", 50_000)
    # 재시작해도 상태 유지
    b2 = LiveSubBroker(initial_cash=999, state_file=tmp_path / "b.json", api=api)
    assert b2.cash == pytest.approx(b.cash)
    assert b2.get_position("KRW-ETH").qty == pytest.approx(b.get_position("KRW-ETH").qty)


def test_refuses_paper_state_file(tmp_path):
    (tmp_path / "a.json").write_text('{"cash": 1000, "positions": {}}')  # mode 없음 = paper 기록
    with pytest.raises(RuntimeError, match="paper"):
        LiveSubBroker(initial_cash=1000, state_file=tmp_path / "a.json", api=FakeUpbitAPI(1000))

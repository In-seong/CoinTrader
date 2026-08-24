from cointrader.data.upbit_client import COLUMNS, _to_frame


def test_to_frame_matches_expected_layout():
    rows = [
        {"candle_date_time_kst": "2026-08-21T10:00:00", "opening_price": 1, "high_price": 2, "low_price": 0.5,
         "trade_price": 1.5, "candle_acc_trade_volume": 10, "candle_acc_trade_price": 15},
        {"candle_date_time_kst": "2026-08-21T09:00:00", "opening_price": 0.9, "high_price": 1.1, "low_price": 0.8,
         "trade_price": 1.0, "candle_acc_trade_volume": 5, "candle_acc_trade_price": 5},
    ]
    df = _to_frame(rows)
    assert list(df.columns) == COLUMNS
    assert df.index.is_monotonic_increasing and df.index.name == "time"
    assert df.iloc[-1]["close"] == 1.5 and df.iloc[0]["open"] == 0.9

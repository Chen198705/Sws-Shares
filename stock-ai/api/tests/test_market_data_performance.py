"""行情批量请求、短 TTL 缓存和 single-flight 回归测试。"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import market_data


def _clear_cache():
    with market_data._cache_guard:
        market_data._cache.clear()
        market_data._cache_locks.clear()


def _fields(symbol: str):
    fields = [""] * 40
    fields[0] = "1"
    fields[1] = symbol
    fields[2] = symbol[-6:]
    fields[3] = "10.00"
    fields[4] = "9.80"
    fields[5] = "9.90"
    fields[6] = "1000"
    fields[30] = "20260924100000"
    fields[31] = "0.20"
    fields[32] = "2.04"
    fields[33] = "10.20"
    fields[34] = "9.70"
    fields[37] = "10000"
    fields[38] = "1.25"
    return fields


def test_batch_quotes_use_one_request_and_cache():
    _clear_cache()
    original = market_data._fetch_quote_fields
    calls = []

    def fake_fetch(symbols):
        calls.append(tuple(symbols))
        return {symbol: _fields(symbol) for symbol in symbols}

    market_data._fetch_quote_fields = fake_fetch
    try:
        first = market_data.get_stocks_realtime(["600519", "000001"])
        second = market_data.get_stocks_realtime(["600519", "000001"])
    finally:
        market_data._fetch_quote_fields = original

    assert len(calls) == 1, calls
    assert set(calls[0]) == {"sh600519", "sz000001"}
    assert first["600519"]["最新价"] == 10.0
    assert second["000001"]["换手率"] == 1.25


def test_history_cache_single_flight_and_deep_copy():
    _clear_cache()
    original = market_data._get_stock_history_uncached
    calls = []

    def fake_history(code, days, freq):
        calls.append((code, days, freq))
        time.sleep(0.15)
        return pd.DataFrame([{"date": "2026-09-24", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}])

    market_data._get_stock_history_uncached = fake_history
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            frames = list(pool.map(lambda _: market_data.get_stock_history("600519", 20, "day"), range(4)))
    finally:
        market_data._get_stock_history_uncached = original

    assert len(calls) == 1, calls
    assert all(len(frame) == 1 for frame in frames)
    frames[0].loc[0, "close"] = 99
    assert frames[1].loc[0, "close"] == 1.5


if __name__ == "__main__":
    test_batch_quotes_use_one_request_and_cache()
    test_history_cache_single_flight_and_deep_copy()
    print("test_market_data_performance: ok")

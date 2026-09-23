"""波动率自适应（vol-aware）单元测试：边界 + 单调性 + clamp 兜底"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from market_data import calc_volatility_profile
from strategy_store import (
    StrategyParams, get_volatility_adjusted_stop_take, get_volatility_position_size,
)


def _df(n=30, vol=0.02, seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, vol, n)
    prices = 100 * (1 + pd.Series(rets)).cumprod()
    return pd.DataFrame({
        "open": prices.shift(1).fillna(prices.iloc[0]),
        "close": prices,
        "high": prices * (1 + abs(rng.normal(0, vol/2, n))),
        "low": prices * (1 - abs(rng.normal(0, vol/2, n))),
        "volume": rng.integers(1e6, 5e6, n),
    })


def test_empty_df_returns_empty():
    assert calc_volatility_profile(pd.DataFrame()) == {}


def test_missing_columns_returns_empty():
    df = pd.DataFrame({"close": [1, 2, 3]})
    assert calc_volatility_profile(df) == {}


def test_short_window_returns_empty():
    df = _df(n=10)
    assert calc_volatility_profile(df) == {}


def test_normal_window_returns_profile():
    df = _df(n=30, vol=0.02)
    p = calc_volatility_profile(df)
    assert "atr_pct" in p and "std_20" in p and "vol_rank" in p
    assert 0 < p["atr_pct"] < 0.1, p
    assert 0.0 <= p["vol_rank"] <= 1.0


def test_vol_position_size_decreases_with_vol():
    p = StrategyParams()
    big = get_volatility_position_size(p, atr_pct=0.04)
    small = get_volatility_position_size(p, atr_pct=0.02)
    tiny = get_volatility_position_size(p, atr_pct=0.005)
    assert tiny > small > big, (big, small, tiny)
    # 不会突破 ceiling
    assert big >= p.vol_position_floor
    assert tiny <= min(p.vol_position_ceiling, p.max_position_size)


def test_vol_stop_take_monotone_and_clamped():
    p = StrategyParams()
    results = [get_volatility_adjusted_stop_take("中线", p, a) for a in (0.01, 0.02, 0.03, 0.04, 0.05)]
    # 止损变得更松（更负）
    sls = [r[0] for r in results]
    assert all(a >= b for a, b in zip(sls, sls[1:]))  # 单调递增（更松）
    # 止盈单调上升
    tps = [r[1] for r in results]
    assert all(a <= b for a, b in zip(tps, tps[1:]))
    # clamp: ATR=5% 触底 vol_max_stop
    assert sls[-1] == p.vol_max_stop
    # ATR=1% 触底 vol_min_stop
    assert sls[0] == p.vol_min_stop
    # 无 atr_pct 走默认底线
    sl0, tp0 = get_volatility_adjusted_stop_take("中线", p, None)
    assert sl0 == p.vol_min_stop and tp0 == 0.0


if __name__ == "__main__":
    for fn in [test_empty_df_returns_empty,
               test_missing_columns_returns_empty,
               test_short_window_returns_empty,
               test_normal_window_returns_profile,
               test_vol_position_size_decreases_with_vol,
               test_vol_stop_take_monotone_and_clamped]:
        fn()
        print("PASS", fn.__name__)

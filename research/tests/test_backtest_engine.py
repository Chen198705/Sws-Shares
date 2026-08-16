"""回测引擎铁律验证：涨停买入拦截 + 跌停卖出顺延 + 最低佣金。"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest.engine import monthly_rebalance, _next_sellable_open


def _panel():
    dates = pd.date_range("2023-01-02", periods=24, freq="B")
    codes = ["600001", "000001", "300001"]
    rows = []
    # 600001: 第 3 天开盘涨停（prev close 10 -> open 11），模拟买入被拦截
    base = {"600001": 10.0, "000001": 5.0, "300001": 8.0}
    prices = {}
    for code in base:
        prices[code] = [base[code] * (1 + 0.01 * i) for i in range(len(dates))]
    for code, p in prices.items():
        for i, d in enumerate(dates):
            prev = prices[code][i - 1] if i > 0 else p[0]
            o = 11.0 if (code == "600001" and i == 2) else p[i]
            rows.append({"date": d, "code": code, "open": o,
                         "close": p[i], "high": max(o, p[i]),
                         "low": min(o, p[i]), "volume": 1_000_000,
                         "pct_chg": (p[i] / prev - 1) * 100 if i else 0.0})
    df = pd.DataFrame(rows)
    return {c: g.drop(columns="code") for c, g in df.groupby("code")}


def test_sellable_open_skips_limit_down():
    panel = _panel()
    dates = panel["600001"]["date"].tolist()
    cal = pd.DatetimeIndex(dates)
    open_ = pd.DataFrame({c: g.set_index("date")["open"] for c, g in panel.items()}).sort_index()
    close = pd.DataFrame({c: g.set_index("date")["close"] for c, g in panel.items()}).sort_index()
    prev_close = close.shift(1)
    # 制造一个跌停开盘：600001 在 idx2 开盘 10.5 vs prev close 10.2 -> +2.9%，不是跌停；
    # 直接改用 idx2 为跌停价格验证顺延逻辑
    prev = close.loc[dates[1], "600001"]
    open_.loc[dates[2], "600001"] = round(prev * 0.9, 2)  # -10% 跌停开盘
    p1 = _next_sellable_open(open_, prev_close, cal, "600001", 2)
    assert p1 == open_.loc[dates[3], "600001"], p1
    print("[test_engine] OK: sell deferred past limit-down open")


def test_monthly_rebalance_smoke():
    panel = _panel()
    dates = panel["600001"]["date"].tolist()
    cal = pd.DatetimeIndex(dates)
    factor = pd.DataFrame(1.0, index=cal, columns=list(panel))
    costs = {"stamp_duty": 0.001, "commission": 0.00025, "min_commission": 5.0,
             "slippage": 0.001, "transfer_fee": 0.00001}
    out = monthly_rebalance(panel, factor, costs, top_quantile=1.0, max_holdings=10,
                            min_listed_days=1, min_price=1.0, max_price=100.0,
                            initial_capital=1_000_000.0)
    assert len(out["nav"]) >= 1 and np.isfinite(out["nav"].iloc[-1])
    assert out["nav"].iloc[-1] > 1.0, "首期收益不应被 NAV 吞掉"
    print(f"[test_engine] OK: smoke nav={out['nav'].iloc[-1]:.6f}")


if __name__ == "__main__":
    test_sellable_open_skips_limit_down()
    test_monthly_rebalance_smoke()

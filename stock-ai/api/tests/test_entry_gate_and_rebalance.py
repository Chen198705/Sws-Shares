"""入场硬校验 + 存量仓位再平衡 回归测试。

覆盖两个已确认的真实缺陷：
1) 模型"声称满足"买入条件但实际不满足（600138 中青旅：RSI=57 却按 RSI<55 给出买入）
2) 单票上限只在开仓时校验，历史存量超限仓位（600138 曾占 ~28%）长期不被削减

测试不写生产库：凡是涉及 DB 的地方都用临时文件 + monkeypatch。
"""
import os, sys, sqlite3, tempfile, importlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


def _ind(**kw):
    base = {
        "RSI(14)": 50.0, "均线多头": "是", "MACD金叉": "否", "MACD状态": "多头",
        "KDJ金叉": "否", "量比": 1.0, "换手率": 1.5,
        "K": 50.0, "D": 50.0, "J": 50.0,
    }
    base.update(kw)
    return base


def _seed_history(n=30, atr_pct=0.025, seed=7, price=10.0):
    import numpy as np
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, atr_pct, n)
    prices = price * (1 + pd.Series(rets)).cumprod()
    return pd.DataFrame({
        "open": prices.shift(1).fillna(prices.iloc[0]),
        "close": prices,
        "high": prices * (1 + abs(rng.normal(0, atr_pct / 2, n))),
        "low": prices * (1 - abs(rng.normal(0, atr_pct / 2, n))),
        "volume": rng.integers(1e6, 5e6, n),
    })


class _Pos:
    def __init__(self, code, cost, cur, vol, horizon="中线"):
        self.stock_code = code
        self.avg_cost = cost
        self.current_price = cur
        self.volume = vol
        self.horizon = horizon


class _Order:
    def __init__(self, code, price, vol):
        self.status = "filled"
        self.filled_price = price
        self.volume = vol
        self.stock_code = code


class _Broker:
    def __init__(self, sellable=10 ** 9):
        self._sellable = sellable
        self.sell_calls = []

    def sellable_volume(self, code):
        return self._sellable

    def sell(self, code, vol, price):
        self.sell_calls.append((code, vol, price))
        return _Order(code, price, vol)


# --------------------------- 入场硬校验 ---------------------------

def test_gate_rejects_zhongqinglv_real_case():
    """600138 实际入场时 RSI=57.0、MACD 未金叉，中线规则应硬拒绝。"""
    tb = importlib.import_module("trading_bot")
    ind = _ind(**{"RSI(14)": 57.0, "MACD金叉": "否", "MACD状态": "空头", "KDJ金叉": "是"})
    ok, reasons = tb.validate_entry_conditions("中线", ind, 1.3)
    assert ok is False, "RSI=57 且 MACD 空头不满足中线条件，必须拒绝"
    joined = "；".join(reasons)
    assert "RSI" in joined and "MACD" in joined, joined
    print(f"PASS test_gate_rejects_zhongqinglv_real_case  ({joined})")


def test_gate_accepts_valid_mid():
    tb = importlib.import_module("trading_bot")
    ok, reasons = tb.validate_entry_conditions("中线", _ind(**{"RSI(14)": 48.0}), 1.2)
    assert ok is True, reasons
    print("PASS test_gate_accepts_valid_mid")


def test_gate_short_needs_kdj_or_volume():
    tb = importlib.import_module("trading_bot")
    weak = _ind(**{"RSI(14)": 35.0, "KDJ金叉": "否", "量比": 1.2})
    ok, reasons = tb.validate_entry_conditions("短线", weak, 2.0)
    assert ok is False and "量比" in "；".join(reasons), reasons
    strong = _ind(**{"RSI(14)": 35.0, "KDJ金叉": "否", "量比": 1.8})
    ok2, reasons2 = tb.validate_entry_conditions("短线", strong, 2.0)
    assert ok2 is True, reasons2
    print("PASS test_gate_short_needs_kdj_or_volume")


def test_gate_long_needs_turnover():
    tb = importlib.import_module("trading_bot")
    ok, reasons = tb.validate_entry_conditions("长线", _ind(**{"RSI(14)": 60.0}), 0.5)
    assert ok is False and "换手率" in "；".join(reasons), reasons
    ok2, reasons2 = tb.validate_entry_conditions("长线", _ind(**{"RSI(14)": 60.0}), 1.5)
    assert ok2 is True, reasons2
    print("PASS test_gate_long_needs_turnover")


def test_gate_missing_indicators_rejected():
    tb = importlib.import_module("trading_bot")
    ok, reasons = tb.validate_entry_conditions("中线", {}, None)
    assert ok is False and reasons, reasons
    print("PASS test_gate_missing_indicators_rejected")


# --------------------------- 存量仓位再平衡 ---------------------------

def _run_rebalance(pos, total=1_000_000.0, sellable=10 ** 9, max_single=0.05):
    tb = importlib.import_module("trading_bot")
    from strategy_store import StrategyParams
    params = StrategyParams()
    params.max_position_size = max_single

    calls = {"trade": [], "attr": []}
    orig_status, orig_hist = tb.get_trading_status, tb.get_stock_history
    orig_log, orig_attr = tb.log_trade, tb.close_attribution_for_code
    tb.get_trading_status = lambda: {"positions": [pos], "balance": {"total_assets": total}}
    tb.get_stock_history = lambda code, days=30: _seed_history(atr_pct=0.025)
    tb.log_trade = lambda *a, **k: calls["trade"].append(a) or 1
    tb.close_attribution_for_code = lambda *a, **k: calls["attr"].append(a)
    broker = _Broker(sellable=sellable)
    try:
        acted = tb.rebalance_oversized_positions(broker, params)
    finally:
        tb.get_trading_status, tb.get_stock_history = orig_status, orig_hist
        tb.log_trade, tb.close_attribution_for_code = orig_log, orig_attr
    return acted, broker, calls


def test_rebalance_trims_oversized_position():
    """600138：36900 股 x 7.05 = 260,145，占总资产 26%；上限 5% 应削到 ~7000 股。"""
    pos = _Pos("600138", 7.11, 7.05, 36900)
    acted, broker, calls = _run_rebalance(pos)
    assert acted is True
    assert broker.sell_calls == [("600138", 29900, 7.05)], broker.sell_calls
    assert calls["trade"], "再平衡必须落 trades 流水"
    assert "存量仓位再平衡" in calls["trade"][0][5], calls["trade"][0]
    print(f"PASS test_rebalance_trims_oversized_position  (sell={broker.sell_calls})")


def test_rebalance_skips_within_tolerance():
    """略超上限（2% 容差内）不动仓，避免反复微调。"""
    pos = _Pos("600612", 33.0, 33.83, 1500)  # 1500 x 33.83 = 50,745 vs cap 50,000
    acted, broker, calls = _run_rebalance(pos)
    assert acted is False and broker.sell_calls == [], broker.sell_calls
    print("PASS test_rebalance_skips_within_tolerance")


def test_rebalance_respects_t1():
    pos = _Pos("600138", 7.11, 7.05, 36900)
    acted, broker, calls = _run_rebalance(pos, sellable=0)
    assert acted is False and broker.sell_calls == [], broker.sell_calls
    print("PASS test_rebalance_respects_t1")


def test_rebalance_never_buys():
    pos = _Pos("300026", 3.5, 3.62, 1000)
    acted, broker, calls = _run_rebalance(pos)
    assert acted is False and broker.sell_calls == [], broker.sell_calls
    print("PASS test_rebalance_never_buys")


def test_should_iterate_excludes_rebalance_rows():
    """再平衡减仓属于风控动作，不能计入迭代样本。"""
    ss = importlib.import_module("strategy_store")
    tmp = Path(tempfile.mkdtemp()) / "iter.db"
    conn = sqlite3.connect(str(tmp))
    conn.execute("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, code TEXT, direction TEXT,
        strategy_type TEXT, price REAL, volume INTEGER, pnl REAL, reason TEXT)""")
    for code, reason in [
        ("600138", "存量仓位再平衡：单票占比 26.0% 超上限 5.0%，减仓 29900 股"),
        ("300026", "触发止盈（+8.2%）[短线]"),
        ("600612", "触发止损（-3.1%）[中线]"),
    ]:
        conn.execute("INSERT INTO trades (ts,code,direction,price,volume,pnl,reason)"
                     " VALUES (?,?,'sell',?,?,?,?)",
                     ("2026-09-24T10:00:00", code, 10.0, 100, 1.0, reason))
    conn.commit()
    conn.close()

    from strategy_store import StrategyParams
    params = StrategyParams()
    params.last_iterated_sell_id = 0
    params.last_reviewed_sell_id = 0
    params.observation_trades_threshold = 2

    orig_db, orig_load = ss.DB_PATH, ss.load_params
    ss.DB_PATH, ss.load_params = tmp, (lambda: params)
    try:
        obs, rev, obs_ready, rev_ready = ss.should_iterate()
    finally:
        ss.DB_PATH, ss.load_params = orig_db, orig_load
    assert obs == 2, f"再平衡行应被排除，实际 obs={obs}"
    assert obs_ready is True and rev_ready is False, (obs_ready, rev_ready)
    print("PASS test_should_iterate_excludes_rebalance_rows")


if __name__ == "__main__":
    test_gate_rejects_zhongqinglv_real_case()
    test_gate_accepts_valid_mid()
    test_gate_short_needs_kdj_or_volume()
    test_gate_long_needs_turnover()
    test_gate_missing_indicators_rejected()
    test_rebalance_trims_oversized_position()
    test_rebalance_skips_within_tolerance()
    test_rebalance_respects_t1()
    test_rebalance_never_buys()
    test_should_iterate_excludes_rebalance_rows()
    print("\nAll PASS")

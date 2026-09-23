"""
中青旅 (600138) 案例 replay + trailing peak 重启持久化测试。

目标：验证 B+C 在以下真实场景是否真的工作
- 场景 A（trailing replay）：
  cost=¥7.11, peak=+7.9% (¥7.67), 当前价 ¥7.40 (+4.1%)
  期望：mid_trailing_activate=6% 已激活；pnl 4.1% <= peak-3%=4.9%
       → trailing 触发回撤止盈，卖出全量
- 场景 B（peak persistence）：
  保存 600138 的 peak 到 SQLite，模拟进程重启（清内存 dict）
  → _load_trailing_peak 必须拿回正确的历史 peak（不是当前 pnl）
"""
import os, sys, sqlite3, importlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "logs" / "trading_log.db"


def _seed_30d_history(atr_pct=0.025, n=30, seed=7):
    """产一份接近中青旅真实 30 日日均振幅 ≈ 2.5% 的 K 线."""
    import numpy as np
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, atr_pct, n)
    prices = 7.11 * (1 + pd.Series(rets)).cumprod()
    return pd.DataFrame({
        "open": prices.shift(1).fillna(prices.iloc[0]),
        "close": prices,
        "high": prices * (1 + abs(rng.normal(0, atr_pct / 2, n))),
        "low":  prices * (1 - abs(rng.normal(0, atr_pct / 2, n))),
        "volume": rng.integers(1e6, 5e6, n),
    })


def _init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trailing_peaks (
            code TEXT PRIMARY KEY,
            peak_pnl REAL NOT NULL,
            strategy_type TEXT DEFAULT '中线',
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, code TEXT, direction TEXT,
            strategy_type TEXT DEFAULT '中线',
            price REAL, volume INTEGER, pnl REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS ai_sell_streak (
            code TEXT PRIMARY KEY,
            streak INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_review_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, code TEXT, strategy_type TEXT,
            action TEXT, reason TEXT, indicators TEXT,
            pnl_pct REAL, atr_pct REAL);
    """)
    conn.commit()
    conn.close()


def _purge(code):
    conn = sqlite3.connect(str(DB_PATH))
    for tbl in ("trailing_peaks", "trades", "ai_sell_streak"):
        conn.execute(f"DELETE FROM {tbl} WHERE code=?", (code,))
    conn.commit()
    conn.close()


def _make_pos(code="600138", cost=7.11, cur=7.40, vol=36900, stype="中线"):
    class P:
        stock_code = code
        avg_cost = cost
        current_price = cur
        volume = vol
        horizon = stype
    return P()


class _MockBroker:
    def __init__(self, sell_returns=None, sellable=1):
        self._sell_returns = sell_returns
        self._sellable = sellable
        self.sell_calls = []

    def sellable_volume(self, code):
        return self._sellable

    def sell(self, code, vol, price):
        self.sell_calls.append((code, vol, price))
        return self._sell_returns


class _FilledOrder:
    def __init__(self, code, price, vol):
        self.status = "filled"
        self.filled_price = price
        self.volume = vol
        self.stock_code = code


def test_peak_persists_across_restart():
    _init_db()
    _purge("600138")
    tb = importlib.import_module("trading_bot")
    tb._save_trailing_peak("600138", 0.079, "中线")
    tb._trailing_peak.clear()
    loaded = tb._load_trailing_peak("600138")
    assert loaded is not None, "重启后 _load_trailing_peak 返回 None"
    assert abs(loaded - 0.079) < 1e-6, f"loaded={loaded}"
    print(f"PASS test_peak_persists_across_restart  (loaded={loaded:.4f})")


def test_trailing_fires_on_zhongqinglv_drop():
    _init_db()
    _purge("600138")

    tb = importlib.import_module("trading_bot")
    tb._trailing_peak.clear()
    tb._ai_sell_streak.clear()

    tb._save_trailing_peak("600138", 0.079, "中线")

    from strategy_store import StrategyParams
    tb.get_effective_params = lambda: StrategyParams()

    pos = _make_pos(cur=7.40, vol=36900, stype="中线")
    tb.get_trading_status = lambda: {"positions": [pos]}
    tb.get_stock_history = lambda code, days=30: _seed_30d_history(atr_pct=0.025, n=30)

    order = _FilledOrder("600138", 7.40, 36900)
    broker = _MockBroker(sell_returns=order, sellable=36900)

    class _NoopClient:
        def is_alive(self): return False

    tb.check_positions(_NoopClient(), broker)

    assert len(broker.sell_calls) == 1, f"trailing 没触发: {broker.sell_calls}"
    code, vol, price = broker.sell_calls[0]
    assert code == "600138" and vol == 36900 and abs(price - 7.40) < 1e-6

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT direction, reason FROM trades WHERE code='600138' ORDER BY id DESC LIMIT 1"
    ).fetchall()
    has_peak = conn.execute("SELECT 1 FROM trailing_peaks WHERE code='600138'").fetchone()
    has_streak = conn.execute("SELECT 1 FROM ai_sell_streak WHERE code='600138'").fetchone()
    conn.close()
    assert rows and rows[0][0] == "sell", rows
    reason = rows[0][1]
    assert "回撤" in reason or "止损" in reason or "止盈" in reason, reason
    assert has_peak is None and has_streak is None

    print(f"PASS test_trailing_fires_on_zhongqinglv_drop  (reason={reason})")


def test_t1_silent_block_now_prints():
    _init_db()
    _purge("600138")
    tb = importlib.import_module("trading_bot")
    tb._trailing_peak.clear()
    tb._ai_sell_streak.clear()

    pos = _make_pos(cur=7.40, vol=36900, stype="中线")
    tb.get_trading_status = lambda: {"positions": [pos]}
    tb.get_stock_history = lambda code, days=30: _seed_30d_history(atr_pct=0.025, n=30)
    from strategy_store import StrategyParams
    tb.get_effective_params = lambda: StrategyParams()
    broker = _MockBroker(sell_returns=None, sellable=0)

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        tb.check_positions(object(), broker)
    text = buf.getvalue()
    assert "T+1" in text or "sellable" in text.lower() or "不可卖" in text or "今日" in text, \
        f"T+1 拦截应可见但日志无痕迹: {text!r}"
    snippet = text.strip().replace("\n", " | ")[:120]
    print(f"PASS test_t1_silent_block_now_prints  ({snippet})")


if __name__ == "__main__":
    test_peak_persists_across_restart()
    test_trailing_fires_on_zhongqinglv_drop()
    test_t1_silent_block_now_prints()
    print("\nAll PASS")

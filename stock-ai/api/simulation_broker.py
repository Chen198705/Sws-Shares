"""
模拟交易券商适配器
- 使用 SQLite 存储订单和持仓
- 价格按成交时刻市价计算
- 不涉及真实资金
"""
import json
import uuid
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

from broker_adapter import BrokerAdapter, Order, Position, normalize_stock_code
from typing import Optional, Dict, Any


DB_PATH = Path(__file__).parent / "reports" / "simulation.db"
DB_PATH.parent.mkdir(exist_ok=True)


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            stock_code TEXT NOT NULL,
            stock_name TEXT DEFAULT '',
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            volume INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            horizon TEXT DEFAULT 'medium',
            created_at TEXT NOT NULL,
            filled_at TEXT,
            filled_price REAL,
            pnl REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT DEFAULT '',
            volume INTEGER NOT NULL DEFAULT 0,
            avg_cost REAL NOT NULL DEFAULT 0.0,
            horizon TEXT DEFAULT 'medium',
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS balance (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cash REAL NOT NULL DEFAULT 1000000.0,
            updated_at TEXT NOT NULL
        )
    """)
    # 初始化资金
    cur = conn.execute("SELECT cash FROM balance WHERE id = 1")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO balance (id, cash, updated_at) VALUES (1, 1000000.0, ?)",
                     (datetime.now().isoformat(),))
        conn.commit()
    return conn


class SimulationBroker(BrokerAdapter):
    def __init__(self, initial_cash: float = 1_000_000.0, **kwargs):
        self._conn = _get_db()
        # 支持自定义初始资金
        if initial_cash != 1_000_000.0:
            self._conn.execute(
                "UPDATE balance SET cash = ?, updated_at = ? WHERE id = 1",
                (initial_cash, datetime.now().isoformat())
            )
            self._conn.commit()

    # ── 内部工具 ────────────────────────────────────────────────

    def _get_cash(self) -> float:
        row = self._conn.execute("SELECT cash FROM balance WHERE id = 1").fetchone()
        return row[0] if row else 0.0

    def _deduct_cash(self, amount: float) -> bool:
        if self._get_cash() >= amount:
            self._conn.execute(
                "UPDATE balance SET cash = cash - ?, updated_at = ? WHERE id = 1",
                (amount, datetime.now().isoformat())
            )
            self._conn.commit()
            return True
        return False

    def _add_cash(self, amount: float):
        self._conn.execute(
            "UPDATE balance SET cash = cash + ?, updated_at = ? WHERE id = 1",
            (amount, datetime.now().isoformat())
        )
        self._conn.commit()

    def _get_or_create_position(self, stock_code: str) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT stock_code, stock_name, volume, avg_cost FROM positions WHERE stock_code = ?",
            (stock_code,)
        ).fetchone()
        return row

    def _update_position_buy(self, stock_code: str, stock_name: str,
                              volume: int, filled_price: float, horizon: str = "medium"):
        existing = self._conn.execute(
            "SELECT volume, avg_cost FROM positions WHERE stock_code = ?", (stock_code,)
        ).fetchone()

        if existing:
            old_vol, old_avg = existing
            new_vol = old_vol + volume
            new_avg = (old_vol * old_avg + volume * filled_price) / new_vol
            self._conn.execute(
                "UPDATE positions SET volume=?, avg_cost=?, stock_name=?, horizon=?, updated_at=? WHERE stock_code=?",
                (new_vol, new_avg, stock_name, horizon, datetime.now().isoformat(), stock_code)
            )
        else:
            self._conn.execute(
                "INSERT INTO positions (stock_code, stock_name, volume, avg_cost, horizon, updated_at) VALUES (?,?,?,?,?,?)",
                (stock_code, stock_name, volume, filled_price, horizon, datetime.now().isoformat())
            )
        self._conn.commit()

    def _update_position_sell(self, stock_code: str, volume: int, filled_price: float):
        row = self._conn.execute(
            "SELECT volume, avg_cost FROM positions WHERE stock_code = ?", (stock_code,)
        ).fetchone()
        if not row:
            return
        old_vol, avg_cost = row
        new_vol = old_vol - volume
        if new_vol <= 0:
            self._conn.execute("DELETE FROM positions WHERE stock_code = ?", (stock_code,))
        else:
            self._conn.execute(
                "UPDATE positions SET volume=?, updated_at=? WHERE stock_code=?",
                (new_vol, datetime.now().isoformat(), stock_code)
            )
        self._conn.commit()

    # ── 公开接口 ────────────────────────────────────────────────

    def buy(self, stock_code: str, volume: int, price: Optional[float] = None, horizon: str = "medium") -> Order:
        from market_data import get_stock_realtime
        stock_code = normalize_stock_code(stock_code)
        stock = get_stock_realtime(stock_code)
        stock_name = stock.get("股票名", stock_code)
        exec_price = price if price else stock.get("最新价", 0.0)
        total_cost = exec_price * volume * 1.0003

        order = Order(
            order_id=str(uuid.uuid4())[:8].upper(),
            stock_code=stock_code,
            stock_name=stock_name,
            direction="buy",
            price=exec_price,
            volume=volume,
            status="pending",
            horizon=horizon,
            created_at=datetime.now().isoformat(),
        )

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if not self._deduct_cash(total_cost):
                order.status = "rejected"
                self._save_order(order)
                self._conn.commit()
                return order
            order.status = "filled"
            order.filled_at = datetime.now().isoformat()
            order.filled_price = exec_price
            self._save_order(order)
            self._update_position_buy(stock_code, stock_name, volume, exec_price, horizon)
            self._conn.commit()
            return order
        except Exception as e:
            self._conn.execute("ROLLBACK")
            order.status = "rejected"
            return order

    def sell(self, stock_code: str, volume: int, price: Optional[float] = None, horizon: str = "medium") -> Order:
        from market_data import get_stock_realtime
        stock_code = normalize_stock_code(stock_code)
        stock = get_stock_realtime(stock_code)
        stock_name = stock.get("股票名", stock_code)
        exec_price = price if price else stock.get("最新价", 0.0)

        row = self._conn.execute(
            "SELECT volume, avg_cost FROM positions WHERE stock_code = ?", (stock_code,)
        ).fetchone()
        if not row or row[0] < volume:
            order = Order(
                order_id=str(uuid.uuid4())[:8].upper(),
                stock_code=stock_code,
                stock_name=stock_name,
                direction="sell",
                price=exec_price,
                volume=volume,
                status="rejected",
                horizon=horizon,
                created_at=datetime.now().isoformat(),
            )
            self._save_order(order)
            return order

        realized_pnl = (exec_price - row[1]) * volume
        order = Order(
            order_id=str(uuid.uuid4())[:8].upper(),
            stock_code=stock_code,
            stock_name=stock_name,
            direction="sell",
            price=exec_price,
            volume=volume,
            status="filled",
            horizon=horizon,
            created_at=datetime.now().isoformat(),
            filled_at=datetime.now().isoformat(),
            filled_price=exec_price,
            pnl=realized_pnl,
        )

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._save_order(order)
            self._update_position_sell(stock_code, volume, exec_price)
            net_proceeds = exec_price * volume * (1 - 0.0013)
            self._add_cash(net_proceeds)
            self._conn.commit()
            return order
        except Exception as e:
            self._conn.execute("ROLLBACK")
            order.status = "rejected"
            return order

    def _save_order(self, order: Order):
        self._conn.execute("""
            INSERT OR REPLACE INTO orders
            (order_id, stock_code, direction, price, volume, status, horizon, created_at, filled_at, filled_price, stock_name, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (order.order_id, order.stock_code, order.direction,
              order.price, order.volume, order.status, getattr(order, "horizon", "medium"),
              order.created_at, order.filled_at, order.filled_price,
              getattr(order, "stock_name", ""), getattr(order, "pnl", 0)))
        self._conn.commit()

    def get_positions(self) -> list[Position]:
        from market_data import get_stock_realtime
        positions = []
        rows = self._conn.execute(
            "SELECT stock_code, stock_name, volume, avg_cost, COALESCE(horizon, 'medium') FROM positions WHERE volume > 0"
        ).fetchall()
        for stock_code, stock_name, volume, avg_cost, horizon in rows:
            stock = get_stock_realtime(stock_code)
            cur_price = stock.get("最新价", avg_cost)
            stock_name = stock.get("股票名", stock_name)
            unrealized = (cur_price - avg_cost) * volume
            pnl_ratio = (cur_price / avg_cost - 1) * 100 if avg_cost > 0 else 0.0
            positions.append(Position(
                stock_code=stock_code,
                stock_name=stock_name,
                volume=volume,
                avg_cost=avg_cost,
                current_price=cur_price,
                unrealized_pnl=unrealized,
                pnl_ratio=pnl_ratio,
                horizon=horizon,
            ))
        return positions

    def get_orders(self, limit: int = 50) -> list[Order]:
        rows = self._conn.execute(
            "SELECT order_id, stock_code, direction, price, volume, status, horizon, created_at, filled_at, filled_price, stock_name, pnl "
            "FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Order(
            order_id=r[0], stock_code=r[1], direction=r[2],
            price=r[3], volume=r[4], status=r[5],
            horizon=r[6] if len(r) > 6 else "medium",
            created_at=r[7] if len(r) > 7 else r[6], filled_at=r[8] if len(r) > 8 else (r[7] if len(r) > 7 else None),
            filled_price=r[9] if len(r) > 9 else (r[8] if len(r) > 8 else None),
            stock_name=r[10] if len(r) > 10 else "",
            pnl=r[11] if len(r) > 11 else 0
        ) for r in rows]

    def cancel_order(self, order_id: str) -> bool:
        row = self._conn.execute(
            "SELECT status FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row and row[0] == "pending":
            self._conn.execute(
                "UPDATE orders SET status='cancelled' WHERE order_id = ?", (order_id,)
            )
            self._conn.commit()
            return True
        return False

    def get_balance(self) -> Dict:
        cash = self._get_cash()
        positions = self.get_positions()
        market_value = sum(p.current_price * p.volume for p in positions)
        total = cash + market_value
        return {
            "cash": cash,
            "market_value": market_value,
            "total_assets": total,
            "positions_count": len(positions),
        }


# ─── DB Schema Migration ──────────────────────────────────────────
def _migrate():
    """Add horizon columns to existing DB"""
    conn = _get_db()
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN horizon TEXT DEFAULT 'medium'")
    except Exception:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE positions ADD COLUMN horizon TEXT DEFAULT 'medium'")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN pnl REAL DEFAULT 0")
    except Exception:
        pass  # column already exists
    conn.commit()
    conn.close()

_migrate()

"""策略参数持久化 - 按 strategy_type 分离止损止盈参数"""
import sqlite3, json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

DB_PATH = Path(__file__).parent / "logs" / "trading_log.db"


@dataclass
class StrategyParams:
    max_position_size: float = 0.25
    max_total_position: float = 0.70
    stop_loss_pct: float = -0.05      # 全局兜底
    take_profit_pct: float = 0.15    # 全局兜底（中线默认）
    min_confidence: int = 60
    sector_weights: dict = field(default_factory=lambda: {
        "600": 1.0, "000": 1.0, "300": 0.8, "002": 1.0,
    })
    iteration: int = 0
    last_iteration_at: Optional[str] = None
    last_insight: str = ""
    closed_trades_threshold: int = 5
    # ---- 按策略类型分离的止损止盈 ----
    short_stop_loss: float = -0.03
    short_take_profit: float = 0.08
    mid_stop_loss: float = -0.05
    mid_take_profit: float = 0.15
    long_stop_loss: float = -0.10
    long_take_profit: float = 0.25


def _conn():
    return sqlite3.connect(str(DB_PATH), check_same_thread=False)


def init_schema():
    c = _conn()
    c.execute("""CREATE TABLE IF NOT EXISTS strategy_params (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trade_attribution (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER,
        strategy_type TEXT DEFAULT '中线',
        ai_reason TEXT,
        market_context TEXT,
        entry_indicators TEXT,
        closed INTEGER DEFAULT 0,
        closed_at TEXT,
        pnl REAL DEFAULT 0,
        closed_reason TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS iteration_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, iteration_num INTEGER, closed_trades_count INTEGER,
        insights TEXT, params_delta TEXT, ai_model_response TEXT)""")
    try:
        c.execute("ALTER TABLE trade_attribution ADD COLUMN strategy_type TEXT DEFAULT '中线'")
    except Exception:
        pass
    c.commit()
    c.close()


def load_params() -> StrategyParams:
    c = _conn()
    rows = c.execute("SELECT key, value FROM strategy_params").fetchall()
    c.close()
    if not rows:
        return StrategyParams()
    p = StrategyParams()
    float_keys = {
        "max_position_size", "max_total_position",
        "stop_loss_pct", "take_profit_pct",
        "short_stop_loss", "short_take_profit",
        "mid_stop_loss", "mid_take_profit",
        "long_stop_loss", "long_take_profit",
    }
    int_keys = {"min_confidence", "closed_trades_threshold", "iteration"}
    for key, val in rows:
        if hasattr(p, key):
            if key == "sector_weights":
                setattr(p, key, json.loads(val))
            elif key in float_keys:
                setattr(p, key, float(val))
            elif key in int_keys:
                setattr(p, key, int(val))
            else:
                setattr(p, key, val)
    return p


def save_params(p: StrategyParams):
    c = _conn()
    now = datetime.now().isoformat()
    for k, v in asdict(p).items():
        sv = json.dumps(v) if isinstance(v, dict) else str(v)
        c.execute("INSERT OR REPLACE INTO strategy_params (key,value,updated_at) VALUES (?,?,?)", (k, sv, now))
    c.commit()
    c.close()


def log_attribution(trade_id: int, ai_reason: str, market_context: str,
                    entry_indicators: str, strategy_type: str = "中线"):
    c = _conn()
    c.execute("""INSERT INTO trade_attribution
        (trade_id,strategy_type,ai_reason,market_context,entry_indicators)
        VALUES (?,?,?,?,?)""",
        (trade_id, strategy_type, ai_reason, market_context, entry_indicators))
    c.commit()
    c.close()


def close_attribution(trade_id: int, pnl: float, closed_reason: str = ""):
    c = _conn()
    c.execute(
        "UPDATE trade_attribution SET closed=1,closed_at=?,pnl=?,closed_reason=? WHERE trade_id=? AND closed=0",
        (datetime.now().isoformat(), pnl, closed_reason, trade_id))
    c.commit()
    c.close()


def get_closed_trades_for_review(limit: int = 50, strategy_type: str = None) -> list:
    c = _conn()
    if strategy_type:
        c.execute("""SELECT ta.id,ta.strategy_type,ta.ai_reason,ta.market_context,
            ta.entry_indicators,ta.pnl,ta.closed_reason,ta.closed_at,
            t.code,t.direction,t.price,t.volume
            FROM trade_attribution ta JOIN trades t ON t.id=ta.trade_id
            WHERE ta.closed=1 AND ta.strategy_type=? ORDER BY ta.closed_at DESC LIMIT ?""",
            (strategy_type, limit))
    else:
        c.execute("""SELECT ta.id,ta.strategy_type,ta.ai_reason,ta.market_context,
            ta.entry_indicators,ta.pnl,ta.closed_reason,ta.closed_at,
            t.code,t.direction,t.price,t.volume
            FROM trade_attribution ta JOIN trades t ON t.id=ta.trade_id
            WHERE ta.closed=1 ORDER BY ta.closed_at DESC LIMIT ?""", (limit,))
    cols = [d[0] for d in c.description]
    rows = c.fetchall()
    c.close()
    return [dict(zip(cols, r)) for r in rows]


def get_strategy_summary() -> dict:
    """各策略类型汇总统计"""
    c = _conn()
    rows = c.execute("""SELECT strategy_type, COUNT(*) as cnt,
        SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins,
        SUM(pnl) as net_pnl
        FROM trade_attribution WHERE closed=1 GROUP BY strategy_type""").fetchall()
    c.close()
    return {r[0]: {"count": r[1], "wins": r[2], "net_pnl": r[3]} for r in rows}


def log_iteration(iteration_num: int, closed_count: int, insights: str,
                  params_delta: str, ai_response: str):
    c = _conn()
    c.execute("""INSERT INTO iteration_log
        (ts,iteration_num,closed_trades_count,insights,params_delta,ai_model_response)
        VALUES (?,?,?,?,?,?)""",
        (datetime.now().isoformat(), iteration_num, closed_count, insights, params_delta, ai_response))
    c.commit()
    c.close()


def should_iterate() -> tuple[bool, int]:
    p = load_params()
    c = _conn()
    cnt = c.execute("SELECT COUNT(*) FROM trade_attribution WHERE closed=1").fetchone()[0]
    c.close()
    return cnt >= p.closed_trades_threshold, cnt


_HORIZON_ALIASES = {
    "short": "短线",
    "medium": "中线",
    "long": "长线",
}


def get_stop_take(strategy_type: str, params: StrategyParams) -> tuple[float, float]:
    """根据策略类型从 params 取止损止盈，兼容中文/英文周期名"""
    label = _HORIZON_ALIASES.get((strategy_type or "").strip().lower(), strategy_type or "中线")
    mapping = {
        "短线": (params.short_stop_loss, params.short_take_profit),
        "中线": (params.mid_stop_loss,   params.mid_take_profit),
        "长线": (params.long_stop_loss,  params.long_take_profit),
    }
    return mapping.get(label, (params.stop_loss_pct, params.take_profit_pct))


if __name__ == "__main__":
    init_schema()
    p = load_params()
    print(f"stop_loss={p.stop_loss_pct} take_profit={p.take_profit_pct} iteration={p.iteration}")
    print(f"short=({p.short_stop_loss},{p.short_take_profit}) mid=({p.mid_stop_loss},{p.mid_take_profit}) long=({p.long_stop_loss},{p.long_take_profit})")
    print(f"summary={get_strategy_summary()}")

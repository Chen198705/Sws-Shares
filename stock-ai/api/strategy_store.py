"""策略参数持久化 - 按 strategy_type 分离止损止盈参数"""
import os, sqlite3, json
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

DB_PATH = Path(__file__).parent / "logs" / "trading_log.db"
RESEARCH_PARAMS_PATH = Path(os.getenv(
    "RESEARCH_PARAMS_PATH",
    str(Path(__file__).resolve().parents[2] / "research" / "export" / "strategy_params.json"),
))


@dataclass
class StrategyParams:
    max_position_size: float = 0.25
    max_total_position: float = 0.70
    min_cash_pct: float = 0.30
    stop_loss_pct: float = -0.05      # 全局兜底
    take_profit_pct: float = 0.15    # 全局兜底（中线默认）
    min_confidence: int = 60
    sector_weights: dict = field(default_factory=lambda: {
        "600": 1.0, "000": 1.0, "300": 0.8, "002": 1.0,
    })
    iteration: int = 0
    last_iteration_at: Optional[str] = None
    last_insight: str = ""
    observation_trades_threshold: int = 3
    adjust_trades_threshold: int = 20
    last_iterated_sell_id: int = 0
    last_reviewed_sell_id: int = 0
    # ---- 按策略类型分离的止损止盈 ----
    short_stop_loss: float = -0.03
    short_take_profit: float = 0.08
    mid_stop_loss: float = -0.05
    mid_take_profit: float = 0.15
    long_stop_loss: float = -0.10
    long_take_profit: float = 0.25
    # ---- 回撤止盈：短线/中线到达激活线后，从峰值回撤超过阈值即落袋 ----
    short_trailing_activate: float = 0.05
    short_trailing_drawdown: float = 0.03
    mid_trailing_activate: float = 0.06
    mid_trailing_drawdown: float = 0.03


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
        insights TEXT, params_delta TEXT, ai_model_response TEXT,
        stage TEXT DEFAULT 'review')""")
    c.execute("""CREATE TABLE IF NOT EXISTS observation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        iteration_num INTEGER, ts TEXT, closed_trades_count INTEGER,
        insights TEXT, ai_model_response TEXT)""")
    try:
        c.execute("ALTER TABLE trade_attribution ADD COLUMN strategy_type TEXT DEFAULT '中线'")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE iteration_log ADD COLUMN stage TEXT DEFAULT 'review'")
    except Exception:
        pass
    try:
        existing = {r[0] for r in c.execute("SELECT key FROM strategy_params").fetchall()}
        now = datetime.now().isoformat()
        for key, val in (("observation_trades_threshold", 3), ("adjust_trades_threshold", 20)):
            if key not in existing:
                c.execute(
                    "INSERT OR REPLACE INTO strategy_params (key,value,updated_at) VALUES (?,?,?)",
                    (key, str(val), now))
    except Exception:
        pass
    try:
        row = c.execute(
            "SELECT 1 FROM strategy_params WHERE key='last_reviewed_sell_id'").fetchone()
        if not row:
            try:
                m = c.execute(
                    "SELECT COALESCE(MAX(id),0) FROM trades WHERE direction='sell'").fetchone()
                watermark = int(m[0] or 0)
            except Exception:
                watermark = 0
            c.execute(
                "INSERT OR REPLACE INTO strategy_params (key,value,updated_at) VALUES ('last_reviewed_sell_id',?,?)",
                (str(watermark), datetime.now().isoformat()))
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
        "min_cash_pct",
        "stop_loss_pct", "take_profit_pct",
        "short_stop_loss", "short_take_profit",
        "mid_stop_loss", "mid_take_profit",
        "long_stop_loss", "long_take_profit",
        "short_trailing_activate", "short_trailing_drawdown",
        "mid_trailing_activate", "mid_trailing_drawdown",
    }
    int_keys = {"min_confidence", "observation_trades_threshold",
                "adjust_trades_threshold", "iteration",
                "last_iterated_sell_id", "last_reviewed_sell_id"}
    legacy_aliases = {"closed_trades_threshold": "observation_trades_threshold"}
    for key, val in rows:
        key = legacy_aliases.get(key, key)
        if not hasattr(p, key):
            continue
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


def load_research_params() -> dict:
    """读取研究层只读契约；文件不存在或损坏时返回空 dict，不影响现有运行。"""
    try:
        if RESEARCH_PARAMS_PATH.exists():
            return json.loads(RESEARCH_PARAMS_PATH.read_text())
    except Exception:
        pass
    return {}


def get_research_overlay() -> dict:
    data = load_research_params()
    return {
        "version": data.get("version"),
        "confidence": data.get("confidence"),
        "regime": data.get("regime"),
        "factor_constraints": data.get("factor_constraints"),
        "policy_factors": data.get("policy_factors"),
        "risk_limits": data.get("risk_limits"),
        "horizon_weights": data.get("horizon_weights"),
    }


def get_effective_params() -> StrategyParams:
    """DB 参数 + 研究层只读风险覆盖；研究层不写回 DB。"""
    p = load_params()
    rl = load_research_params().get("risk_limits") or {}
    for src, dst in [
        ("max_position_pct", "max_total_position"),
        ("max_total_position", "max_total_position"),
        ("single_stock_pct", "max_position_size"),
        ("max_position_size", "max_position_size"),
        ("min_cash_pct", "min_cash_pct"),
    ]:
        if src in rl and rl[src] is not None:
            try:
                setattr(p, dst, float(rl[src]))
            except (TypeError, ValueError):
                pass
    return p


def log_attribution(trade_id: int, ai_reason: str, market_context: str,
                    entry_indicators: str, strategy_type: str = "中线"):
    c = _conn()
    exists = c.execute("SELECT 1 FROM trade_attribution WHERE trade_id=?", (trade_id,)).fetchone()
    if exists:
        c.close()
        return
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


def close_attribution_for_code(code: str, pnl: float, closed_reason: str = "",
                               volume: Optional[int] = None) -> int:
    """按股票代码 FIFO 关闭未平仓归因；盈亏按卖出数量比例分摊。"""
    c = _conn()
    rows = c.execute("""
        SELECT ta.id, t.volume FROM trade_attribution ta
        JOIN trades t ON t.id = ta.trade_id
        WHERE t.code = ? AND t.direction = 'buy' AND ta.closed = 0
        ORDER BY ta.trade_id
    """, (code,)).fetchall()
    if not rows:
        c.close()
        return 0
    total = volume if volume is not None else sum(r[1] or 0 for r in rows)
    remaining = total
    now = datetime.now().isoformat()
    closed = 0
    for ta_id, buy_vol in rows:
        if remaining <= 0:
            break
        alloc = min(buy_vol or 0, remaining)
        share = alloc / total if total else 0.0
        c.execute(
            "UPDATE trade_attribution SET closed=1,closed_at=?,pnl=?,closed_reason=? WHERE id=? AND closed=0",
            (now, (pnl or 0.0) * share, closed_reason, ta_id))
        c.commit()
        closed += 1
        remaining -= alloc
    c.close()
    return closed


def reconcile_closed_trades() -> list:
    """已平仓归因 = trades 卖单 FIFO 匹配全部带归因的买入（只读，不写库）。

    以 trades 流水为唯一口径：DB 中 closed 标记仅作状态留痕，
    聚合/复盘统一从这里推导，避免部分卖出时与写库状态重复或漏算。
    """
    c = _conn()
    buys = c.execute("""
        SELECT ta.id,ta.strategy_type,ta.ai_reason,ta.market_context,ta.entry_indicators,
               t.code,t.price,t.volume
        FROM trade_attribution ta JOIN trades t ON t.id = ta.trade_id
        WHERE t.direction = 'buy' ORDER BY ta.trade_id""").fetchall()
    sells = c.execute("""
        SELECT id,ts,code,volume,pnl,reason,strategy_type,price FROM trades
        WHERE direction = 'sell' ORDER BY id""").fetchall()
    c.close()

    pool = {}
    for r in buys:
        rec = {"id": r[0], "strategy_type": r[1], "ai_reason": r[2],
               "market_context": r[3], "entry_indicators": r[4],
               "code": r[5], "price": r[6], "volume": r[7], "remaining": r[7]}
        pool.setdefault(rec["code"], []).append(rec)

    closed = []
    for sell_id, ts, code, sell_vol, sell_pnl, reason, sell_stype, sell_price in sells:
        q = pool.get(code)
        remaining = sell_vol
        while q and remaining > 0:
            b = q[0]
            alloc = min(b["remaining"], remaining)
            share = alloc / sell_vol if sell_vol else 0.0
            closed.append({
                "id": b["id"], "strategy_type": b["strategy_type"],
                "ai_reason": b["ai_reason"], "market_context": b["market_context"],
                "entry_indicators": b["entry_indicators"],
                "pnl": (sell_pnl or 0.0) * share,
                "closed_reason": reason or "卖出", "closed_at": ts,
                "code": code, "direction": "buy", "price": b["price"], "volume": alloc,
            })
            b["remaining"] -= alloc
            remaining -= alloc
            if b["remaining"] <= 0:
                q.pop(0)
        if remaining > 0:
            stype = _HORIZON_ALIASES.get((sell_stype or "").strip().lower(), sell_stype or "中线")
            closed.append({
                "id": ("sell", sell_id), "strategy_type": stype,
                "ai_reason": "", "market_context": "",
                "entry_indicators": "",
                "pnl": (sell_pnl or 0.0) * (remaining / sell_vol) if sell_vol else (sell_pnl or 0.0),
                "closed_reason": reason or "卖出", "closed_at": ts,
                "code": code, "direction": "sell", "price": sell_price, "volume": remaining,
            })
    closed.sort(key=lambda x: x["closed_at"] or "", reverse=True)
    return closed


def get_closed_trades_for_review(limit: int = 50, strategy_type: str = None) -> list:
    records = reconcile_closed_trades()
    if strategy_type:
        records = [r for r in records if r["strategy_type"] == strategy_type]
    return records[:limit]


def get_strategy_summary() -> dict:
    """各策略类型汇总统计"""
    out = {}
    for r in reconcile_closed_trades():
        st = r["strategy_type"] or "未知"
        d = out.setdefault(st, {"count": 0, "wins": 0, "net_pnl": 0.0})
        d["count"] += 1
        if r["pnl"] > 0:
            d["wins"] += 1
        d["net_pnl"] += r["pnl"] or 0.0
    return out


def log_iteration(iteration_num: int, closed_count: int, insights: str,
                  params_delta: str, ai_response: str, stage: str = "review"):
    c = _conn()
    c.execute("""INSERT INTO iteration_log
        (ts,iteration_num,closed_trades_count,insights,params_delta,ai_model_response,stage)
        VALUES (?,?,?,?,?,?,?)""",
        (datetime.now().isoformat(), iteration_num, closed_count, insights,
         params_delta, ai_response, stage))
    c.commit()
    c.close()


def should_iterate() -> tuple[int, int, bool, bool]:
    """返回 (观察待处理笔数, 复核待处理笔数, 观察是否达阈值, 复核是否达阈值)。"""
    p = load_params()
    c = _conn()
    try:
        obs_base = p.last_iterated_sell_id
        obs_row = c.execute(
            "SELECT COUNT(*) FROM trades WHERE direction='sell' AND id > ?",
            (obs_base,)).fetchone()
        obs_cnt = int(obs_row[0] or 0)
        rev_base = p.last_reviewed_sell_id
        rev_row = c.execute(
            "SELECT COUNT(*) FROM trades WHERE direction='sell' AND id > ?",
            (rev_base,)).fetchone()
        rev_cnt = int(rev_row[0] or 0)
    except Exception:
        obs_cnt = rev_cnt = 0
    finally:
        c.close()
    return obs_cnt, rev_cnt, obs_cnt >= p.observation_trades_threshold, rev_cnt >= p.adjust_trades_threshold


def log_observation(iteration_num: int, closed_count: int, insights: str, ai_response: str):
    c = _conn()
    c.execute("""INSERT INTO observation_log
        (iteration_num,ts,closed_trades_count,insights,ai_model_response)
        VALUES (?,?,?,?,?)""",
        (iteration_num, datetime.now().isoformat(), closed_count, insights, ai_response))
    c.commit()
    c.close()


def get_recent_observations(limit: int = 10) -> list:
    c = _conn()
    rows = c.execute("""
        SELECT iteration_num, ts, closed_trades_count, insights
        FROM observation_log ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    c.close()
    return [
        {"iteration_num": r[0], "ts": r[1], "closed_trades_count": r[2], "insights": r[3]}
        for r in reversed(rows)
    ]


def get_max_sell_id() -> int:
    c = _conn()
    try:
        row = c.execute("SELECT COALESCE(MAX(id),0) FROM trades WHERE direction='sell'").fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        c.close()


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


def get_account_peak() -> float:
    """读取已记录账户总资产峰值；无记录返回 0（由调用方初始化）。"""
    c = _conn()
    row = c.execute("SELECT value FROM strategy_params WHERE key='account_peak'").fetchone()
    c.close()
    try:
        return float(row[0]) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def update_account_peak(total_assets: float) -> dict:
    """更新峰值并返回当前峰值与回撤；首见时以当前资产为峰值。"""
    peak = get_account_peak()
    if peak <= 0:
        peak = total_assets
    peak = max(peak, total_assets)
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO strategy_params (key,value,updated_at) VALUES ('account_peak',?,?)",
        (str(peak), datetime.now().isoformat()))
    c.commit()
    c.close()
    dd = total_assets / peak - 1 if peak > 0 else 0.0
    return {"peak": peak, "total_assets": total_assets, "drawdown": float(dd)}


def get_circuit_break_until() -> str:
    c = _conn()
    row = c.execute("SELECT value FROM strategy_params WHERE key='circuit_break_until'").fetchone()
    c.close()
    return row[0] if row and row[0] else ""


def set_circuit_break(days: int) -> str:
    until = (datetime.now() + timedelta(days=days)).isoformat()
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO strategy_params (key,value,updated_at) VALUES ('circuit_break_until',?,?)",
        (until, datetime.now().isoformat()))
    c.commit()
    c.close()
    return until


if __name__ == "__main__":
    init_schema()
    p = load_params()
    print(f"stop_loss={p.stop_loss_pct} take_profit={p.take_profit_pct} iteration={p.iteration}")
    print(f"short=({p.short_stop_loss},{p.short_take_profit}) mid=({p.mid_stop_loss},{p.mid_take_profit}) long=({p.long_stop_loss},{p.long_take_profit})")
    print(f"summary={get_strategy_summary()}")

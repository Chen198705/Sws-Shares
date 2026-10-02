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
    # ---- 波动率自适应（风险画像；不覆盖周期止损止盈） ----
    # 止损参考 = max(策略红线, -vol_stop_k * ATR%)；止盈参考 = max(策略红线, vol_take_k * ATR%)
    # 仓位上限 = clip(vol_position_k / ATR%, vol_position_floor, vol_position_ceiling)
    vol_stop_k: float = 2.5           # 止损距离 = k 倍 ATR%（与日内波动成正比）
    vol_take_k: float = 4.0           # 止盈距离 = k 倍 ATR%（让高波动股能跑得更远）
    vol_min_stop: float = -0.04       # 兜底：即使波动再小，止损也不会比这更紧
    vol_max_stop: float = -0.12       # 兜底：即使波动再大，止损也不会比这更宽
    vol_position_k: float = 0.30      # 仓位 = k / ATR%（日振幅 2% → 仓位 15%）
    vol_position_floor: float = 0.03  # 仓位下限（防止过度集中）
    vol_position_ceiling: float = 0.20  # 仓位上限（防止过度分散）
    ai_review_interval_min: int = 30  # AI 复评持仓的最小间隔（分钟）
    ai_review_min_pnl: float = -0.03  # 仅在浮亏 ≥ 此值时才发起 AI 复评（节省算力）
    ai_sell_streak_threshold: int = 2  # AI 复评连续建议 sell 多少次才真正平仓（反噪声）


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
        "vol_stop_k", "vol_take_k",
        "vol_min_stop", "vol_max_stop",
        "vol_position_k", "vol_position_floor", "vol_position_ceiling",
        "ai_review_min_pnl",
    }
    int_keys = {"min_confidence", "observation_trades_threshold",
                "adjust_trades_threshold", "iteration",
                "last_iterated_sell_id", "last_reviewed_sell_id",
                "ai_review_interval_min", "ai_sell_streak_threshold"}
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
    # 风控类减仓（存量仓位再平衡）不是策略信号样本，排除出迭代统计以免污染胜率/盈亏归因
    sample_filter = ("AND (reason IS NULL OR reason NOT LIKE '存量仓位再平衡%')")
    try:
        obs_base = p.last_iterated_sell_id
        obs_row = c.execute(
            f"SELECT COUNT(*) FROM trades WHERE direction='sell' AND id > ? {sample_filter}",
            (obs_base,)).fetchone()
        obs_cnt = int(obs_row[0] or 0)
        rev_base = p.last_reviewed_sell_id
        rev_row = c.execute(
            f"SELECT COUNT(*) FROM trades WHERE direction='sell' AND id > ? {sample_filter}",
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


def get_volatility_adjusted_stop_take(strategy_type: str, params: StrategyParams,
                                       atr_pct: Optional[float]) -> tuple[float, float]:
    """
    波动率自适应止损止盈：用 ATR% 自动划红线，min/max_stop 仅做边界兜底。

    规则：
      stop_loss = clamp(-vol_stop_k * atr_pct, vol_max_stop, vol_min_stop)
                 即：宽波动 → 容忍更多亏损；窄波动 → 不会比 min_stop 更紧；
                     极端波动也不会突破 max_stop。
      take_profit = max(vol_take_k * atr_pct, 0)
                 即：高波动时让盈利奔跑；低波动时收得紧凑。

    例子（中线，ATR%=3%）：
      vol_stop_k=2.5  → vol 止损 -7.5%
      vol_take_k=4    → vol 止盈 12%
    """
    if not atr_pct or atr_pct <= 0:
        return params.vol_min_stop, 0.0
    # 止损：波动越大可放宽（更负），波动越小越紧；min/max_stop 兜底
    vol_sl = -params.vol_stop_k * atr_pct
    sl = max(vol_sl, params.vol_max_stop)  # 不比 max_stop 更宽（更松）
    sl = min(sl, params.vol_min_stop)      # 不比 min_stop 更紧
    # 止盈：波动大时让盈利奔跑，atr_pct=0 时已 return 0
    vol_tp = params.vol_take_k * atr_pct
    return sl, vol_tp


def get_volatility_position_size(params: StrategyParams, atr_pct: Optional[float]) -> float:
    """
    波动率自适应仓位：atr_pct 越大 → 仓位越小（风险预算归一）。
    size = clip(vol_position_k / (atr_pct * 100), vol_position_floor, vol_position_ceiling)
    其中 atr_pct 是小数（0.02 = 2%），除以 100 后换算成百分点（2）。
    默认 k=0.30：日振幅 2% → 仓位 15%；日振幅 4% → 仓位 7.5%。
    兜底：min(vol_position_ceiling, max_position_size)
    """
    ceiling = min(params.vol_position_ceiling, params.max_position_size)
    if not atr_pct or atr_pct <= 0:
        return max(params.vol_position_floor, ceiling)
    size = params.vol_position_k / (atr_pct * 100)
    return max(params.vol_position_floor, min(size, ceiling))


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




# ============================================================================
# 分析结论缓存 + 分析计划队列
#
# 设计目标：把"打开网页就触发 AI 分析"改成"后台按计划预分析，结果存到 cache，
# 网页只读 cache"。避免每次打开/选股都付 30-60s AI 等待。
# ============================================================================

# 不同 horizon 的 cache 过期时间（秒）：短线短、中线中、长线长
_ANALYSIS_TTL_SECONDS = {
    "short": 900,     # 短线 15 分钟
    "mid": 1800,      # 中线 30 分钟
    "long": 10800,    # 长线 180 分钟（3 小时，覆盖全交易日）
    "unknown": 1800,  # 默认 30 分钟
}


def init_analysis_schema():
    """分析 cache + 队列表。与 init_schema 并存，幂等可重复调用。"""
    c = _conn()
    c.execute("""CREATE TABLE IF NOT EXISTS analysis_cache (
        code TEXT PRIMARY KEY,
        stock_snapshot TEXT,
        indicators TEXT,
        analysis_text TEXT,
        action TEXT,
        horizon TEXT,
        used_model TEXT,
        used_ai INTEGER,
        ai_error TEXT,
        created_at TEXT,
        expires_at TEXT,
        is_stale INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS analysis_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        priority INTEGER DEFAULT 5,
        reason TEXT,
        queued_at TEXT,
        started_at TEXT,
        finished_at TEXT,
        status TEXT DEFAULT 'pending',
        error TEXT DEFAULT '')""")
    # 部分唯一索引：同一 code 在 pending 状态下只占一行
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_unique_pending
        ON analysis_queue(code) WHERE status='pending'""")
    # 部分索引：调度器按优先级 + 时间拉取
    c.execute("""CREATE INDEX IF NOT EXISTS idx_queue_priority
        ON analysis_queue(priority DESC, queued_at ASC) WHERE status='pending'""")
    # 观点翻转事件表：只在 action/horizon 翻转瞬间记一行（不存全文）
    c.execute("""CREATE TABLE IF NOT EXISTS analysis_view_flips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        from_action TEXT,
        to_action TEXT,
        from_horizon TEXT,
        to_horizon TEXT,
        flipped_at TEXT,
        used_model TEXT,
        prev_created_at TEXT)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_flips_code_time
        ON analysis_view_flips(code, flipped_at DESC)""")
    c.commit()
    c.close()


def _analysis_ttl_for(horizon: str) -> int:
    return _ANALYSIS_TTL_SECONDS.get((horizon or "").lower(), _ANALYSIS_TTL_SECONDS["unknown"])


def get_cached_analysis(code: str):
    """读 cache。返回 dict 或 None（不存在）。返回的 dict 里有 is_stale 标记。"""
    c = _conn()
    row = c.execute("""SELECT code, stock_snapshot, indicators, analysis_text,
                              action, horizon, used_model, used_ai, ai_error,
                              created_at, expires_at, is_stale
                       FROM analysis_cache WHERE code=?""", (code,)).fetchone()
    c.close()
    if not row:
        return None
    now = datetime.now()
    try:
        expires_dt = datetime.fromisoformat(row[10]) if row[10] else now
        created_dt = datetime.fromisoformat(row[9]) if row[9] else now
    except Exception:
        expires_dt = now
        created_dt = now
    is_stale = bool(row[11]) or expires_dt < now
    return {
        "code": row[0],
        "stock_snapshot": json.loads(row[1]) if row[1] else None,
        "indicators": json.loads(row[2]) if row[2] else None,
        "analysis_text": row[3],
        "action": row[4],
        "horizon": row[5],
        "used_model": row[6],
        "used_ai": bool(row[7]),
        "ai_error": row[8],
        "created_at": row[9],
        "expires_at": row[10],
        "is_stale": is_stale,
        "age_seconds": max(0.0, (now - created_dt).total_seconds()),
    }


def save_analysis(code: str, stock_snapshot: dict, indicators: dict,
                  analysis_text: str, action: str, horizon: str,
                  used_model: str, used_ai: bool, ai_error: str = "") -> None:
    """写入或覆盖 cache。按 horizon 决定 expires_at，is_stale 重置为 0。
    若与上一份 cache 的 action/horizon 不同，先在 analysis_view_flips 记一行事件。
    """
    ttl = _analysis_ttl_for(horizon)
    now = datetime.now()
    expires = now + timedelta(seconds=ttl)
    new_action = (action or "hold").strip().lower()
    new_horizon = (horizon or "unknown").strip().lower()
    c = _conn()
    prev = c.execute(
        "SELECT action, horizon, created_at FROM analysis_cache WHERE code=?",
        (code,)).fetchone()
    if prev is not None:
        prev_action = (prev[0] or "").strip().lower()
        prev_horizon = (prev[1] or "").strip().lower()
        prev_created = prev[2] or ""
        if (prev_action != new_action) or (prev_horizon != new_horizon):
            c.execute("""INSERT INTO analysis_view_flips
                (code, from_action, to_action, from_horizon, to_horizon,
                 flipped_at, used_model, prev_created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (code, prev_action, new_action, prev_horizon, new_horizon,
                 now.isoformat(), used_model or "", prev_created))
    c.execute("""INSERT OR REPLACE INTO analysis_cache
        (code, stock_snapshot, indicators, analysis_text, action, horizon,
         used_model, used_ai, ai_error, created_at, expires_at, is_stale)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
        (code, json.dumps(stock_snapshot or {}, ensure_ascii=False, default=str),
         json.dumps(indicators or {}, ensure_ascii=False, default=str),
         analysis_text or "", new_action, new_horizon,
         used_model or "", 1 if used_ai else 0, ai_error or "",
         now.isoformat(), expires.isoformat()))
    c.commit()
    c.close()


def enqueue_analysis(code: str, reason: str = "scheduled", priority: int = 5) -> bool:
    """把 code 加入待分析队列；同一 code 在 pending 状态下不重复（部分唯一索引）。"""
    c = _conn()
    try:
        c.execute("""INSERT INTO analysis_queue (code, priority, reason, queued_at, status)
                     VALUES (?,?,?,?, 'pending')""",
                  (code, int(priority), reason or "scheduled", datetime.now().isoformat()))
        c.commit()
        c.close()
        return True
    except sqlite3.IntegrityError:
        c.close()
        return False


def claim_next_queued():
    """原子地取出一条 pending，标 running。返回 {'id','code','reason','priority'} 或 None。"""
    c = _conn()
    try:
        row = c.execute("""SELECT id, code, reason, priority FROM analysis_queue
                           WHERE status='pending'
                           ORDER BY priority DESC, queued_at ASC
                           LIMIT 1""").fetchone()
        if not row:
            return None
        cid, code, reason, priority = row
        c.execute("UPDATE analysis_queue SET status='running', started_at=? WHERE id=?",
                  (datetime.now().isoformat(), cid))
        c.commit()
        return {"id": cid, "code": code, "reason": reason, "priority": priority}
    finally:
        c.close()


def mark_queue_done(queue_id: int, error: str = "") -> None:
    """标记完成。error 非空时标 failed。"""
    c = _conn()
    status = "failed" if error else "done"
    c.execute("UPDATE analysis_queue SET status=?, finished_at=?, error=? WHERE id=?",
              (status, datetime.now().isoformat(), error or "", queue_id))
    c.commit()
    c.close()


def queue_stats() -> dict:
    c = _conn()
    pending = c.execute("SELECT COUNT(*) FROM analysis_queue WHERE status='pending'").fetchone()[0]
    running = c.execute("SELECT COUNT(*) FROM analysis_queue WHERE status='running'").fetchone()[0]
    failed = c.execute("SELECT COUNT(*) FROM analysis_queue WHERE status='failed'").fetchone()[0]
    c.close()
    return {"pending": pending, "running": running, "failed": failed}


def list_pending_codes(limit: int = 50) -> list:
    """调试/展示用：列出当前 pending 队列的 code。"""
    c = _conn()
    rows = c.execute("""SELECT code, priority, reason, queued_at FROM analysis_queue
                        WHERE status='pending'
                        ORDER BY priority DESC, queued_at ASC
                        LIMIT ?""", (limit,)).fetchall()
    c.close()
    return [{"code": r[0], "priority": r[1], "reason": r[2], "queued_at": r[3]} for r in rows]


def expire_stale_analyses() -> int:
    """把所有 expires_at < now 的 cache 标记为 stale，返回受影响行数。"""
    c = _conn()
    c.execute("UPDATE analysis_cache SET is_stale=1 WHERE expires_at < ? AND is_stale=0",
              (datetime.now().isoformat(),))
    changed = c.total_changes
    c.commit()
    c.close()
    return changed



if __name__ == "__main__":
    init_schema()
    p = load_params()
    print(f"stop_loss={p.stop_loss_pct} take_profit={p.take_profit_pct} iteration={p.iteration}")
    print(f"short=({p.short_stop_loss},{p.short_take_profit}) mid=({p.mid_stop_loss},{p.mid_take_profit}) long=({p.long_stop_loss},{p.long_take_profit})")
    print(f"summary={get_strategy_summary()}")

#!/usr/bin/env python3
"""分析计划后台调度器。

目标：把"打开网页就触发 AI 分析"改成"后台按计划预分析 + 结果缓存"。
- 持仓股：每 5 分钟过期（短线关注度高），每轮 tick 时入队（priority=10）
- 候选股（hot_stocks）：每 30 分钟过期，每轮 tick 时入队（priority=5）
- 单进程串行：避免 oMLX 并发把 GPU 打爆
- 单次处理：拉行情 → 调 AI → 写 cache → 标记完成

用法：
  from analysis_planner import start_planner_thread
  start_planner_thread()  # 在 trading_bot 里调用一次即可
"""
import sys, time, traceback
from datetime import datetime
from pathlib import Path
from threading import Thread, Event

sys.path.insert(0, str(Path(__file__).parent))

from strategy_store import (
    init_analysis_schema, init_schema,
    enqueue_analysis, claim_next_queued, mark_queue_done,
    save_analysis, get_cached_analysis, queue_stats,
    expire_stale_analyses, list_pending_codes,
)
from market_data import get_stock_realtime, get_stock_history, calc_indicators
from ai_client import OMLXClient, analyze_with_fallback, get_client

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
PLANNER_LOG = LOG_DIR / "analysis_planner.log"

# 调度参数
TICK_INTERVAL_SECONDS = 30      # 每 30 秒 tick 一次
HOLDING_REFRESH_SECONDS = 300   # 持仓股 5 分钟内不需要重分析
HOT_REFRESH_SECONDS = 1800      # 候选股 30 分钟内不需要重分析
MAX_PER_TICK = 1                # 每 tick 最多处理 1 只，避免并发打爆 oMLX


def _log(msg: str):
    PLANNER_LOG.parent.mkdir(exist_ok=True)
    with PLANNER_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")


# 持仓 / 候选缓存：避免每 30 秒都去查 broker / hot_stocks
_last_holding_refresh: float = 0.0
_last_hot_refresh: float = 0.0
_cached_holdings: list = []
_cached_hot_codes: list = []


def _refresh_holdings_cached(broker=None) -> list:
    """拉一次持仓，缓存 HOLDING_REFRESH_SECONDS 秒。"""
    global _last_holding_refresh, _cached_holdings
    now = time.monotonic()
    if now - _last_holding_refresh < HOLDING_REFRESH_SECONDS and _cached_holdings:
        return _cached_holdings
    try:
        if broker is None:
            from broker_adapter import get_broker
            broker = get_broker()
        status = broker.get_status() if hasattr(broker, "get_status") else {}
        positions = status.get("positions", []) or []
        _cached_holdings = [p.stock_code for p in positions if getattr(p, "stock_code", None)]
        _last_holding_refresh = now
        _log(f"刷新持仓缓存: {len(_cached_holdings)} 只")
    except Exception as e:
        _log(f"刷新持仓缓存失败: {e}")
    return _cached_holdings


def _refresh_hot_codes_cached() -> list:
    """拉一次候选股（hot_stocks），缓存 HOT_REFRESH_SECONDS 秒。"""
    global _last_hot_refresh, _cached_hot_codes
    now = time.monotonic()
    if now - _last_hot_refresh < HOT_REFRESH_SECONDS and _cached_hot_codes:
        return _cached_hot_codes
    try:
        from market_scanner import scan_market
        candidates = scan_market() or []
        _cached_hot_codes = [c.get("code") for c in candidates if c.get("code")]
        _last_hot_refresh = now
        _log(f"刷新候选缓存: {len(_cached_hot_codes)} 只")
    except Exception as e:
        _log(f"刷新候选缓存失败: {e}")
    return _cached_hot_codes


def _enqueue_plan_targets(broker=None):
    """按计划把持仓 + 候选股入队。已过期/缺失的优先。"""
    holdings = _refresh_holdings_cached(broker)
    hot_codes = _refresh_hot_codes_cached()

    enqueued = 0
    # 持仓股：priority=10（最高），reason=holding
    for code in holdings:
        cached = get_cached_analysis(code)
        if cached and not cached["is_stale"]:
            continue
        if enqueue_analysis(code, reason="holding", priority=10):
            enqueued += 1

    # 候选股：priority=5
    for code in hot_codes:
        cached = get_cached_analysis(code)
        if cached and not cached["is_stale"]:
            continue
        if enqueue_analysis(code, reason="hot_stock", priority=5):
            enqueued += 1

    if enqueued:
        _log(f"入队 {enqueued} 只（持仓 {len(holdings)} + 候选 {len(hot_codes)}）")
    return enqueued


def _process_one(client) -> bool:
    """处理一条队列：取一条 → 拉行情 → 调 AI → 写 cache。返回是否处理了。"""
    job = claim_next_queued()
    if not job:
        return False
    code = job["code"]
    try:
        # 1. 拉行情
        stock = get_stock_realtime(code)
        if isinstance(stock, dict) and "错误" in stock:
            mark_queue_done(job["id"], error=stock["错误"])
            _log(f"[{code}] 行情失败: {stock['错误']}")
            return True

        df = get_stock_history(code)
        indicators = calc_indicators(df)

        # 2. 调 AI（复用 server 的 fallback 链）
        analysis_text, action, used_ai, horizon, ai_error = "", "hold", False, "unknown", ""
        try:
            res = analyze_with_fallback(
                stock, indicators, 0.0,
                diagnostics={"ai_error": None}
            )
            if isinstance(res, tuple) and len(res) >= 4:
                analysis_text, action, used_ai, horizon = res[:4]
            elif isinstance(res, tuple) and len(res) == 3:
                analysis_text, action, used_ai = res
            if isinstance(res, tuple) and len(res) >= 5:
                ai_error = res[4] or ""
        except Exception as e:
            ai_error = f"ai_fallback_failed: {e}"
            _log(f"[{code}] analyze_with_fallback 异常: {e}")

        # 3. 写 cache
        used_model = getattr(client, "model", "") or ""
        save_analysis(
            code=code,
            stock_snapshot=stock,
            indicators=indicators,
            analysis_text=analysis_text,
            action=action,
            horizon=horizon,
            used_model=used_model,
            used_ai=used_ai,
            ai_error=ai_error,
        )
        mark_queue_done(job["id"])
        _log(f"[{code}] 分析完成 action={action} horizon={horizon} used_ai={used_ai}")
        return True
    except Exception as e:
        tb = traceback.format_exc()
        mark_queue_done(job["id"], error=str(e)[:200])
        _log(f"[{code}] 处理异常: {e}\n{tb}")
        return True


def tick(broker=None):
    """单次 tick：入队 + 处理一条。供守护循环或手动调用。"""
    try:
        expire_stale_analyses()
    except Exception as e:
        _log(f"expire_stale_analyses 失败: {e}")
    _enqueue_plan_targets(broker)
    try:
        client = get_client()
    except Exception:
        client = OMLXClient()
    processed = 0
    while processed < MAX_PER_TICK:
        if not _process_one(client):
            break
        processed += 1
    return processed


def _run_loop(stop_event: Event, broker=None):
    """守护循环。"""
    init_analysis_schema()
    _log(f"analysis_planner 启动 · tick={TICK_INTERVAL_SECONDS}s · max_per_tick={MAX_PER_TICK}")
    while not stop_event.is_set():
        try:
            tick(broker)
        except Exception as e:
            _log(f"tick 异常: {e}")
            traceback.print_exc()
        # 分段 sleep，stop_event.set() 时能立刻退出
        for _ in range(TICK_INTERVAL_SECONDS):
            if stop_event.is_set():
                break
            time.sleep(1)
    _log("analysis_planner 退出")


_started = False


def start_planner_thread(broker=None) -> Thread:
    """启动后台线程。重复调用幂等。返回 Thread 对象。"""
    global _started
    if _started:
        return None
    _started = True
    init_analysis_schema()
    t = Thread(target=_run_loop, args=(Event(), broker), daemon=True, name="analysis-planner")
    t.start()
    return t


def main():
    """CLI：单次 tick（调试用）。"""
    init_analysis_schema()
    init_schema()  # 兜底，确保主 schema 也存在
    n = tick()
    stats = queue_stats()
    pending = list_pending_codes(10)
    print(f"处理 {n} 条；队列 {stats}")
    if pending:
        print("pending 前 10:")
        for p in pending:
            print(f"  {p['code']} pri={p['priority']} reason={p['reason']}")


if __name__ == "__main__":
    main()

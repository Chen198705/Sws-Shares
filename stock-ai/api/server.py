import sys, os
import asyncio
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from datetime import datetime, date
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.concurrency import run_in_threadpool
import uvicorn, json

sys.path.insert(0, str(Path(__file__).parent))

from market_data import get_all_indices, get_stock_realtime, get_stock_history, calc_indicators
from ai_client import OllamaClient, analyze_with_fallback, get_client
from rule_engine import analyze as rule_analyze
from broker_adapter import get_broker
from trader import get_trading_status
from strategy_store import get_strategy_summary
from strategy_store import get_effective_params
from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL
from config import EXTRA_LLM_MODELS
from config import HIDE_LLM_MODELS
import market_calendar


_HEALTH_CACHE_TTL = 30.0
_HEALTH_CACHE_NEGATIVE_TTL = 3.0
_MODELS_CACHE_TTL = 15.0
_MODELS_FETCH_TIMEOUT = float(os.getenv("AI_MODELS_TIMEOUT", "20") or 20)
_WARMUP_TIMEOUT = float(os.getenv("AI_WARMUP_TIMEOUT", "180") or 180)
_health_cache = {"expires": 0.0, "payload": None}
_health_cache_lock = threading.Lock()
_health_refresh_inflight = False
_models_cache = {"expires": 0.0, "models": None}
_models_cache_lock = threading.Lock()
_models_fetch_inflight = False
_models_fetch_done = None
_MODELS_CACHE_FILE = Path(__file__).parent / "data" / "models_cache.json"
_analyze_semaphore = asyncio.Semaphore(2)


class SafeJSONResponse(JSONResponse):
    def render(self, content):
        return json.dumps(content, default=self._json_default).encode("utf-8")
    @staticmethod
    def _json_default(obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def is_market_open():
    import datetime
    now = datetime.datetime.now()
    time_str = now.strftime("%H%M")
    trading_day = market_calendar.is_trading_day(now.date())
    in_hours = ("0930" <= time_str <= "1130") or ("1300" <= time_str <= "1500")
    if trading_day and in_hours:
        return True, "交易中"
    if trading_day and time_str < "0930":
        return False, "等待开盘 · 09:30 开始交易"
    if trading_day and time_str < "1300":
        return False, "午间休市 · 13:00 恢复交易"
    if trading_day:
        return False, "今日已收盘"
    next_day = market_calendar.next_trading_day(now.date())
    return False, f"休市中 · 下个交易日 {next_day.strftime('%m/%d %A')}"


def serialize_positions(positions):
    return [
        {"stock_code": p.stock_code, "stock_name": getattr(p, "stock_name", p.stock_code),
         "volume": p.volume, "avg_cost": getattr(p, "avg_cost", 0), "current_price": getattr(p, "current_price", 0),
         "unrealized_pnl": getattr(p, "unrealized_pnl", 0), "pnl_ratio": getattr(p, "pnl_ratio", 0),
         "horizon": getattr(p, "horizon", "medium"), "prev_close": getattr(p, "prev_close", 0.0)}
        for p in positions
    ]


def _refresh_health_cache():
    global _health_refresh_inflight
    client = get_client()
    try:
        ai_alive = client.is_alive()
        payload = {"status": "ok", "ai": ai_alive, "model": client.model}
    except Exception as e:
        payload = {"status": "degraded", "ai": False, "model": client.model, "error": str(e)}
    finally:
        with _health_cache_lock:
            _health_cache["payload"] = payload
            ttl = _HEALTH_CACHE_TTL if payload.get("ai") else _HEALTH_CACHE_NEGATIVE_TTL
            _health_cache["expires"] = time.time() + ttl
            _health_refresh_inflight = False


def _health_payload_sync():
    """立即返回缓存，模型探活放到后台，避免首屏请求等待 oMLX。"""
    global _health_refresh_inflight
    now = time.time()
    with _health_cache_lock:
        if now < _health_cache["expires"] and _health_cache["payload"] is not None:
            return dict(_health_cache["payload"])
        client = get_client()
        payload = _health_cache["payload"]
        if payload is None:
            payload = {"status": "checking", "ai": None, "model": client.model}
        if not _health_refresh_inflight:
            _health_refresh_inflight = True
            threading.Thread(
                target=_refresh_health_cache,
                name="shenwansan-health",
                daemon=True,
            ).start()
        return dict(payload)


async def health(request):
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(None, _health_payload_sync)
    return SafeJSONResponse(payload)


def _fetch_remote_models_sync():
    import requests as _req
    r = _req.get(
        OLLAMA_BASE_URL + "/v1/models",
        headers={"Authorization": "Bearer " + OLLAMA_API_KEY},
        timeout=_MODELS_FETCH_TIMEOUT,
    )
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def _load_models_disk_cache():
    """上次成功拉到的模型列表；oMLX 冷启动 / 重启时前端下拉不至于空白。"""
    try:
        data = json.loads(_MODELS_CACHE_FILE.read_text())
        models = data.get("models") if isinstance(data, dict) else None
        if isinstance(models, list) and models:
            return [str(m) for m in models]
    except Exception:
        pass
    return None


def _save_models_disk_cache(models):
    try:
        _MODELS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MODELS_CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"models": list(models)}, ensure_ascii=False))
        os.replace(tmp, _MODELS_CACHE_FILE)
    except Exception:
        pass


def _fetch_remote_models(force=False):
    """拉取 oMLX 模型列表。

    加载顺序要点（首屏不再重复打 oMLX）：
      1. 内存 TTL 命中直接返回
      2. 真 single-flight：已有请求在飞就等它，绝不并发再发一次
      3. 成功后顺手 mark_alive，健康检查命中同一份结果
      4. 失败回落到磁盘缓存，过期也比空白强
    """
    global _models_fetch_inflight, _models_fetch_done
    leader = False
    waiter = None
    with _models_cache_lock:
        cached = _models_cache["models"]
        if cached is None:
            cached = _load_models_disk_cache()
            if cached:
                _models_cache["models"] = cached
        if not force and cached is not None and time.time() < _models_cache["expires"]:
            return list(cached)
        if _models_fetch_inflight:
            waiter = _models_fetch_done
        else:
            _models_fetch_inflight = True
            _models_fetch_done = threading.Event()
            leader = True

    if not leader:
        # 已经有人在拉：等结果复用，避免首屏每开一个页面就多一次上游请求
        if waiter is not None:
            waiter.wait(_MODELS_FETCH_TIMEOUT + 3)
        with _models_cache_lock:
            cached = _models_cache["models"]
        if cached:
            return list(cached)
        raise RuntimeError("模型列表正在刷新且暂无可用缓存")

    try:
        models = _fetch_remote_models_sync()
        with _models_cache_lock:
            _models_cache["models"] = models
            _models_cache["expires"] = time.time() + _MODELS_CACHE_TTL
        _save_models_disk_cache(models)
        with suppress(Exception):
            get_client().mark_alive(True)
        return list(models)
    except Exception:
        with _models_cache_lock:
            stale = _models_cache["models"]
        if stale:
            return list(stale)
        raise
    finally:
        with _models_cache_lock:
            _models_fetch_inflight = False
            if _models_fetch_done is not None:
                _models_fetch_done.set()


def _build_model_list(all_models):
    # 非通用 chat LLM：Embedding / OCR / Whisper / ASR / TTS / Rerank /
    # Dflash（推测解码架构）/ MTP（多 token 预测变体，如 MTPLX）
    llm_exclude = ["embedding", "bge-", "ocr", "whisper", "asr", "tts", "rerank", "dflash", "mtp"]
    model_list = [m for m in all_models if not any(e in m.lower() for e in llm_exclude)]
    if HIDE_LLM_MODELS:
        model_list = [m for m in model_list if m not in HIDE_LLM_MODELS]
    # current 以持久化配置为准：前端下拉、沈万三配置、API 分析共用同一个值。
    current = get_bot_config().get("model") or get_client().model
    for m in EXTRA_LLM_MODELS:
        if m not in model_list:
            model_list.append(m)
    if current and current not in model_list:
        model_list.insert(0, current)
    return sorted(model_list), current


async def models_list(request):
    try:
        loop = asyncio.get_running_loop()
        all_models = await loop.run_in_executor(None, _fetch_remote_models)
        model_list, current = _build_model_list(all_models)
        return JSONResponse({"models": model_list, "current": current})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def model_switch(request):
    try:
        body = await request.json()
        model = body.get("model", "").strip()
        if not model:
            return JSONResponse({"error": "model required"}, status_code=400)
        return JSONResponse(apply_model_switch(model))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def market_status(request):
    open_, msg = is_market_open()
    return SafeJSONResponse({"open": open_, "message": msg})

async def indices(request):
    try:
        return SafeJSONResponse(await run_in_threadpool(get_all_indices))
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

async def stock(request):
    code = request.path_params.get("code", "")
    try:
        return SafeJSONResponse(await run_in_threadpool(get_stock_realtime, code))
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)


def _history_payload_sync(code: str, days: int, freq: str):
    df = get_stock_history(code, days, freq)
    if df is None or df.empty:
        return {"error": "数据不足"}, 400
    ind = calc_indicators(df)
    return {
        "history": df[["date", "open", "high", "low", "close", "volume"]].to_dict(orient="records"),
        "indicators": ind,
    }, 200


async def history(request):
    code = request.path_params.get("code", "")
    days = int(request.query_params.get("days", 240))
    freq = request.query_params.get("freq", "day")
    try:
        payload, status_code = await run_in_threadpool(_history_payload_sync, code, days, freq)
        return SafeJSONResponse(payload, status_code=status_code)
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)


def _prepare_analysis_payload_sync(code: str):
    stock_data = get_stock_realtime(code)
    if "错误" in stock_data:
        return {"error": stock_data["错误"]}, 400
    df = get_stock_history(code)
    ind = calc_indicators(df)
    avg_pct = 0
    try:
        idx = get_all_indices()
        vals = [d.get("涨跌幅", 0) for d in idx.values() if "错误" not in d]
        avg_pct = sum(vals) / max(len(vals), 1)
    except Exception:
        pass
    return {
        "stock": stock_data,
        "indicators": ind,
        "index_pct": avg_pct,
    }, 200


def _analyze_prepared_payload_sync(prepared: dict, diagnostics: dict):
    analysis_text, action, used_ai, horizon = analyze_with_fallback(
        prepared["stock"],
        prepared["indicators"],
        prepared["index_pct"],
        diagnostics=diagnostics,
    )
    return {
        "analysis": analysis_text,
        "action": action,
        "used_ai": used_ai,
        "horizon": horizon,
        "ai_error": diagnostics.get("ai_error"),
        "stock": prepared["stock"],
        "indicators": prepared["indicators"],
    }, 200


async def analyze(request):
    try:
        body = await request.json()
    except:
        return SafeJSONResponse({"error": "invalid body"}, status_code=400)
    code = body.get("code", "").strip()
    if not code:
        return SafeJSONResponse({"error": "股票代码不能为空"}, status_code=400)
    try:
        prepared, status_code = await run_in_threadpool(_prepare_analysis_payload_sync, code)
        if status_code != 200:
            return SafeJSONResponse(prepared, status_code=status_code)
        diagnostics = {}
        async with _analyze_semaphore:
            payload, status_code = await run_in_threadpool(
                _analyze_prepared_payload_sync,
                prepared,
                diagnostics,
            )
        return SafeJSONResponse(payload, status_code=status_code)
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

def _serialize_order(order):
    return {
        "id": order.order_id,
        "code": order.stock_code,
        "direction": order.direction,
        "price": order.price,
        "volume": order.volume,
        "status": order.status,
        "filled_price": getattr(order, "filled_price", order.price),
        "stock_name": getattr(order, "stock_name", ""),
        "pnl": getattr(order, "pnl", 0),
        "horizon": getattr(order, "horizon", "medium"),
        "time": str(order.created_at) if order.created_at else "",
    }


def _portfolio_payload_sync():
    status = get_trading_status()
    status["positions"] = serialize_positions(status.get("positions", []))
    status["recent_orders"] = [_serialize_order(o) for o in status.get("recent_orders", [])]
    return status


async def portfolio(request):
    try:
        return SafeJSONResponse(await run_in_threadpool(_portfolio_payload_sync))
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)


def _orders_payload_sync():
    broker = get_broker()
    return {"orders": [_serialize_order(o) for o in broker.get_orders(20)]}


async def orders(request):
    try:
        return SafeJSONResponse(await run_in_threadpool(_orders_payload_sync))
    except Exception as e:
        return SafeJSONResponse({"error": str(e), "orders": []}, status_code=500)


def _order_stats_payload_sync():
    broker = get_broker()
    all_orders = broker.get_orders(limit=100000)

    def _filled_price(o):
        fp = getattr(o, "filled_price", None)
        return float(fp) if fp else float(o.price)

    sell_filled = [o for o in all_orders if o.direction == "sell" and o.status == "filled"]
    buy_filled = [o for o in all_orders if o.direction == "buy" and o.status == "filled"]

    sell_pnl = [float(getattr(o, "pnl", 0) or 0) for o in sell_filled]
    sell_profit = round(sum(p for p in sell_pnl if p > 0), 2)
    sell_loss = round(sum(p for p in sell_pnl if p < 0), 2)
    sell_net = round(sell_profit + sell_loss, 2)

    buy_cost = round(sum(_filled_price(o) * o.volume for o in buy_filled), 2)

    positions = broker.get_positions()
    unreal = [float(getattr(p, "unrealized_pnl", 0) or 0) for p in positions]
    unreal_profit = round(sum(p for p in unreal if p > 0), 2)
    unreal_loss = round(sum(p for p in unreal if p < 0), 2)
    unreal_net = round(unreal_profit + unreal_loss, 2)

    cnt_all = len(all_orders)
    cnt_buy = sum(1 for o in all_orders if o.direction == "buy")
    cnt_sell = sum(1 for o in all_orders if o.direction == "sell")

    return {
        "counts": {"all": cnt_all, "buy": cnt_buy, "sell": cnt_sell},
        "sell": {
            "count": len(sell_filled),
            "profit": sell_profit,
            "loss": sell_loss,
            "net": sell_net,
        },
        "buy": {
            "count": len(buy_filled),
            "cost": buy_cost,
            "profit": unreal_profit,
            "loss": unreal_loss,
            "net": unreal_net,
        },
    }


async def order_stats(request):
    try:
        return SafeJSONResponse(await run_in_threadpool(_order_stats_payload_sync))
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

async def order(request):
    try:
        body = await request.json()
    except:
        return SafeJSONResponse({"success": False, "error": "invalid body"}, status_code=400)
    market_open, market_msg = is_market_open()
    if not market_open:
        return SafeJSONResponse({"success": False, "error": market_msg}, status_code=403)
    try:
        payload, status_code = await run_in_threadpool(_order_payload_sync, body)
        return SafeJSONResponse(payload, status_code=status_code)
    except Exception as e:
        return SafeJSONResponse({"success": False, "error": str(e)}, status_code=500)


def _order_payload_sync(body):
    broker = get_broker()
    code = body.get("code", "").strip()
    direction = body.get("direction", "buy")
    volume = int(body.get("volume", 100))
    stock = get_stock_realtime(code)
    price = stock.get("最新价", 0)
    if direction == "buy":
        o = broker.buy(code, volume, price)
        ok = o.status == "filled"
        return {
            "success": ok,
            "order": {
                "id": o.order_id,
                "code": o.stock_code,
                "direction": o.direction,
                "price": o.filled_price,
                "volume": o.volume,
                "status": o.status,
            },
        }, 200

    available = broker.sellable_volume(code)
    if available <= 0:
        return {"success": False, "error": f"T+1 锁定：{code} 当日买入（T+0），次日才能卖"}, 409
    o = broker.sell(code, volume, price)
    if o is None:
        return {"success": False, "error": f"可卖量不足（{available} 股），已自动截单"}, 409
    ok = o.status == "filled"
    return {
        "success": ok,
        "order": {
            "id": o.order_id,
            "code": o.stock_code,
            "direction": o.direction,
            "price": o.filled_price,
            "volume": o.volume,
            "status": o.status,
        },
    }, 200

def _reconcile_payload_sync():
    """
    账本对账端点：拆解 NAV 与初始资金之间的差额来源。
    恒等式：NAV + 累计手续费 = 初始资金 + 已实现盈亏 + 浮动盈亏
    """
    try:
        broker = get_broker()
        all_orders = broker.get_orders(limit=100000)
        balance = broker.get_balance()
        positions = broker.get_positions()

        from config import INITIAL_CASH
        initial_cash = float(INITIAL_CASH)

        def _fp(o):
            fp = getattr(o, "filled_price", None)
            return float(fp) if fp else float(o.price)

        buy_filled = [o for o in all_orders if o.direction == "buy" and o.status == "filled"]
        sell_filled = [o for o in all_orders if o.direction == "sell" and o.status == "filled"]

        buy_turnover = round(sum(_fp(o) * o.volume for o in buy_filled), 2)
        sell_turnover = round(sum(_fp(o) * o.volume for o in sell_filled), 2)

        buy_commission = round(buy_turnover * BUY_FEE_RATE, 2)
        sell_commission = round(sell_turnover * BUY_FEE_RATE, 2)
        stamp_tax = round(sell_turnover * (SELL_FEE_RATE - BUY_FEE_RATE), 2)
        total_fees = round(buy_commission + sell_commission + stamp_tax, 2)

        realized_pnl = round(sum(float(getattr(o, "pnl", 0) or 0) for o in sell_filled), 2)
        unrealized_pnl = round(sum(float(getattr(p, "unrealized_pnl", 0) or 0) for p in positions), 2)

        nav = float(balance.get("total_assets", 0))
        cash = float(balance.get("cash", 0))
        market_value = float(balance.get("market_value", 0))

        # 恒等式校验：NAV + 手续费 = 初始资金 + 已实现 + 浮动
        rhs = initial_cash + realized_pnl + unrealized_pnl
        lhs = nav + total_fees
        diff = round(lhs - rhs, 4)

        return {
            "initial_cash": initial_cash,
            "cash": round(cash, 4),
            "market_value": round(market_value, 2),
            "total_assets": round(nav, 2),
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "fees": {
                "buy_commission": buy_commission,
                "sell_commission": sell_commission,
                "stamp_tax": stamp_tax,
                "total": total_fees,
                "buy_rate": BUY_FEE_RATE,
                "sell_rate": SELL_FEE_RATE,
            },
            "turnover": {
                "buy": buy_turnover,
                "sell": sell_turnover,
            },
            "orders": {
                "buy_count": len(buy_filled),
                "sell_count": len(sell_filled),
            },
            "identity": {
                "expected_with_fees": round(rhs, 2),
                "actual_with_fees": round(lhs, 2),
                "diff": diff,
                "consistent": abs(diff) < 0.05,
            },
            "as_of": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


async def reconcile(request):
    payload = await run_in_threadpool(_reconcile_payload_sync)
    return SafeJSONResponse(payload, status_code=500 if payload.get("error") else 200)

def hot_stocks(request):
    import sqlite3
    db_path = Path(__file__).parent / "logs" / "trading_log.db"
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT code, name, score, price, chg_pct, vol_ratio, turnover, reasons FROM market_scan_log WHERE ts = (SELECT MAX(ts) FROM market_scan_log) ORDER BY score DESC LIMIT 10")
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return JSONResponse({"stocks": []})
        stocks = [{"code": r[0], "name": r[1], "score": r[2], "price": r[3], "chg_pct": r[4], "vol_ratio": r[5], "turnover": r[6], "reasons": (r[7].split("|") if r[7] else [])} for r in rows]
        return JSONResponse({"stocks": stocks})
    except Exception as e:
        return JSONResponse({"stocks": [], "error": str(e)})

def _signal_payload_sync(code: str):
    stock_data = get_stock_realtime(code)
    if "错误" in stock_data:
        return {"error": stock_data["错误"]}, 400
    df = get_stock_history(code)
    ind = calc_indicators(df)
    rule_text, rule_action = rule_analyze(stock_data, ind, 0)
    return {"text": rule_text, "action": rule_action, "period": "medium", "reason": rule_text}, 200


async def signal(request):
    try:
        body = await request.json()
    except:
        return SafeJSONResponse({"error": "invalid body"}, status_code=400)
    code = body.get("code", "").strip()
    if not code:
        return SafeJSONResponse({"error": "code empty"}, status_code=400)
    try:
        payload, status_code = await run_in_threadpool(_signal_payload_sync, code)
        return SafeJSONResponse(payload, status_code=status_code)
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)


# ── 沈万三模型配置 ─────────────────────────────────────────────
BOT_CONFIG_PATH = Path(__file__).parent / "bot_config.json"
_bot_config_lock = threading.Lock()

def get_bot_config():
    try:
        if BOT_CONFIG_PATH.exists():
            data = json.loads(BOT_CONFIG_PATH.read_text())
            if isinstance(data, dict) and data.get("model"):
                return data
    except Exception:
        pass
    return {"model": OLLAMA_MODEL}

def save_bot_config(cfg):
    """原子写：先落临时文件再 replace，避免机器人进程读到半截 JSON。"""
    with _bot_config_lock:
        tmp = BOT_CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        os.replace(tmp, BOT_CONFIG_PATH)


def apply_model_switch(model):
    """统一模型切换的唯一入口。

    前端头部下拉（/api/model/switch）与沈万三设置弹窗（/api/bot-model/set）
    都走这里，保证三处状态一致：
      1. 持久化 bot_config.json —— 机器人进程靠它热切换
      2. 更新 API 进程全局客户端 —— 前端 /api/analyze 立即使用新模型
    """
    cfg = get_bot_config()
    cfg["model"] = model
    save_bot_config(cfg)
    get_client().set_model(model)
    return {"ok": True, "model": model}

async def bot_model_get(request):
    cfg = get_bot_config()
    return SafeJSONResponse({"model": cfg["model"], "effective": get_client().model})

async def bot_model_set(request):
    try:
        body = await request.json()
    except:
        return SafeJSONResponse({"error": "invalid body"}, status_code=400)
    model = body.get("model", "").strip()
    if not model:
        return SafeJSONResponse({"error": "model required"}, status_code=400)
    return SafeJSONResponse(apply_model_switch(model))


def _strategy_params_payload_sync():
    params = get_effective_params()
    return {
        "short_stop_loss": params.short_stop_loss,
        "short_take_profit": params.short_take_profit,
        "mid_stop_loss": params.mid_stop_loss,
        "mid_take_profit": params.mid_take_profit,
        "long_stop_loss": params.long_stop_loss,
        "long_take_profit": params.long_take_profit,
        "max_position_size": params.max_position_size,
        "max_total_position": params.max_total_position,
        "short_trailing_activate": params.short_trailing_activate,
        "short_trailing_drawdown": params.short_trailing_drawdown,
        "mid_trailing_activate": params.mid_trailing_activate,
        "mid_trailing_drawdown": params.mid_trailing_drawdown,
    }


async def strategy_params_get(request):
    """返回前端交易规则弹窗所需的动态参数。"""
    return SafeJSONResponse(await run_in_threadpool(_strategy_params_payload_sync))


def _research_status_payload_sync():
    root = Path(__file__).resolve().parents[2]
    contract_path = root / "research" / "export" / "strategy_params.json"
    attribution_path = root / "research" / "attribution" / "reports" / "attribution.json"
    payload = {"contract": {}, "attribution": None, "strategy_summary": {}}
    if contract_path.exists():
        payload["contract"] = json.loads(contract_path.read_text())
    if attribution_path.exists():
        payload["attribution"] = json.loads(attribution_path.read_text())
    try:
        payload["strategy_summary"] = get_strategy_summary()
    except Exception:
        pass
    payload["overlay_active"] = bool(payload["contract"])
    return payload


async def research_status(request):
    """研究层只读状态：参数契约 + 平仓归因 + 策略汇总。"""
    return SafeJSONResponse(await run_in_threadpool(_research_status_payload_sync))


# ── 路由 ────────────────────────────────────────────────────────
routes = [
    Route("/api/health", health),
    Route("/api/models", models_list),
    Route("/api/model/switch", model_switch, methods=["POST"]),
    Route("/api/market-status", market_status),
    Route("/api/indices", indices),
    Route("/api/stock/{code}", stock),
    Route("/api/history/{code}", history),
    Route("/api/analyze", analyze, methods=["POST"]),
    Route("/api/portfolio", portfolio),
    Route("/api/orders", orders),
    Route("/api/orders/stats", order_stats),
    Route("/api/reconcile", reconcile),
    Route("/api/order", order, methods=["POST"]),
    Route("/api/hot-stocks", hot_stocks),
    Route("/api/signal", signal, methods=["POST"]),
    Route("/api/bot-model", bot_model_get),
    Route("/api/bot-model/set", bot_model_set, methods=["POST"]),
    Route("/api/research/status", research_status),
    Route("/api/strategy-params", strategy_params_get),
]

static_path = Path(__file__).parent.parent / "front" / "dist"

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        is_index = path in ("", ".", "index.html") or path.endswith("/index.html")
        cache_control = "public, max-age=31536000, immutable" if path.startswith("assets/") else (
            "no-cache, must-revalidate" if is_index else "public, max-age=300, must-revalidate"
        )
        etag = None
        try:
            full_path, stat_result = self.lookup_path(path)
            full_path = Path(full_path)
            if full_path.is_dir():
                index_path = full_path / "index.html"
                if index_path.is_file():
                    stat_result = index_path.stat()
            if stat_result is not None:
                etag = f'"{stat_result.st_mtime_ns:x}-{stat_result.st_size:x}"'
        except Exception:
            etag = None
        if etag and Headers(scope=scope).get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = cache_control
        if etag:
            response.headers["ETag"] = etag
        return response

static_routes = [Mount("/", app=NoCacheStaticFiles(directory=str(static_path), html=True), name="static")]
all_routes = routes + static_routes


def _warmup_ai_sync():
    """进程启动后预热：模型列表 + 真正把默认模型换入显存。

    只打 /v1/models 不会加载权重，首个 /api/analyze 仍要付 15-20s 冷加载。
    这里主动发一次 1-token 空转请求，把冷加载挪到进程启动阶段，
    首屏分析直接走热路径（实测冷 17.1s → 热 0.28s）。
    """
    try:
        _fetch_remote_models(force=True)
    except Exception as e:
        print(f"[warmup] 模型列表预热失败: {e}", flush=True)
    try:
        client = get_client()
        ok = client.is_alive()
        print(f"[warmup] oMLX 可用: {ok}", flush=True)
        if not ok:
            return
        t0 = time.monotonic()
        client.chat(
            [{"role": "user", "content": "ping"}],
            temperature=0.0,
            max_tokens=1,
            timeout=_WARMUP_TIMEOUT,
        )
        print(f"[warmup] 模型 {client.model} 已换入显存（{time.monotonic() - t0:.1f}s），首屏分析走热路径", flush=True)
    except Exception as e:
        print(f"[warmup] oMLX 预热失败: {e}", flush=True)


async def _warmup_ai_async():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _warmup_ai_sync)


@asynccontextmanager
async def lifespan(app):
    warmup_task = asyncio.create_task(_warmup_ai_async())
    yield
    if not warmup_task.done():
        warmup_task.cancel()
    with suppress(asyncio.CancelledError):
        await warmup_task


app = Starlette(routes=all_routes, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1024)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5168, log_level="warning")
from simulation_broker import BUY_FEE_RATE, SELL_FEE_RATE

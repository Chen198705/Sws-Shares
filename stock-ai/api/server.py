import sys, os
from pathlib import Path
from datetime import datetime, date
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
import uvicorn, json

sys.path.insert(0, str(Path(__file__).parent))

from market_data import get_all_indices, get_stock_realtime, get_stock_history, calc_indicators
from ai_client import OllamaClient, analyze_with_fallback, get_client
from rule_engine import analyze as rule_analyze
from broker_adapter import get_broker
from trader import get_trading_status
from strategy_store import get_strategy_summary
from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL


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
    weekday = now.weekday()
    time_str = now.strftime("%H%M")
    is_weekend = weekday >= 5
    is_trading_hours = ("0930" <= time_str <= "1130") or ("1300" <= time_str <= "1500")
    is_open = not is_weekend and is_trading_hours
    if is_open:
        return True, "交易中"
    elif is_weekend:
        next_day = now + datetime.timedelta(days=1)
        if next_day.weekday() == 6:
            next_day += datetime.timedelta(days=1)
        return False, f"休市中 · 下个交易日 {next_day.strftime('%m/%d %A')}"
    elif time_str < "0930":
        return False, "等待开盘 · 09:30 开始交易"
    elif time_str < "1300":
        return False, "午间休市 · 13:00 恢复交易"
    else:
        return False, "今日已收盘"


def serialize_positions(positions):
    return [
        {"stock_code": p.stock_code, "stock_name": getattr(p, "stock_name", p.stock_code),
         "volume": p.volume, "avg_cost": getattr(p, "avg_cost", 0), "current_price": getattr(p, "current_price", 0),
         "unrealized_pnl": getattr(p, "unrealized_pnl", 0), "pnl_ratio": getattr(p, "pnl_ratio", 0), "horizon": getattr(p, "horizon", "medium")}
        for p in positions
    ]


async def health(request):
    return SafeJSONResponse({"status": "ok", "ai": get_client().is_alive(), "model": get_client().model})

async def models_list(request):
    try:
        import requests as _req
        r = _req.get(OLLAMA_BASE_URL + "/v1/models", headers={"Authorization": "Bearer " + OLLAMA_API_KEY}, timeout=10)
        r.raise_for_status()
        all_models = [m["id"] for m in r.json().get("data", [])]
        # 非通用 chat LLM：Embedding / OCR / Whisper / ASR / TTS / Rerank /
        # Dflash（推测解码架构）/ MTP（多 token 预测变体，如 MTPLX）
        llm_exclude = ["embedding", "bge-", "ocr", "whisper", "asr", "tts", "rerank", "dflash", "mtp"]
        model_list = [m for m in all_models if not any(e in m.lower() for e in llm_exclude)]
        # 兜底：当前在用模型始终保留在列表里，避免被规则误判后下拉里看不到它
        current = get_client().model
        if current and current in all_models and current not in model_list:
            model_list.insert(0, current)
        return JSONResponse({"models": model_list, "current": current})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def model_switch(request):
    try:
        body = await request.json()
        model = body.get("model", "").strip()
        if not model:
            return JSONResponse({"error": "model required"}, status_code=400)
        get_client().set_model(model)
        return JSONResponse({"ok": True, "model": model})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def market_status(request):
    open_, msg = is_market_open()
    return SafeJSONResponse({"open": open_, "message": msg})

async def indices(request):
    try:
        return SafeJSONResponse(get_all_indices())
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

async def stock(request):
    code = request.path_params.get("code", "")
    try:
        return SafeJSONResponse(get_stock_realtime(code))
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

async def history(request):
    code = request.path_params.get("code", "")
    days = int(request.query_params.get("days", 240))
    freq = request.query_params.get("freq", "day")
    try:
        df = get_stock_history(code, days, freq)
        ind = calc_indicators(df)
        if df is None or df.empty:
            return SafeJSONResponse({"error": "数据不足"}, status_code=400)
        return SafeJSONResponse({"history": df[["date","open","high","low","close","volume"]].to_dict(orient="records"), "indicators": ind})
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

async def analyze(request):
    try:
        body = await request.json()
    except:
        return SafeJSONResponse({"error": "invalid body"}, status_code=400)
    code = body.get("code", "").strip()
    if not code:
        return SafeJSONResponse({"error": "股票代码不能为空"}, status_code=400)
    try:
        stock_data = get_stock_realtime(code)
        if "错误" in stock_data:
            return SafeJSONResponse({"error": stock_data["错误"]}, status_code=400)
        df = get_stock_history(code)
        ind = calc_indicators(df)
        avg_pct = 0
        try:
            idx = get_all_indices()
            vals = [d.get("涨跌幅", 0) for d in idx.values() if "错误" not in d]
            avg_pct = sum(vals) / max(len(vals), 1)
        except:
            pass
        analysis_text, action, used_ai, horizon = analyze_with_fallback(stock_data, ind, avg_pct)
        return SafeJSONResponse({
            "analysis": analysis_text, "action": action, "used_ai": used_ai, "horizon": horizon,
            "stock": stock_data, "indicators": ind,
        })
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

async def portfolio(request):
    try:
        status = get_trading_status()
        status["positions"] = serialize_positions(status.get("positions", []))
        status["recent_orders"] = [
            {"id": o.order_id, "code": o.stock_code, "direction": o.direction,
             "price": o.price, "volume": o.volume, "status": o.status,
             "filled_price": getattr(o, "filled_price", o.price),
             "stock_name": getattr(o, "stock_name", ""),
             "pnl": getattr(o, "pnl", 0),
             "horizon": getattr(o, "horizon", "medium"),
             "time": str(o.created_at) if o.created_at else ""}
            for o in status.get("recent_orders", [])
        ]
        return SafeJSONResponse(status)
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)

async def orders(request):
    try:
        broker = get_broker()
        return SafeJSONResponse({"orders": [
            {"id": o.order_id, "code": o.stock_code, "direction": o.direction,
             "price": o.price, "volume": o.volume, "status": o.status,
             "filled_price": getattr(o, "filled_price", o.price),
             "stock_name": getattr(o, "stock_name", ""),
             "pnl": getattr(o, "pnl", 0),
             "horizon": getattr(o, "horizon", "medium"),
             "time": str(o.created_at) if o.created_at else ""}
            for o in broker.get_orders(20)
        ]})
    except Exception as e:
        return SafeJSONResponse({"error": str(e), "orders": []}, status_code=500)

async def order_stats(request):
    try:
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

        return SafeJSONResponse({
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
        })
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
        broker = get_broker()
        code = body.get("code", "").strip()
        direction = body.get("direction", "buy")
        volume = int(body.get("volume", 100))
        stock = get_stock_realtime(code)
        price = stock.get("最新价", 0)
        if direction == "buy":
            o = broker.buy(code, volume, price)
        else:
            o = broker.sell(code, volume, price)
        return SafeJSONResponse({"success": o.status == "filled", "order": {"id": o.order_id, "code": o.stock_code, "direction": o.direction, "price": o.filled_price, "volume": o.volume, "status": o.status}})
    except Exception as e:
        return SafeJSONResponse({"success": False, "error": str(e)}, status_code=500)

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

async def signal(request):
    try:
        body = await request.json()
    except:
        return SafeJSONResponse({"error": "invalid body"}, status_code=400)
    code = body.get("code", "").strip()
    if not code:
        return SafeJSONResponse({"error": "code empty"}, status_code=400)
    try:
        stock_data = get_stock_realtime(code)
        if "错误" in stock_data:
            return SafeJSONResponse({"error": stock_data["错误"]}, status_code=400)
        df = get_stock_history(code)
        ind = calc_indicators(df)
        rule_text, rule_action = rule_analyze(stock_data, ind, 0)
        return SafeJSONResponse({"text": rule_text, "action": rule_action, "period": "medium", "reason": rule_text})
    except Exception as e:
        return SafeJSONResponse({"error": str(e)}, status_code=500)


# ── 沈万三模型配置 ─────────────────────────────────────────────
BOT_CONFIG_PATH = Path(__file__).parent / "bot_config.json"

def get_bot_config():
    if BOT_CONFIG_PATH.exists():
        return json.loads(BOT_CONFIG_PATH.read_text())
    return {"model": OLLAMA_MODEL}

def save_bot_config(cfg):
    BOT_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

async def bot_model_get(request):
    cfg = get_bot_config()
    return SafeJSONResponse({"model": cfg["model"]})

async def bot_model_set(request):
    try:
        body = await request.json()
    except:
        return SafeJSONResponse({"error": "invalid body"}, status_code=400)
    model = body.get("model", "").strip()
    if not model:
        return SafeJSONResponse({"error": "model required"}, status_code=400)
    cfg = get_bot_config()
    cfg["model"] = model
    save_bot_config(cfg)
    get_client().set_model(model)
    return SafeJSONResponse({"ok": True, "model": model})


async def research_status(request):
    """研究层只读状态：参数契约 + 平仓归因 + 策略汇总。"""
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
    return SafeJSONResponse(payload)


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
    Route("/api/order", order, methods=["POST"]),
    Route("/api/hot-stocks", hot_stocks),
    Route("/api/signal", signal, methods=["POST"]),
    Route("/api/bot-model", bot_model_get),
    Route("/api/bot-model/set", bot_model_set, methods=["POST"]),
    Route("/api/research/status", research_status),
]

static_path = Path(__file__).parent.parent / "front" / "dist"

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

static_routes = [Mount("/", app=NoCacheStaticFiles(directory=str(static_path), html=True), name="static")]
all_routes = routes + static_routes

app = Starlette(routes=all_routes)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1024)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5168, log_level="warning")

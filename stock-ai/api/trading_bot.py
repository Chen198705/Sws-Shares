#!/usr/bin/env python3
"""自动操盘机器人 - 集成策略存储、自动迭代、KDJ/成交量/换手率/strategy_type"""
import sys, os, json, time, sqlite3, functools, re
print = functools.partial(print, flush=True)
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread, Event

sys.path.insert(0, str(Path(__file__).parent))
from market_data import (
    get_all_indices, get_stock_realtime, get_stock_history,
    calc_indicators, get_turnover_rate, build_entry_indicators,
)
from ai_client import OllamaClient
from broker_adapter import get_broker
from trader import get_trading_status
import stock_report
import json as _json

# Read bot model from bot_config.json
_BOT_CONFIG_PATH = Path(__file__).parent / "bot_config.json"
_DEFAULT_MODEL = "Qwen3.6-35B-A3B-4bit"  # hardcoded default

def _get_bot_model():
    if _BOT_CONFIG_PATH.exists():
        return _json.loads(_BOT_CONFIG_PATH.read_text()).get("model", _DEFAULT_MODEL)
    return _DEFAULT_MODEL

BOT_MODEL = _get_bot_model()
from strategy_store import (
    init_schema as init_strategy_schema, load_params,
    log_attribution, close_attribution, should_iterate, get_stop_take,
)
from iteration_engine import run_iteration
from market_scanner import scan_market, log_scan_result

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
DB_PATH = LOG_DIR / "trading_log.db"

SCAN_INTERVAL = 30 * 60
MARKET_SCAN_INTERVAL = 30 * 60  # 全市场扫描间隔


def init_db():
    c = sqlite3.connect(str(DB_PATH))
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, code TEXT, direction TEXT, strategy_type TEXT DEFAULT '中线',
        price REAL, volume INTEGER, pnl REAL, reason TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, code TEXT, action TEXT, strategy_type TEXT,
        confidence INTEGER, price REAL, executed INTEGER DEFAULT 0, analysis TEXT)""")
    try:
        c.execute("ALTER TABLE trades ADD COLUMN strategy_type TEXT DEFAULT '中线'")
    except Exception:
        pass
    c.commit()
    c.close()


def log_trade(code, direction, price, volume, pnl=0.0, reason="", strategy_type="中线"):
    c = sqlite3.connect(str(DB_PATH))
    c.execute(
        "INSERT INTO trades (ts,code,direction,strategy_type,price,volume,pnl,reason) VALUES (?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(), code, direction, strategy_type, price, volume, pnl, reason))
    c.commit()
    tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.close()
    return tid


def log_scan(code, action, confidence, price, analysis, strategy_type="中线", executed=0):
    c = sqlite3.connect(str(DB_PATH))
    c.execute(
        "INSERT INTO scan_log (ts,code,action,strategy_type,confidence,price,analysis,executed) VALUES (?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(), code, action, strategy_type, confidence, price, analysis[:500], executed))
    c.commit()
    c.close()


def is_trading_day():
    return datetime.now().weekday() < 5

def is_trading_hours():
    t = datetime.now().strftime("%H%M")
    return ("0930" <= t <= "1130") or ("1300" <= t <= "1500")

def seconds_to_open():
    now = datetime.now()
    t = now.strftime("%H%M")
    if "0930" <= t <= "1130" or "1300" <= t <= "1500":
        return 0
    elif t < "0930":
        target = now.replace(hour=9, minute=30, second=0)
    elif t < "1300":
        target = now.replace(hour=13, minute=0, second=0)
    else:
        target = now.replace(hour=9, minute=30, second=0) + timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
    return max(0, int((target - now).total_seconds()))


def run_scheduled_reports(stop_event):
    """交易日 11:30 / 15:05 由常驻进程内定时器发飞书报告，替代不可靠的 cron"""
    REPORT_TIMES = [
        ("11:30", "上午盘"),
        ("15:05", "下午盘"),
    ]
    reported = set()
    while not stop_event.is_set():
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        hm = now.strftime("%H:%M")
        if is_trading_day():
            for target, period in REPORT_TIMES:
                key = f"{today}:{period}"
                if key in reported:
                    continue
                if hm < target:
                    continue
                # 到点后允许 10 分钟补发窗口，防止进程刚好在忙或重启
                if hm > f"{int(target[:2]):02d}:{int(target[3:]) + 10:02d}":
                    continue
                print(f"[定时报告] {period} 报告触发 {now:%Y-%m-%d %H:%M:%S}")
                ok = False
                for attempt in range(1, 4):
                    try:
                        ok = stock_report.report()
                    except Exception as e:
                        print(f"[定时报告] {period} 报告异常: {e}")
                    if ok:
                        break
                    print(f"[定时报告] {period} 第 {attempt} 次失败，60 秒后重试")
                    if stop_event.wait(60):
                        return
                    now = datetime.now()
                    hm = now.strftime("%H:%M")
                    if hm > f"{int(target[:2]):02d}:{int(target[3:]) + 10:02d}":
                        break
                reported.add(key)
                print(f"[定时报告] {period} {'推送成功' if ok else '推送失败，已记入日志'}")
        if stop_event.wait(20):
            return


def get_market_context():
    try:
        idx = get_all_indices()
        vals = [(n, d["涨跌幅"]) for n, d in idx.items() if "错误" not in d]
        if vals:
            avg = sum(v for _, v in vals) / len(vals)
            lines = ", ".join(f"{n}{v:+.2f}%" for n, v in vals[:3])
            return f"大盘均值 {avg:+.2f}%（{lines}）"
    except:
        pass
    return "大盘数据获取失败"


def _market_strength(mkt: str):
    """把大盘描述解析为强弱档位，用于动态分配短/中/长线权重"""
    m = re.search(r"([+-]?\d+(?:\.\d+)?)%", mkt or "")
    if not m:
        return 0.0
    return float(m.group(1))


def _horizon_weights(mkt: str):
    """按大盘强弱给出短线/中线/长线参考权重，强市偏短、弱市偏长"""
    return {
        "强": "短线 60%、中线 30%、长线 10%",
        "偏强": "短线 50%、中线 40%、长线 10%",
        "震荡": "短线 40%、中线 50%、长线 10%",
        "偏弱": "短线 30%、中线 40%、长线 30%",
    }[_market_regime(mkt)]


def _market_regime(mkt: str):
    avg = _market_strength(mkt)
    if avg >= 0.8:
        return "强"
    if avg >= 0.2:
        return "偏强"
    if avg >= -0.3:
        return "震荡"
    return "偏弱"


# 每个档位 10 个周期槽位，轮转分配确保短/中/长线都能出现
_HORIZON_BUCKETS = {
    "强": ["短线", "中线", "短线", "短线", "长线", "短线", "中线", "短线", "中线", "短线"],
    "偏强": ["短线", "中线", "短线", "中线", "短线", "中线", "长线", "短线", "中线", "短线"],
    "震荡": ["短线", "中线", "短线", "中线", "中线", "长线", "短线", "中线", "短线", "中线"],
    "偏弱": ["短线", "长线", "中线", "长线", "短线", "中线", "长线", "短线", "中线", "中线"],
}
_horizon_cursor = 0


def _allocate_horizon(mkt: str):
    """按大盘档位轮转分配周期，避免模型清一色输出中线"""
    global _horizon_cursor
    regime = _market_regime(mkt)
    buckets = _HORIZON_BUCKETS[regime]
    label = buckets[_horizon_cursor % len(buckets)]
    _horizon_cursor += 1
    return label, regime, _horizon_weights(mkt)


def _parse_bot_direction(text: str) -> str:
    """优先解析模型明确给出的“操作方向”，避免正文里的风险词导致误判。"""
    m = re.search(r"操作方向\s*[：:]?\s*(买入|卖出|观望|持有|加仓|减仓|清仓)", text or "")
    if m:
        d = m.group(1)
        if d in ("卖出", "减仓", "清仓"):
            return "sell"
        if d in ("买入", "加仓"):
            return "buy"
        return "hold"

    def has_advice(phrase: str) -> bool:
        return phrase in text and f"不{phrase}" not in text and f"不要{phrase}" not in text

    if has_advice("建议卖出") or any(has_advice(k) for k in ("卖出信号", "建议清仓", "清仓回避", "建议减仓", "止盈离场", "止盈卖出")):
        return "sell"
    if has_advice("建议买入") or any(has_advice(k) for k in ("买入信号", "建议加仓", "建议低吸", "轻仓买入")):
        return "buy"
    return "hold"


def trigger_iteration():
    ok, count = should_iterate()
    if ok:
        print(f"[迭代] 已积累 {count} 笔平仓，触发策略复盘...")
        Thread(target=run_iteration, daemon=True).start()


_HORIZON_LABELS = {"short": "短线", "medium": "中线", "long": "长线"}


def _horizon_label(value) -> str:
    """把 broker 的英文周期名转成中文，中文原样返回"""
    if not value:
        return "中线"
    return _HORIZON_LABELS.get(str(value).strip().lower(), value)


def check_positions(client, broker):
    params = load_params()
    status = get_trading_status()
    action_taken = False
    for pos in status.get("positions", []):
        code = pos.stock_code
        entry = pos.avg_cost or 0
        cur_p = pos.current_price or 0
        vol   = pos.volume or 0
        stype = _horizon_label(getattr(pos, 'horizon', '中线'))
        if entry <= 0 or cur_p <= 0:
            continue
        pnl_pct = (cur_p - entry) / entry
        sl, tp = get_stop_take(stype, params)
        if pnl_pct <= sl:
            reason = f"触发止损（{pnl_pct*100:.1f}%）[{stype}]"
            try:
                order = broker.sell(code, vol, cur_p)
                if order.status == "filled":
                    pnl = (order.filled_price - entry) * vol
                    tid = log_trade(code, "sell", order.filled_price, vol, pnl, reason, stype)
                    close_attribution(tid, pnl, reason)
                    print(f"  [{code}] {reason}，盈亏 ¥{pnl:+.2f}")
                    action_taken = True
            except Exception as e:
                print(f"  [{code}] 止损失败: {e}")
        elif pnl_pct >= tp:
            reason = f"触发止盈（+{pnl_pct*100:.1f}%）[{stype}]"
            try:
                order = broker.sell(code, vol, cur_p)
                if order.status == "filled":
                    pnl = (order.filled_price - entry) * vol
                    tid = log_trade(code, "sell", order.filled_price, vol, pnl, reason, stype)
                    close_attribution(tid, pnl, reason)
                    print(f"  [{code}] {reason}，盈亏 ¥{pnl:+.2f}")
                    action_taken = True
            except Exception as e:
                print(f"  [{code}] 止盈失败: {e}")
        else:
            print(f"  [{code}] 持仓[{stype}] 成本¥{entry:.2f} 现价¥{cur_p:.2f} {pnl_pct*100:+.1f}%")
    if action_taken:
        trigger_iteration()


def analyze_and_decide(client, broker, code):
    try:
        stock = get_stock_realtime(code)
        if "错误" in stock:
            return None
        hist = get_stock_history(code, days=60)
        ind = calc_indicators(hist)
        if not ind:
            return None
        # 换手率单独请求
        turnover = get_turnover_rate(code)
        mkt = get_market_context()
        horizon_label, regime, weights = _allocate_horizon(mkt)
        print(f"  [{code}] 周期分配: {horizon_label}（大盘{regime}，权重 {weights}）")

        if client.is_alive():
            params = load_params()
            prompt = f"""股票：{stock.get('股票名', code)}（{code}）
当前价：{stock['最新价']} 涨跌幅：{stock['涨跌幅']:+.2f}%
今开={stock['今开']} 最高={stock['最高']} 最低={stock['最低']}
MA5={ind['MA5']:.2f} MA20={ind['MA20']:.2f} 均线多头={ind['均线多头']}
RSI(14)={ind['RSI(14)']:.1f} RSI状态={ind['RSI状态']}
MACD金叉={ind['MACD金叉']} MACD状态={ind['MACD状态']}
K={ind['K']:.1f} D={ind['D']:.1f} J={ind['J']:.1f} KDJ金叉={ind['KDJ金叉']} KDJ状态={ind['KDJ状态']}
量比={ind['量比']:.2f} 成交量状态={ind['成交量状态']} 换手率={turnover:.2f}%
大盘：{mkt}
当前大盘环境：{regime}（{weights}）
本只候选股的最终策略类型必须为：{horizon_label}
总资产约100万，短线止损{params.short_stop_loss*100:.0f}%止盈{params.short_take_profit*100:.0f}%，中线止损{params.mid_stop_loss*100:.0f}%止盈{params.mid_take_profit*100:.0f}%，长线止损{params.long_stop_loss*100:.0f}%止盈{params.long_take_profit*100:.0f}%

请严格判断，给出：
1. 策略类型：直接输出“{horizon_label}”，不要输出其他周期
2. 操作方向（买入/卖出/观望）
3. 仓位（总资产百分比，如20%）
4. 止损/止盈价（根据策略类型）
5. 操作理由（1-2句话）

买入条件：
- 短线：RSI<40 且 (KDJ金叉 或 放量上涨 量比>1.5)
- 中线：RSI<55 且 (均线多头 且 MACD金叉)
- 长线：RSI<65 且 均线多头 且 换手率>1%
卖出条件：RSI>70 或 均线死叉 或 KDJ高位死叉"""
            analysis = client.chat([
                {"role": "system", "content": "你是一个严格的A股量化交易员，禁止废话。"},
                {"role": "user", "content": prompt}
            ], temperature=0.2)
        else:
            return None

        # 周期由大盘环境分配，模型必须遵循，避免清一色输出中线
        stype = horizon_label

        action = _parse_bot_direction(analysis)
        if action == "buy":
            pos_pct = 0.2
            m = re.search(r"(\d{1,3})%", analysis)
            if m:
                pos_pct = int(m.group(1)) / 100
        elif action == "sell":
            pos_pct = 0.5
        else:
            pos_pct = 0

        ei = build_entry_indicators(stock, ind, turnover)

        return {
            "action": action, "position_ratio": pos_pct,
            "price": stock["最新价"], "analysis": analysis,
            "market_context": mkt, "entry_indicators": ei,
            "strategy_type": stype, "horizon": {"短线": "short", "中线": "medium", "长线": "long"}[stype],
        }
    except Exception as e:
        print(f"  [{code}] 分析失败: {e}")
        return None


def execute_decision(decision, broker):
    code   = decision["code"]
    action = decision["action"]
    price  = decision["price"]
    stype  = decision.get("strategy_type", "中线")
    horizon = decision.get("horizon", {"短线": "short", "中线": "medium", "长线": "long"}.get(stype, "medium"))
    params = load_params()
    bal    = broker.get_balance()
    total  = bal["total_assets"]

    if action == "buy":
        max_new = total * params.max_total_position - bal["market_value"]
        if max_new <= 0:
            print(f"  [{code}] 总仓位已达上限")
            return
        vol = int(min(total * decision["position_ratio"], max_new) / price / 100) * 100
        if vol < 100:
            print(f"  [{code}] 买入金额过小")
            return
        try:
            order = broker.buy(code, vol, price, horizon=horizon)
            if order.status == "filled":
                tid = log_trade(code, "buy", order.filled_price, vol, reason=f"AI建仓[{stype}]", strategy_type=stype)
                log_attribution(tid, ai_reason=decision.get("analysis","")[:200],
                                market_context=decision.get("market_context",""),
                                entry_indicators=decision.get("entry_indicators",""),
                                strategy_type=stype)
                log_scan(code, "buy", 70, order.filled_price, decision.get("analysis","")[:500], stype, 1)
                print(f"  [{code}] 买入成交 {vol}股 @¥{order.filled_price:.2f} [{stype}]")
                trigger_iteration()
        except Exception as e:
            print(f"  [{code}] 买入失败: {e}")

    elif action == "sell":
        status = get_trading_status()
        for pos in status.get("positions", []):
            if pos.stock_code == code:
                vol = pos.volume
                avg = pos.avg_cost
                try:
                    order = broker.sell(code, vol, price)
                    if order.status == "filled":
                        pnl = (order.filled_price - avg) * vol
                        tid = log_trade(code, "sell", order.filled_price, vol, pnl, "AI信号卖出", stype)
                        close_attribution(tid, pnl, "AI信号卖出")
                        log_scan(code, "sell", 70, order.filled_price, "AI信号卖出", stype, 1)
                        print(f"  [{code}] 卖出成交 {vol}股 @¥{order.filled_price:.2f} 盈亏 ¥{pnl:+.2f}")
                        trigger_iteration()
                except Exception as e:
                    print(f"  [{code}] 卖出失败: {e}")
                break


def run_scan(client, broker, code):
    d = analyze_and_decide(client, broker, code)
    if d and d["action"] != "hold":
        d["code"] = code
        act = d["action"] if d else "N/A"; print("  [", code, "] AI判断:", act)
        execute_decision(d, broker)




def startup_warmup(client, broker):
    """启动时检查网络和模型，完成后做默认股预分析"""
    import urllib.request
    print('\n[启动预热] 检查网络连通性...')
    net_ok = False
    try:
        urllib.request.urlopen('https://www.baidu.com', timeout=5)
        net_ok = True
        print('[启动预热] 网络: OK')
    except Exception:
        print('[启动预热] 网络: 离线 (跳过预热)')

    print('[启动预热] 检查模型服务...')
    model_ok = client.is_alive()
    if model_ok:
        print(f'[启动预热] 模型 ({BOT_MODEL}): 在线')
    else:
        print(f'[启动预热] 模型 ({BOT_MODEL}): 离线 (跳过预热)')

    if net_ok and model_ok:
        # 选一只代表性股票做预热（白酒龙头茅台）
        warmup_code = '600519'
        print(f'[启动预热] 预热分析 {warmup_code}...')
        try:
            from market_data import get_stock_realtime, get_stock_history, calc_indicators
            stock = get_stock_realtime(warmup_code)
            hist = get_stock_history(warmup_code, days=20)
            ind = calc_indicators(hist) or {}
            price = stock.get('最新价', 0)
            chg = stock.get('涨跌幅', 0)
            prompt = f'股票{stock.get("股票名",warmup_code)}({warmup_code})现价¥{price}，涨跌幅{chg:+.2f}%，MA5={ind.get("MA5","N/A")}，RSI={ind.get("RSI(14)","N/A")}。请简要判断当前适合操作的方向（买入/卖出/观望）。'
            resp = client.chat([
                {"role":"system","content":"你是一名专业的A股交易员，回答简洁直接，只输出判断结果和理由。"},
                {"role":"user","content":prompt}
            ], temperature=0.3, max_tokens=200)
            print(f'[启动预热] 预热完成: {resp[:100].strip()}')
        except Exception as e:
            print(f'[启动预热] 预热分析失败: {e}')
    print('[启动预热] 完毕，进入主循环')
    return net_ok and model_ok

def main_loop(stop_event):
    client = OllamaClient(model=BOT_MODEL)
    broker = get_broker()
    while not stop_event.is_set():
        if startup_warmup(client, broker):
            break
        print("[启动预热] 未通过，60 秒后重试...")
        for _ in range(60):
            if stop_event.is_set():
                return
            time.sleep(1)
    init_strategy_schema()
    params = load_params()
    print(f"自动操盘机器人启动 · 模型: {BOT_MODEL}")
    print("全市场主力资金扫描启动")
    print(f"短线止损{params.short_stop_loss*100:.0f}%止盈{params.short_take_profit*100:.0f}%  中线止损{params.mid_stop_loss*100:.0f}%止盈{params.mid_take_profit*100:.0f}%  长线止损{params.long_stop_loss*100:.0f}%止盈{params.long_take_profit*100:.0f}% 扫描间隔 {SCAN_INTERVAL//60}分钟")
    print(f"迭代触发: 满 {params.closed_trades_threshold} 笔平仓")
    Thread(target=run_scheduled_reports, args=(stop_event,), daemon=True).start()
    while not stop_event.is_set():
        ts = datetime.now().strftime("%H:%M")
        if not is_trading_day():
            print(f"[{ts}] 非交易日，休眠...")
            time.sleep(seconds_to_open())
            continue
        if not is_trading_hours():
            secs = seconds_to_open()
            print(f"[{ts}] 非交易时段，{secs//3600}h{secs%3600//60}m 后开盘，休眠...")
            time.sleep(min(secs, 1800))
            continue
        print(f"\n[{'='*50}]")
        print(f"[{ts}] 开始扫描...")
        params = load_params()
        print("检查持仓...")
        check_positions(client, broker)
        print("全市场扫描选股...")
        candidates = scan_market()
        log_scan_result(candidates)
        
        # 已持仓的股票不再重复买入
        status = get_trading_status()
        held = {pos.stock_code for pos in status.get("positions", [])}
        
        print("分析候选股票...")
        for cand in candidates[:5]:  # 最多分析TOP5
            code = cand["code"]
            if code in held:
                print(f"  [{code}] 已在持仓，跳过")
                continue
            print(f"  分析候选: {cand['name']}({code}) 分数={cand['score']}")
            run_scan(client, broker, code)
            time.sleep(5)  # 候选股分析间隔稍长
        bal = get_trading_status()["balance"]
        print(f"\n账户: 总资产 ¥{bal['total_assets']:,.0f} 现金 ¥{bal['cash']:,.0f} 持仓 ¥{bal['market_value']:,.0f}")
        for pos in get_trading_status().get("positions", []):
            pct = (pos.current_price - pos.avg_cost) / max(pos.avg_cost or 1,1) * 100
            st = _horizon_label(getattr(pos, 'horizon', '中线'))
            print(f"  {pos.stock_code} {pos.volume}股 成本¥{pos.avg_cost:.2f} 现价¥{pos.current_price:.2f} {pct:+.1f}% [{st}]")
        print(f"\n[{ts}] 本轮完成，等待 {SCAN_INTERVAL//60} 分钟...")
        for _ in range(SCAN_INTERVAL):
            if stop_event.is_set():
                break
            time.sleep(1)


if __name__ == "__main__":
    init_db()
    stop_event = Event()
    try:
        main_loop(stop_event)
    except KeyboardInterrupt:
        print("\n机器人已停止")
        stop_event.set()

#!/usr/bin/env python3
"""自动操盘机器人 - 集成策略存储、自动迭代、KDJ/成交量/换手率/strategy_type"""
import sys, os, json, time, sqlite3, functools, re
from typing import Optional
print = functools.partial(print, flush=True)
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread, Event

sys.path.insert(0, str(Path(__file__).parent))
from market_data import (
    get_all_indices, get_stock_realtime, get_stock_history,
    calc_indicators, get_turnover_rate, build_entry_indicators,
    calc_volatility_profile,
)
from ai_client import OMLXClient, _policy_overlay_text
from broker_adapter import get_broker
from trader import get_trading_status
import stock_report
import json as _json

# Read bot model from bot_config.json
_BOT_CONFIG_PATH = Path(__file__).parent / "bot_config.json"
_DEFAULT_MODEL = "Qwen3.6-35B-A3B-4bit"  # hardcoded default

def _get_bot_model():
    """读取 bot_config.json 的模型；文件缺失 / 半截 JSON 时回落到默认模型。"""
    try:
        if _BOT_CONFIG_PATH.exists():
            model = _json.loads(_BOT_CONFIG_PATH.read_text()).get("model")
            if model and model.strip():
                return model.strip()
    except Exception:
        pass
    return _DEFAULT_MODEL

BOT_MODEL = _get_bot_model()


def sync_bot_model(client, current_config_model):
    """每轮检查 bot_config.json，模型变化时热切换主模型。

    返回当前生效的配置值。只比对配置文件（而不是 client.model），
    避免 fallback 提升导致的来回切换。
    """
    desired = _get_bot_model()
    if desired != current_config_model:
        print(f"[模型切换] 配置变更 {current_config_model} → {desired}，热切换主模型")
        client.set_model(desired)
    return desired
from strategy_store import (
    init_schema as init_strategy_schema, load_params,
    log_attribution, should_iterate, get_stop_take,
    get_effective_params, get_research_overlay,
    get_account_peak, update_account_peak, get_circuit_break_until, set_circuit_break,
    close_attribution_for_code,
    get_volatility_position_size,
)
from industry_map import sector_concentration_ok
from iteration_engine import run_iteration, iteration_running
from market_scanner import scan_market, log_scan_result
import market_calendar

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
DB_PATH = LOG_DIR / "trading_log.db"

POSITION_CHECK_INTERVAL = 5 * 60
MARKET_SCAN_INTERVAL = 30 * 60  # 全市场扫描间隔
SCAN_INTERVAL = POSITION_CHECK_INTERVAL  # 兼容别名，实际使用上面的常量


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
    # 持久化 trailing peak：进程重启不丢失峰值跟踪，避免回撤止盈失效
    c.execute("""CREATE TABLE IF NOT EXISTS trailing_peaks (
        code TEXT PRIMARY KEY,
        peak_pnl REAL NOT NULL,
        strategy_type TEXT DEFAULT '中线',
        updated_at TEXT NOT NULL)""")
    # AI 复评审计：保留最近一次 AI 复评结果，便于事后追溯
    c.execute("""CREATE TABLE IF NOT EXISTS ai_review_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, code TEXT, strategy_type TEXT,
        action TEXT, reason TEXT, indicators TEXT,
        pnl_pct REAL, atr_pct REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_sell_streak (
        code TEXT PRIMARY KEY,
        streak INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL)""")
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
    # 用 JQData 权威交易日历，避免工作日节假日（中秋/国庆）被误判为交易日
    return market_calendar.is_trading_day()

def is_trading_hours():
    t = datetime.now().strftime("%H%M")
    return ("0930" <= t <= "1130") or ("1300" <= t <= "1500")

def seconds_to_open():
    now = datetime.now()
    t = now.strftime("%H%M")
    if "0930" <= t <= "1130" or "1300" <= t <= "1500":
        return 0
    if is_trading_day() and t < "0930":
        target = now.replace(hour=9, minute=30, second=0)
    elif is_trading_day() and t < "1300":
        target = now.replace(hour=13, minute=0, second=0)
    else:
        # 非交易日或收盘后：跳到下一个真实交易日的 09:30
        nxt = market_calendar.next_trading_day(now.date())
        target = datetime.combine(nxt, datetime.min.time()).replace(hour=9, minute=30)
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
        "强": "短线 80%、中线 10%、长线 10%",
        "偏强": "短线 70%、中线 20%、长线 10%",
        "震荡": "短线 60%、中线 30%、长线 10%",
        "偏弱": "短线 50%、中线 30%、长线 20%",
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


def _research_regime_bucket() -> Optional[str]:
    """研究层 regime 优先；无契约时返回 None 走原大盘强弱逻辑。"""
    regime = (get_research_overlay().get("regime") or {}).get("state", "")
    if "牛" in regime:
        return "强"
    if "熊" in regime:
        return "偏弱"
    if "震荡" in regime:
        return "震荡"
    if "转换" in regime:
        return "偏弱"
    return None


# 每个档位 10 个周期槽位，轮转分配确保短/中/长线都能出现
_HORIZON_BUCKETS = {
    "强": ["短线", "短线", "短线", "短线", "短线", "短线", "短线", "短线", "中线", "长线"],
    "偏强": ["短线", "短线", "短线", "短线", "短线", "短线", "短线", "中线", "中线", "长线"],
    "震荡": ["短线", "短线", "短线", "短线", "短线", "短线", "中线", "中线", "中线", "长线"],
    "偏弱": ["短线", "短线", "短线", "短线", "短线", "中线", "中线", "中线", "长线", "长线"],
}
_horizon_cursor = 0


def _allocate_horizon(mkt: str):
    """按大盘档位轮转分配周期，避免模型清一色输出中线"""
    global _horizon_cursor
    regime = _research_regime_bucket() or _market_regime(mkt)
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
    obs_cnt, rev_cnt, obs_ready, rev_ready = should_iterate()
    if not obs_ready and not rev_ready:
        return
    if iteration_running():
        print(f"[迭代] 已积累观察 {obs_cnt} 笔/复核 {rev_cnt} 笔，但已有迭代进程在运行，跳过本次")
        return
    stage = "复核调参" if rev_ready else "观察"
    print(f"[迭代] 观察 {obs_cnt} 笔、复核 {rev_cnt} 笔，触发{stage}...")
    # 用独立子进程 + 显式 PYTHONPATH，避免线程继承环境的静默失败
    import subprocess
    api_dir = Path(__file__).parent
    env = {**os.environ}
    site_pkgs = api_dir / ".venv" / "lib" / "python3.9" / "site-packages"
    env["PYTHONPATH"] = os.pathsep.join([
        str(site_pkgs), str(api_dir),
        env.get("PYTHONPATH", ""),
    ]).strip(os.pathsep)
    log_file = LOG_DIR / "iteration_run.log"
    log_file.parent.mkdir(exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        subprocess.Popen(
            [str(sys.executable), str(api_dir / "iteration_engine.py")],
            cwd=str(api_dir), env=env,
            stdout=f, stderr=subprocess.STDOUT,
        )


_HORIZON_LABELS = {"short": "短线", "medium": "中线", "long": "长线"}


def _horizon_label(value) -> str:
    """把 broker 的英文周期名转成中文，中文原样返回"""
    if not value:
        return "中线"
    return _HORIZON_LABELS.get(str(value).strip().lower(), value)


_trailing_peak: dict = {}
_last_ai_review: dict = {}  # code -> datetime（最近一次 AI 复评的时间）
_ai_sell_streak: dict = {}  # code -> int（连续 AI sell 次数；用于反噪声）


def _load_trailing_peak(code: str) -> Optional[float]:
    """从 SQLite 读取指定股票的 trailing peak；用于进程重启后恢复峰值跟踪。"""
    try:
        c = sqlite3.connect(str(DB_PATH))
        row = c.execute("SELECT peak_pnl FROM trailing_peaks WHERE code=?", (code,)).fetchone()
        c.close()
        return float(row[0]) if row else None
    except Exception:
        return None


def _save_trailing_peak(code: str, peak_pnl: float, stype: str = "中线"):
    """持久化 trailing peak；进程重启不丢峰值，回撤止盈才能稳定生效。"""
    try:
        c = sqlite3.connect(str(DB_PATH))
        c.execute(
            "INSERT OR REPLACE INTO trailing_peaks (code, peak_pnl, strategy_type, updated_at) VALUES (?,?,?,?)",
            (code, float(peak_pnl), stype, datetime.now().isoformat()))
        c.commit()
        c.close()
    except Exception as e:
        print(f"  [_trailing_peak] 保存失败 {code}: {e}")


def _drop_trailing_peak(code: str):
    """平仓后清理 trailing peak 记录。"""
    try:
        c = sqlite3.connect(str(DB_PATH))
        c.execute("DELETE FROM trailing_peaks WHERE code=?", (code,))
        c.commit()
        c.close()
    except Exception:
        pass


def _load_ai_sell_streak(code: str) -> int:
    try:
        c = sqlite3.connect(str(DB_PATH))
        row = c.execute("SELECT streak FROM ai_sell_streak WHERE code=?", (code,)).fetchone()
        c.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _save_ai_sell_streak(code: str, streak: int):
    try:
        c = sqlite3.connect(str(DB_PATH))
        c.execute(
            "INSERT OR REPLACE INTO ai_sell_streak (code, streak, updated_at) VALUES (?,?,?)",
            (code, int(streak), datetime.now().isoformat()))
        c.commit()
        c.close()
    except Exception as e:
        print(f"  [_ai_sell_streak] 保存失败 {code}: {e}")


def _drop_ai_sell_streak(code: str):
    """平仓后清理 sell streak。"""
    try:
        c = sqlite3.connect(str(DB_PATH))
        c.execute("DELETE FROM ai_sell_streak WHERE code=?", (code,))
        c.commit()
        c.close()
    except Exception:
        pass


def _log_ai_review(code: str, stype: str, action: str, reason: str,
                    indicators: str, pnl_pct: float, atr_pct: float):
    try:
        c = sqlite3.connect(str(DB_PATH))
        c.execute(
            "INSERT INTO ai_review_log (ts,code,strategy_type,action,reason,indicators,pnl_pct,atr_pct)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), code, stype, action, reason[:200],
             indicators[:300], pnl_pct, atr_pct))
        c.commit()
        c.close()
    except Exception:
        pass


def _should_ai_review(code: str, pnl_pct: float, params) -> bool:
    """是否需要发起 AI 复评：距上次复评 ≥ 间隔 + 仅在浮亏较大时触发，避免无谓开销。"""
    interval = max(5, int(getattr(params, "ai_review_interval_min", 30))) * 60
    last = _last_ai_review.get(code)
    if last and (datetime.now() - last).total_seconds() < interval:
        return False
    return pnl_pct <= -float(getattr(params, "ai_review_min_pnl", -0.03))


def _ai_re_evaluate_position(client, broker, code: str, pos, params, atr_pct: float) -> str:
    """轻量级 AI 复评：只问"继续持有 vs 卖出"，避免冗长分析拖慢决策。
    返回 'sell' / 'hold' / 'skip'（skip 表示模型不可用，走规则）。"""
    if not client or not client.is_alive():
        return "skip"
    try:
        entry = pos.avg_cost or 0
        cur_p = pos.current_price or 0
        if entry <= 0 or cur_p <= 0:
            return "skip"
        pnl_pct = (cur_p - entry) / entry
        stype = _horizon_label(getattr(pos, 'horizon', '中线'))
        hist = get_stock_history(code, days=30)
        ind = calc_indicators(hist)
        vol = calc_volatility_profile(hist)
        ind_brief = ""
        if ind:
            ind_brief = (
                f"MA5={ind.get('MA5',0):.2f} MA20={ind.get('MA20',0):.2f} "
                f"均线多头={ind.get('均线多头','?')} RSI={ind.get('RSI(14)',0):.1f} "
                f"MACD状态={ind.get('MACD状态','?')} KDJ状态={ind.get('KDJ状态','?')}"
            )
        atr_str = f"{vol.get('atr_pct', 0)*100:.2f}%" if vol else "?"
        stop_loss, take_profit = get_stop_take(stype, params)
        prompt = f"""复评持仓 {code}（{stype}，入场 {entry:.2f}，现价 {cur_p:.2f}，浮盈 {pnl_pct*100:+.1f}%）
ATR%={atr_str}。指标: {ind_brief}
执行止损线={stop_loss*100:.1f}% / 止盈线={take_profit*100:.1f}%。
ATR% 仅用于风险画像和仓位约束，不覆盖当前周期的执行阈值。
请判断：这只票的趋势是否被破坏？只回答 JSON：{{"action":"sell|hold","reason":"一句话原因"}}"""
        text = client.chat([
            {"role": "system", "content": "你是严格量化交易员，专注判断趋势是否破坏。"},
            {"role": "user", "content": prompt},
        ], temperature=0.1)
        m = re.search(r'\{[^{}]*"action"[^{}]*\}', text or "", re.DOTALL)
        action = "hold"
        reason = ""
        if m:
            try:
                obj = _json.loads(m.group(0))
                action = str(obj.get("action", "hold")).lower().strip()
                reason = str(obj.get("reason", ""))[:200]
            except Exception:
                pass
        if action not in ("sell", "hold"):
            action = "hold"
        _last_ai_review[code] = datetime.now()
        _log_ai_review(code, stype, action, reason, ind_brief, pnl_pct, atr_pct or 0)
        return action
    except Exception as e:
        print(f"  [{code}] AI 复评失败: {e}")
        return "skip"


def _trailing_hit(stype: str, params, peak: float, pnl_pct: float) -> bool:
    """回撤止盈：短线/中线到达激活线后，从峰值回撤超过阈值即落袋；长线不启用"""
    if stype == "短线":
        return peak >= params.short_trailing_activate and pnl_pct <= peak - params.short_trailing_drawdown
    if stype == "中线":
        return peak >= params.mid_trailing_activate and pnl_pct <= peak - params.mid_trailing_drawdown
    return False


REBALANCE_TOLERANCE = 1.02  # 轻微超限不反复微调，避免无谓交易成本


def rebalance_oversized_positions(broker, params, skip_codes=None, status=None):
    """存量仓位再平衡：只减不增，把超过单票上限的历史仓位削回上限。

    背景：单票上限是 2026-08-16 之后才进执行层的，此前的存量仓位
    （如 600138 曾占总资产 ~28%）不会被新开仓检查拦截，需要单独的再平衡通道。

    约束：
    - 只减仓、不加仓；卖出量按 100 股整数倍向下取整
    - 单票上限 = min(max_position_size, 波动率自适应仓位上限)
    - 遵守 T+1：卖出量不超过 broker.sellable_volume()
    - 削减不足 100 股或超限幅度在 REBALANCE_TOLERANCE 内则跳过
    """
    skip_codes = set(skip_codes or ())
    status = status or get_trading_status()
    total = float((status.get("balance") or {}).get("total_assets") or 0)
    if total <= 0:
        return False
    acted = False
    for pos in status.get("positions", []):
        code = pos.stock_code
        if code in skip_codes:
            continue
        vol = int(pos.volume or 0)
        cur_p = float(pos.current_price or 0)
        entry = float(pos.avg_cost or 0)
        if vol <= 0 or cur_p <= 0:
            continue
        atr_pct = (calc_volatility_profile(get_stock_history(code, days=30)) or {}).get("atr_pct") or 0.0
        cap_pct = min(params.max_position_size, get_volatility_position_size(params, atr_pct))
        cap_value = total * cap_pct
        cur_value = vol * cur_p
        if cur_value <= cap_value * REBALANCE_TOLERANCE:
            continue
        sellable = int(broker.sellable_volume(code) or 0)
        if sellable <= 0:
            print(f"  [{code}] 再平衡跳过：T+1 闸口，今日买入当日不可卖")
            continue
        target_vol = int(cap_value / cur_p / 100) * 100
        sell_vol = min(vol - target_vol, sellable)
        sell_vol = int(sell_vol // 100) * 100
        if sell_vol < 100:
            continue
        stype = _horizon_label(getattr(pos, "horizon", "中线"))
        reason = (f"存量仓位再平衡：单票占比 {cur_value / total * 100:.1f}% "
                  f"超上限 {cap_pct * 100:.1f}%，减仓 {sell_vol} 股")
        try:
            order = broker.sell(code, sell_vol, cur_p)
            if order is not None and order.status == "filled":
                pnl = (order.filled_price - entry) * sell_vol
                log_trade(code, "sell", order.filled_price, sell_vol, pnl, reason, stype)
                close_attribution_for_code(code, pnl, reason, sell_vol)
                print(f"  [{code}] {reason}，盈亏 ¥{pnl:+.2f}")
                acted = True
        except Exception as e:
            print(f"  [{code}] 再平衡卖出失败: {e}")
    return acted


def check_positions(client, broker):
    params = get_effective_params()
    status = get_trading_status()
    action_taken = False
    sold_codes = set()  # 本轮已减/已平的代码，避免同一轮被再平衡重复处理
    for pos in status.get("positions", []):
        code = pos.stock_code
        entry = pos.avg_cost or 0
        cur_p = pos.current_price or 0
        vol   = pos.volume or 0
        stype = _horizon_label(getattr(pos, 'horizon', '中线'))
        if entry <= 0 or cur_p <= 0:
            continue
        # ── T+1 闸口：T+0 买入当日不可卖，止损/止盈/回撤均静默跳过 ──
        sv = broker.sellable_volume(code)
        if sv <= 0:
            print(f"  [{code}] T+1闸口 跳过：今日买入当日不可卖（持{vol}股，需持有≥1日才能卖）")
            continue
        pnl_pct = (cur_p - entry) / entry
        # ── 波动率画像用于仓位和复评；实际止损止盈按周期固定参数执行 ──
        vol_prof = calc_volatility_profile(get_stock_history(code, days=30))
        atr_pct = (vol_prof or {}).get("atr_pct") or 0.0
        sl, tp = get_stop_take(stype, params)
        # ── trailing peak：先查内存 → 缺失时从 SQLite 加载 → 写回 ──
        peak = _trailing_peak.get(code)
        if peak is None:
            peak = _load_trailing_peak(code)
            if peak is None:
                peak = pnl_pct
            _trailing_peak[code] = peak
        if pnl_pct > peak:
            peak = pnl_pct
            _trailing_peak[code] = peak
            _save_trailing_peak(code, peak, stype)
        trailing = _trailing_hit(stype, params, peak, pnl_pct)
        # ── 止损 / 止盈 / 回撤 共用的卖出执行 ──
        def _do_sell(reason: str):
            nonlocal action_taken
            try:
                order = broker.sell(code, vol, cur_p)
                if order is not None and order.status == "filled":
                    pnl = (order.filled_price - entry) * vol
                    tid = log_trade(code, "sell", order.filled_price, vol, pnl, reason, stype)
                    close_attribution_for_code(code, pnl, reason, vol)
                    print(f"  [{code}] {reason}，盈亏 ¥{pnl:+.2f}")
                    _trailing_peak.pop(code, None)
                    _drop_trailing_peak(code)
                    _ai_sell_streak.pop(code, None)
                    _drop_ai_sell_streak(code)
                    action_taken = True
                    sold_codes.add(code)
                    return True
            except Exception as e:
                print(f"  [{code}] 卖出失败: {e}")
            return False

        # ATR% 只保留为审计上下文，实际阈值始终来自当前周期参数
        tag = f" ATR%={atr_pct*100:.1f}%" if atr_pct > 0 else ""

        if pnl_pct <= sl:
            reason = f"触发止损（{pnl_pct*100:.1f}%）[{stype}]{tag}"
            _do_sell(reason)
        elif pnl_pct >= tp:
            reason = f"触发止盈（+{pnl_pct*100:.1f}%）[{stype}]{tag}"
            _do_sell(reason)
        elif trailing:
            reason = f"触发回撤止盈（峰值+{peak*100:.1f}%，现+{pnl_pct*100:.1f}%）[{stype}]{tag}"
            _do_sell(reason)
        else:
            # ── 兜底：价格规则未触发 → 触发 AI 复评通道（浮亏时） ──
            if _should_ai_review(code, pnl_pct, params):
                # 进程重启 streak 从 SQLite 加载；缺失则视为 0
                if code not in _ai_sell_streak:
                    _ai_sell_streak[code] = _load_ai_sell_streak(code)
                decision = _ai_re_evaluate_position(client, broker, code, pos, params, atr_pct)
                # 反噪声：连续 N 次独立复评建议 sell 才真正平仓，避免单次抖动误卖
                threshold = int(getattr(params, "ai_sell_streak_threshold", 2) or 2)
                if decision == "sell":
                    _ai_sell_streak[code] = int(_ai_sell_streak.get(code, 0)) + 1
                    _save_ai_sell_streak(code, _ai_sell_streak[code])
                else:
                    if _ai_sell_streak.get(code, 0):
                        _ai_sell_streak[code] = 0
                        _save_ai_sell_streak(code, 0)
                streak = _ai_sell_streak.get(code, 0)
                if decision == "sell" and streak >= threshold:
                    reason = f"AI复评连续{streak}次建议平仓（{pnl_pct*100:+.1f}%，ATR%={atr_pct*100:.1f}%）[{stype}]"
                    _do_sell(reason)
                else:
                    tail = f" streak={streak}/{threshold}" if decision == "sell" else ""
                    print(f"  [{code}] 持仓[{stype}] 成本¥{entry:.2f} 现价¥{cur_p:.2f} {pnl_pct*100:+.1f}% 固定线 {sl*100:.1f}%/{tp*100:.1f}% AI: {decision}{tail}")
            else:
                print(f"  [{code}] 持仓[{stype}] 成本¥{entry:.2f} 现价¥{cur_p:.2f} {pnl_pct*100:+.1f}% 固定线 {sl*100:.1f}%/{tp*100:.1f}%")
    # ── 止损/止盈/回撤处置完成后，再处理存量的超限仓位（只减不增） ──
    try:
        if rebalance_oversized_positions(broker, params, skip_codes=sold_codes):
            action_taken = True
    except Exception as e:
        print(f"  [再平衡] 执行失败: {e}")
    if action_taken:
        trigger_iteration()


# sr: 简化的数字格式化（用于止损/止盈线标签）
def sr(x):
    try:
        return f"{x:.1f}"
    except Exception:
        return str(x)


# ── 入场硬校验：提示词里的买入条件必须由代码复核，防止模型"声称满足"实际不满足 ──
# 与 analyze_and_decide 提示词中的"买入条件"保持一一对应：
#   短线：RSI<40 且 (KDJ金叉 或 放量上涨 量比>1.5)
#   中线：RSI<55 且 均线多头 且 MACD 多头动能（金叉或 MACD 状态已转多头）
#   长线：RSI<65 且 均线多头 且 换手率>1%
ENTRY_RULES = {
    "短线": "RSI<40 且 (KDJ金叉 或 量比>1.5)",
    "中线": "RSI<55 且 均线多头 且 MACD金叉/多头",
    "长线": "RSI<65 且 均线多头 且 换手率>1%",
}


def validate_entry_conditions(stype, ind, turnover=None):
    """代码侧入场闸口，返回 (ok, reasons)。指标缺失一律视为不满足（保守拒绝）。"""
    if not ind:
        return False, ["技术指标缺失"]
    try:
        rsi = float(ind.get("RSI(14)"))
    except (TypeError, ValueError):
        return False, ["RSI 缺失或非法"]

    fail = []
    if stype == "短线":
        if not rsi < 40:
            fail.append(f"RSI {rsi:.1f} 未低于 40")
        try:
            vol_ratio = float(ind.get("量比") or 0)
        except (TypeError, ValueError):
            vol_ratio = 0.0
        if ind.get("KDJ金叉") != "是" and vol_ratio <= 1.5:
            fail.append(f"KDJ未金叉且量比 {vol_ratio:.2f} 未超 1.5")
    elif stype == "长线":
        if not rsi < 65:
            fail.append(f"RSI {rsi:.1f} 未低于 65")
        if ind.get("均线多头") != "是":
            fail.append("均线非多头排列")
        try:
            tv = float(turnover if turnover is not None else ind.get("换手率") or 0)
        except (TypeError, ValueError):
            tv = 0.0
        if not tv > 1.0:
            fail.append(f"换手率 {tv:.2f}% 未超 1%")
    else:  # 中线为默认口径
        if not rsi < 55:
            fail.append(f"RSI {rsi:.1f} 未低于 55")
        if ind.get("均线多头") != "是":
            fail.append("均线非多头排列")
        if ind.get("MACD金叉") != "是" and ind.get("MACD状态") != "多头":
            fail.append("MACD 既未金叉也非多头")
    return (not fail), fail


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
            params = get_effective_params()
            policy_hint = _policy_overlay_text()
            policy_block = f"\n{policy_hint}\n" if policy_hint else ""
            # ── 波动率画像：ATR 约束仓位，周期固定阈值约束止损止盈 ──
            vol_prof = calc_volatility_profile(get_stock_history(code, days=30))
            atr_pct = (vol_prof or {}).get("atr_pct") or 0.0
            fixed_sl, fixed_tp = get_stop_take(horizon_label, params)
            vol_pos = get_volatility_position_size(params, atr_pct)
            vol_block = (
                f"\n周期执行阈值：\n"
                f"- 止损线 {fixed_sl*100:.1f}% / 止盈线 {fixed_tp*100:.1f}%\n"
                f"\n波动率画像：\n"
                f"- ATR%={atr_pct*100:.2f}%（近 20 日日均振幅）\n"
                f"- 单票仓位上限 {vol_pos*100:.1f}%（ATR 只约束仓位，不覆盖周期阈值）\n"
                if atr_pct > 0 else
                "\n波动率画像缺失，沿用周期固定阈值。\n"
            )
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
{policy_block}
本只候选股的最终策略类型必须为：{horizon_label}
总资产约100万，短线止损{params.short_stop_loss*100:.0f}%止盈{params.short_take_profit*100:.0f}%，中线止损{params.mid_stop_loss*100:.0f}%止盈{params.mid_take_profit*100:.0f}%，长线止损{params.long_stop_loss*100:.0f}%止盈{params.long_take_profit*100:.0f}%
{vol_block}

请严格判断，给出：
1. 策略类型：直接输出“{horizon_label}”，不要输出其他周期
2. 操作方向（买入/卖出/观望）
3. 仓位（总资产百分比，如20%）
4. 止损/止盈价（根据策略类型）
5. 操作理由（1-2句话）

买入条件：
- 短线：RSI<40 且 (KDJ金叉 或 放量上涨 量比>1.5)
- 中线：RSI<55 且 均线多头 且 (MACD金叉 或 MACD状态多头)
- 长线：RSI<65 且 均线多头 且 换手率>1%
以上买入条件会由系统代码二次硬校验，条件不满足时买入会被直接拒绝，请不要给出不满足条件的买入建议。
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

        # ── 入场硬校验：代码复核买入条件，模型"声称满足"不作数 ──
        entry_ok, entry_reasons = validate_entry_conditions(stype, ind, turnover)
        if action == "buy" and not entry_ok:
            print(f"  [{code}] 入场条件不满足({ENTRY_RULES.get(stype, stype)})：{'；'.join(entry_reasons)} → 降级观望")
            log_scan(code, "hold", 0, stock["最新价"],
                     f"硬校验拒绝买入：{'；'.join(entry_reasons)} | {analysis[:200]}", stype, 0)

        return {
            "action": action, "position_ratio": pos_pct,
            "price": stock["最新价"], "analysis": analysis,
            "market_context": mkt, "entry_indicators": ei,
            "strategy_type": stype, "horizon": {"短线": "short", "中线": "medium", "长线": "long"}[stype],
            "entry_ok": entry_ok, "entry_reasons": entry_reasons,
            "indicators": ind, "turnover": turnover,
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
    params = get_effective_params()
    bal    = broker.get_balance()
    total  = bal["total_assets"]

    if action == "buy":
        # ── 入场硬校验（执行前最后一道闸）：模型建议买入 ≠ 允许买入 ──
        entry_ok, entry_reasons = validate_entry_conditions(
            stype, decision.get("indicators"), decision.get("turnover"))
        if not entry_ok:
            print(f"  [{code}] 入场硬校验未通过，拒买：{'；'.join(entry_reasons)}")
            log_scan(code, "hold", 0, price,
                     f"硬校验拒买：{'；'.join(entry_reasons)}", stype, 0)
            return
        max_new = total * params.max_total_position - bal["market_value"]
        if max_new <= 0:
            print(f"  [{code}] 总仓位已达上限")
            return
        # ── 波动率自适应仓位：用 ATR% 反推单票上限 ──
        # 单票红线 = min(max_position_size, vol_position_size)
        vol_prof = calc_volatility_profile(get_stock_history(code, days=30))
        atr_pct = (vol_prof or {}).get("atr_pct") or 0.0
        vol_cap_pct = get_volatility_position_size(params, atr_pct)
        single_cap_pct = min(params.max_position_size, vol_cap_pct)
        single_cap = total * single_cap_pct
        print(f"  [{code}] vol位置 ATR%={atr_pct*100:.2f}% → 单票上限 {single_cap_pct*100:.1f}%")
        desired = min(total * decision["position_ratio"], max_new, single_cap)
        vol = int(desired / price / 100) * 100
        if vol < 100:
            print(f"  [{code}] 买入金额过小")
            return
        status = get_trading_status()
        ok, ind, same_val, limit_val = sector_concentration_ok(
            code, vol * price, status.get("positions", []), total)
        if not ok:
            print(f"  [{code}] 行业集中度超限 [{ind}] 同行业 ¥{same_val:,.0f}+本次 ¥{vol*price:,.0f} > ¥{limit_val:,.0f} (30%)")
            return
        if desired >= single_cap - 1:
            print(f"  [{code}] 触达单票上限 {params.max_position_size:.0%}")
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
                # ── T+1 闸口：T+0 买入当日不可卖，AI 信号也跳过 ──
                if broker.sellable_volume(code) <= 0:
                    return
                try:
                    order = broker.sell(code, vol, price)
                    if order is not None and order.status == "filled":
                        pnl = (order.filled_price - avg) * vol
                        tid = log_trade(code, "sell", order.filled_price, vol, pnl, "AI信号卖出", stype)
                        close_attribution_for_code(code, pnl, "AI信号卖出", vol)
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


def enforce_account_circuit_breaker(broker):
    """RISK.md 账户级熔断：回撤>20% 清仓暂停 1 个月；>30% 暂停 3 个月。"""
    try:
        bal = broker.get_balance()
        total = bal.get("total_assets", 0)
        if total <= 0:
            return False
        peak_state = update_account_peak(total)
        dd = peak_state["drawdown"]
        until = get_circuit_break_until()
        if until:
            try:
                if datetime.fromisoformat(until) > datetime.now():
                    return True
            except ValueError:
                pass
        if dd <= -0.30:
            days = 90
        elif dd <= -0.20:
            days = 30
        else:
            return False
        status = get_trading_status()
        closed = 0
        for pos in status.get("positions", []):
            # ── T+1 闸口：T+0 不可卖，连熔断也不能破 ──
            if broker.sellable_volume(pos.stock_code) <= 0:
                continue
            try:
                order = broker.sell(pos.stock_code, pos.volume, pos.current_price)
                if order is not None and order.status == "filled":
                    pnl = (order.filled_price - pos.avg_cost) * pos.volume
                    st = _horizon_label(getattr(pos, "horizon", "中线"))
                    tid = log_trade(pos.stock_code, "sell", order.filled_price,
                                    pos.volume, pnl, f"账户熔断({dd:.1%})", st)
                    close_attribution_for_code(pos.stock_code, pnl, f"账户熔断({dd:.1%})", pos.volume)
                    closed += 1
            except Exception as e:
                print(f"  [{pos.stock_code}] 熔断清仓失败: {e}")
        until = set_circuit_break(days)
        print(f"[风控] 账户回撤 {dd:.1%}，触发熔断，清仓 {closed} 只，暂停 {days} 天至 {until}")
        return True
    except Exception as e:
        print(f"[风控] 熔断检查失败: {e}")
        return False




def get_recommend_top():
    """最近一次全市场扫描的推荐第1名，无数据时回退到贵州茅台"""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT code, name FROM market_scan_log "
            "WHERE ts = (SELECT MAX(ts) FROM market_scan_log) "
            "ORDER BY score DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0], row[1] or row[0]
    except Exception:
        pass
    return '600519', '贵州茅台'


def startup_warmup(client, broker):
    """启动时检查网络和模型，完成后做默认股预分析"""
    import urllib.request
    print('\n[启动预热] 检查网络连通性...')
    net_ok = False
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open('https://www.baidu.com', timeout=5)
        net_ok = True
        print('[启动预热] 网络: OK')
    except Exception:
        print('[启动预热] 网络: 离线 (跳过预热)')

    print('[启动预热] 检查模型服务...')
    model_ok = client.is_alive()
    if model_ok:
        print(f'[启动预热] 模型 ({client.model}): 在线')
    else:
        print(f'[启动预热] 模型 ({client.model}): 离线 (跳过预热)')

    if net_ok and model_ok:
        # 默认对最近一次推荐股第1名做预热
        warmup_code, warmup_name = get_recommend_top()
        print(f'[启动预热] 预热分析 {warmup_name}({warmup_code})...')
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
    current_config_model = _get_bot_model()
    client = OMLXClient(model=current_config_model)
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
    print(f"自动操盘机器人启动 · 模型: {client.model}")
    print("全市场主力资金扫描启动")
    print(f"短线止损{params.short_stop_loss*100:.0f}%止盈{params.short_take_profit*100:.0f}%  中线止损{params.mid_stop_loss*100:.0f}%止盈{params.mid_take_profit*100:.0f}%  长线止损{params.long_stop_loss*100:.0f}%止盈{params.long_take_profit*100:.0f}%")
    print(f"持仓检查每{POSITION_CHECK_INTERVAL//60}分钟 · 全市场选股每{MARKET_SCAN_INTERVAL//60}分钟")
    print(f"回撤止盈: 短线+{params.short_trailing_activate*100:.0f}%启动回撤{params.short_trailing_drawdown*100:.0f}%落袋  中线+{params.mid_trailing_activate*100:.0f}%启动回撤{params.mid_trailing_drawdown*100:.0f}%落袋")
    print(f"迭代触发: 满 {params.observation_trades_threshold} 笔观察 / 满 {params.adjust_trades_threshold} 笔复核调参")
    print(f"迭代水位: 上次观察卖单 id={params.last_iterated_sell_id}，上次复核卖单 id={params.last_reviewed_sell_id}")
    Thread(target=run_scheduled_reports, args=(stop_event,), daemon=True).start()
    scan_rounds = MARKET_SCAN_INTERVAL // POSITION_CHECK_INTERVAL
    scan_round = 0  # 0 表示本轮执行全市场扫描
    while not stop_event.is_set():
        # 每轮同步 Dashboard 的模型配置，切换后无需重启机器人进程
        current_config_model = sync_bot_model(client, current_config_model)
        ts = datetime.now().strftime("%H:%M")
        if not is_trading_day():
            secs = seconds_to_open()
            print(f"[{ts}] 非交易日，{secs//3600}h{secs%3600//60}m 后开盘，休眠...")
            time.sleep(min(secs, 1800))
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
        if enforce_account_circuit_breaker(broker):
            print(f"[{ts}] 账户熔断暂停期，跳过扫描与买入")
            for _ in range(POSITION_CHECK_INTERVAL):
                if stop_event.is_set():
                    break
                time.sleep(1)
            continue
        if scan_round % scan_rounds == 0:
            print("全市场扫描选股...")
            candidates = scan_market()
            log_scan_result(candidates)

            # 已持仓的股票不再重复买入
            status = get_trading_status()
            held = {pos.stock_code for pos in status.get("positions", [])}

            print("分析候选股票...")
            for cand in candidates[:10]:  # 全量分析推荐TOP10
                code = cand["code"]
                if code in held:
                    print(f"  [{code}] 已在持仓，跳过")
                    continue
                print(f"  分析候选: {cand['name']}({code}) 分数={cand['score']}")
                run_scan(client, broker, code)
                time.sleep(5)  # 候选股分析间隔稍长
        else:
            print(f"全市场扫描倒计时: {scan_rounds - scan_round % scan_rounds} 轮后执行")
        scan_round += 1
        bal = get_trading_status()["balance"]
        print(f"\n账户: 总资产 ¥{bal['total_assets']:,.0f} 现金 ¥{bal['cash']:,.0f} 持仓 ¥{bal['market_value']:,.0f}")
        for pos in get_trading_status().get("positions", []):
            pct = (pos.current_price - pos.avg_cost) / max(pos.avg_cost or 1,1) * 100
            st = _horizon_label(getattr(pos, 'horizon', '中线'))
            print(f"  {pos.stock_code} {pos.volume}股 成本¥{pos.avg_cost:.2f} 现价¥{pos.current_price:.2f} {pct:+.1f}% [{st}]")
        print(f"\n[{ts}] 本轮完成，{POSITION_CHECK_INTERVAL//60} 分钟后检查持仓...")
        for _ in range(POSITION_CHECK_INTERVAL):
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

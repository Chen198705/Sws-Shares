"""
全市场主力资金扫描器
策略：量价代理主力信号 + 聚宽资金流加成
"""
import time, functools, sqlite3, os, random
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import requests

print = functools.partial(print, flush=True)
from jqdatasdk import auth, get_all_securities, get_price, get_money_flow_pro
auth(os.getenv("JQ_USERNAME", ""), os.getenv("JQ_PASSWORD", ""))
from market_data import get_stock_realtime, get_stock_history, calc_indicators, get_turnover_rate
from strategy_store import get_research_overlay
from research_snapshot import value_bp_metric

DB_PATH = Path(__file__).parent / "logs" / "trading_log.db"
SCAN_LIMIT = 200
MAX_WORKERS = 8
MIN_PRICE = 2.0
MAX_PRICE = 500.0
MIN_VOL_RATIO = 1.5
MIN_TURNOVER = 0.5
TOP_N = 10

# 研究层契约 60 秒缓存，避免每次打分重复读盘
_overlay_cache = {"ts": 0.0, "data": {}}


def _research_overlay():
    now = time.time()
    if now - _overlay_cache["ts"] > 60 or not _overlay_cache["data"]:
        _overlay_cache["data"] = get_research_overlay()
        _overlay_cache["ts"] = now
    return _overlay_cache["data"]


def jq_to_simple(code):
    return code.split(".")[0]


def score_stock(code, ind, turnover, stock, hist=None):
    score = 0
    reasons = []
    price = stock.get("最新价", 0)
    chg_pct = stock.get("涨跌幅", 0)
    vol_ratio = ind.get("量比", 1.0)
    rsi = ind.get("RSI(14)", 50)
    macd_gold = ind.get("MACD金叉") == "是"
    kdj_gold = ind.get("KDJ金叉") == "是"
    ma_bull = ind.get("均线多头") == "是"
    macd_state = ind.get("MACD状态") == "多头"
    kdj_state = ind.get("KDJ状态")
    amount = stock.get("成交额", 0)

    if vol_ratio >= 2.0:
        score += 30; reasons.append(f"放量{vol_ratio:.1f}倍")
    elif vol_ratio >= 1.5:
        score += 20; reasons.append(f"温和放量{vol_ratio:.1f}倍")

    if turnover >= 3.0:
        score += 20; reasons.append(f"高换手{turnover:.1f}%")
    elif turnover >= 1.5:
        score += 10; reasons.append(f"换手{turnover:.1f}%")

    if macd_gold:
        score += 25; reasons.append("MACD金叉")
    elif macd_state:
        score += 8; reasons.append("MACD多头")

    if kdj_gold and kdj_state != "超买":
        score += 15; reasons.append("KDJ低位金叉")
    elif kdj_gold:
        score += 5; reasons.append("KDJ金叉")

    if ma_bull:
        score += 10; reasons.append("均线多头")

    if 35 <= rsi <= 60:
        score += 10; reasons.append(f"RSI适中({rsi:.0f})")
    elif rsi < 30:
        score += 5; reasons.append(f"RSI超卖({rsi:.0f})")

    if 0 <= chg_pct <= 5:
        score += 10; reasons.append(f"涨幅温和+{chg_pct:.1f}%")
    elif chg_pct > 5:
        score -= 5; reasons.append(f"涨幅过大+{chg_pct:.1f}%")

    if amount >= 1e8:
        score += 10; reasons.append("成交额过亿")
    elif amount >= 5e7:
        score += 5

    if 5 <= price <= 200:
        score += 5; reasons.append(f"股价¥{price:.0f}")

    # 研究层因子约束（EXP-20260815-003 L1）：低换手有效、涨停追涨失效、低回撤有效
    fc = {f.get("id"): f for f in _research_overlay().get("factor_constraints") or []}
    t_st = (fc.get("liq_20d_turnover") or {}).get("status", "")
    if "有效" in t_st or "正信号" in t_st:
        if turnover < 1.5:
            score += 10; reasons.append("研究层低换手")
        elif turnover >= 8:
            score -= 15; reasons.append(f"研究层高换手{turnover:.0f}%反向")
    l_st = (fc.get("astock_limit_up_5d") or {}).get("status", "")
    if "失效" in l_st and chg_pct > 7:
        score -= 10; reasons.append("研究层涨停追涨失效")
    m_st = (fc.get("astock_maxdd_60d") or {}).get("status", "")
    if ("有效" in m_st or "正信号" in m_st) and hist is not None and len(hist) >= 20:
        close_series = hist["close"].tail(20)
        maxdd20 = float((close_series / close_series.cummax() - 1).min())
        if maxdd20 > -0.08:
            score += 8; reasons.append(f"研究层低回撤({maxdd20:.1%})")
        elif maxdd20 < -0.20:
            score -= 8; reasons.append(f"研究层回撤过大({maxdd20:.1%})")
    # 研究层 value_bp（EXP-009 L1）：账面市值比越高越便宜，纳入信号层倾斜
    v_st = (fc.get("value_bp") or {}).get("status", "")
    if "通过" in v_st or "有效" in v_st:
        pb, bp = value_bp_metric(code)
        if pb and bp:
            if bp >= 0.75:
                score += 8; reasons.append(f"研究层低估值PB={pb:.2f}")
            elif bp >= 0.40:
                score += 3
            elif bp < 0.25:
                score -= 5; reasons.append(f"研究层高估值PB={pb:.2f}")

    return {
        "code": code, "name": stock.get("股票名", code), "score": score,
        "price": price, "chg_pct": chg_pct, "vol_ratio": vol_ratio,
        "turnover": turnover, "rsi": rsi, "macd_gold": macd_gold,
        "kdj_gold": kdj_gold, "ma_bull": ma_bull,
        "reasons": reasons, "amount": amount,
    }


def try_get_money_flow(simple_code):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        df = get_money_flow_pro(simple_code, end_date=today, count=1)
        if not df.empty:
            row = df.iloc[-1]
            return {
                "net_xl": float(row.get("inflow_xl") or 0),
                "net_l": float(row.get("inflow_l") or 0),
            }
    except:
        pass
    return {}


def analyze_single(jq_code):
    try:
        simple = jq_to_simple(jq_code)
        stock = get_stock_realtime(simple)
        if "错误" in stock or stock.get("最新价", 0) <= 0:
            return None
        price = stock.get("最新价", 0)
        if price < MIN_PRICE or price > MAX_PRICE:
            return None

        hist = get_stock_history(simple, days=30)
        ind = calc_indicators(hist)
        if not ind:
            return None
        turnover = get_turnover_rate(simple)
        if turnover < MIN_TURNOVER:
            return None

        result = score_stock(simple, ind, turnover, stock, hist)
# 主力资金已从腾讯实时行情获取
        return result
    except:
        return None


def scan_market():
    print(f"[扫描] {datetime.now().strftime('%H:%M:%S')} 开始全市场扫描...")
    try:
        all_stocks = get_all_securities(["stock"])
        today = pd.Timestamp("today")
        codes = [c for c in all_stocks.index if all_stocks.loc[c]["end_date"] > today]
        print(f"[扫描] 全市场 {len(codes)} 只股票")
    except Exception as e:
        print(f"[扫描] 获取股票列表失败: {e}")
        return []

    if len(codes) > SCAN_LIMIT:
        codes = random.sample(codes, SCAN_LIMIT)
        print(f"[扫描] 采样 {len(codes)} 只")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(analyze_single, code): code for code in codes}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"[扫描] 已分析 {done}/{len(codes)}...")
            r = future.result()
            if r and r["score"] >= 25:
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:TOP_N]

    print(f"[扫描] 完成，{len(results)} 只达到门槛，TOP{len(top)}：")
    for i, r in enumerate(top, 1):
        print(f"  {i}. {r['name']}({r['code']}) 分={r['score']} "
              f"价={r['price']} 涨{r['chg_pct']:+.1f}% "
              f"量比{r['vol_ratio']:.1f} 换手{r['turnover']:.1f}% "
              f"{' '.join(r['reasons'][:3])}")
    return top


def log_scan_result(candidates):
    if not candidates:
        return
    conn = sqlite3.connect(str(DB_PATH))
    ts = datetime.now().isoformat()
    for c in candidates:
        conn.execute(
            "INSERT OR IGNORE INTO market_scan_log "
            "(ts,code,name,score,price,chg_pct,vol_ratio,turnover,reasons) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, c["code"], c["name"], c["score"], c["price"],
             c["chg_pct"], c["vol_ratio"], c["turnover"], "|".join(c["reasons"])))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS market_scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, code TEXT, name TEXT, score REAL,
        price REAL, chg_pct REAL, vol_ratio REAL,
        turnover REAL, reasons TEXT)""")
    conn.commit()
    conn.close()
    candidates = scan_market()
    log_scan_result(candidates)
    print(f"\n本次扫描完成，信号候选 {len(candidates)} 只")

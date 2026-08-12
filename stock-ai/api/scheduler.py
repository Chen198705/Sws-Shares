#!/usr/bin/env python3
"""
定时调度器 - 定期自动运行大盘/个股分析，输出报告
支持 cron 模式或守护进程模式

用法:
  python scheduler.py                    # 守护进程模式
  python scheduler.py --once            # 单次运行
  python scheduler.py --cron            # cron 模式（输出JSON，适合n8n调用）
"""
import sys
import json
import argparse
import datetime
import time
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data import get_all_indices, get_stock_realtime, get_stock_history, calc_indicators
from ai_client import OllamaClient
from config import REPORT_DIR, TRADING_PLAN


WATCH_LIST = ["600519", "300750", "000001", "000858", "601318"]


def save_report(title: str, content: str, tag: str = "daily"):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"{ts}_{title}.md"
    path.write_text(content, encoding="utf-8")
    print(f"报告已保存: {path}")


def save_signal(code: str, signal: dict, analysis: str):
    """将信号写入 SQLite 数据库"""
    db_path = REPORT_DIR / "signals.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            code TEXT,
            action TEXT,
            confidence INTEGER,
            position REAL,
            analysis TEXT
        )
    """)
    cur.execute("""
        INSERT INTO signals (ts, code, action, confidence, position, analysis)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.datetime.now().isoformat(),
        code,
        signal.get("action", "hold"),
        signal.get("confidence", 0),
        signal.get("position_ratio", 0),
        analysis[:500],
    ))
    conn.commit()
    conn.close()


def parse_signal(analysis_text: str, plan: str) -> dict:
    text = analysis_text.lower()
    confidence = 50
    action = "hold"
    position = 0.0
    if any(k in text for k in ["强烈买入", "强烈建议买入"]):
        action = "buy"
        confidence = 80
        position = 0.4 if plan == "conservative" else 0.6
    elif any(k in text for k in ["买入", "建议买入", "逢低买入", "逢低布局"]):
        action = "buy"
        confidence = 60
        position = 0.2 if plan == "conservative" else 0.3
    elif any(k in text for k in ["卖出", "减仓", "清仓", "建议卖出"]):
        action = "sell"
        confidence = 70
        position = 0.5
    elif any(k in text for k in ["观望", "等待", "不建议操作"]):
        action = "hold"
        confidence = 40
    return {"action": action, "confidence": confidence, "position_ratio": position}


def run_analysis(ai_client: OllamaClient):
    """执行一次完整分析"""
    results = {}

    # 大盘分析
    print("分析大盘...")
    indices = get_all_indices()
    lines = ["# 大盘分析报告\n", f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"]
    for name, d in indices.items():
        if "错误" not in d:
            lines.append(f"- {name}: {d['最新价']} {d['涨跌幅']:+.2f}%\n")
    market_str = "".join(lines)

    prompt = market_str + "\n请给出：1.情绪判断 2.短期走势 3.操作策略（轻仓/半仓/重仓/空仓）4.风险提示"
    try:
        analysis = ai_client.chat([
            {"role": "system", "content": "你是专业A股投资顾问，简明扼要给出建议。"},
            {"role": "user", "content": prompt},
        ], temperature=0.3)
        results["market"] = {"status": "success", "analysis": analysis}
        save_report("market_analysis", market_str + "\n\n## AI 分析\n" + analysis)
    except Exception as e:
        results["market"] = {"status": "error", "message": str(e)}
        print(f"大盘分析失败: {e}")

    # 个股分析
    for code in WATCH_LIST:
        print(f"分析个股 {code}...")
        try:
            stock = get_stock_realtime(code)
            if "错误" in stock:
                results[code] = {"status": "error", "message": stock["错误"]}
                continue
            hist = get_stock_history(code, days=60)
            ind = calc_indicators(hist)

            lines = [
                f"股票：{stock['股票名']}（{code}）\n",
                f"最新价：{stock['最新价']}  涨跌幅：{stock['涨跌幅']:+.2f}%\n",
            ]
            if ind:
                lines.append(f"MA5={ind['MA5']:.2f} MA20={ind['MA20']:.2f} RSI={ind['RSI(14)']:.1f} 均线多头={ind['均线多头']}\n")
            prompt = "".join(lines) + "\n请给出：操作建议（买入/卖出/观望）、仓位、止损位、风险等级"
            analysis = ai_client.chat([
                {"role": "system", "content": "你是专业A股投资顾问。"},
                {"role": "user", "content": prompt},
            ], temperature=0.4)
            signal = parse_signal(analysis, TRADING_PLAN)
            results[code] = {"status": "success", "stock": stock, "analysis": analysis, "signal": signal}
            save_report(f"stock_{code}", "\n".join(lines) + "\n\n## AI 分析\n" + analysis)
            save_signal(code, signal, analysis)
        except Exception as e:
            results[code] = {"status": "error", "message": str(e)}
            print(f"个股 {code} 分析失败: {e}")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="单次运行后退出")
    parser.add_argument("--cron", action="store_true", help="输出JSON格式（供n8n/cron调用）")
    parser.add_argument("--interval", type=int, default=3600, help="守护进程轮询间隔（秒）")
    args = parser.parse_args()

    ai_client = OllamaClient()

    if args.cron:
        results = run_analysis(ai_client)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if args.once:
        results = run_analysis(ai_client)
        if not args.cron:
            print("\n=== 分析结果摘要 ===")
            for key, val in results.items():
                if val.get("status") == "success" and "signal" in val:
                    s = val["signal"]
                    print(f"  {key}: {s['action']} (置信度{s['confidence']}%)")
                else:
                    print(f"  {key}: {val.get('message', val.get('status'))}")
        return

    # 守护进程模式
    print(f"守护进程模式启动，间隔 {args.interval} 秒")
    print("按 Ctrl+C 停止")
    while True:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{ts}] 开始分析...")
        run_analysis(ai_client)
        print(f"[{ts}] 分析完成，等待 {args.interval}s...")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

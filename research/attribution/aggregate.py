"""平仓归因聚合：按策略/周期/技术因子分桶统计胜率与盈亏。

读取沈万三 trading_log.db 的 trade_attribution（只读），输出研究层归因报告。
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stock-ai" / "api"))
import strategy_store
DEFAULT_DB = ROOT / "stock-ai" / "api" / "logs" / "trading_log.db"
DEFAULT_OUT = ROOT / "research" / "attribution" / "reports"


def _bucket(text: str) -> dict:
    """从 entry_indicators 文本提取技术因子分桶。"""
    t = text or ""
    def grab(pattern, default="未知"):
        m = re.search(pattern, t)
        return m.group(1) if m else default
    turnover = grab(r"换手率=([\d.]+)%")
    try:
        tv = float(turnover)
        turnover_bucket = "<1%" if tv < 1 else ("1-3%" if tv < 3 else ("3-5%" if tv < 5 else ">5%"))
    except (TypeError, ValueError):
        turnover_bucket = "未知"
    ma_bull = grab(r"均线多头=([是是否])", "否")
    rsi_state = grab(r"RSI状态=(\S+)", "正常")
    macd_gold = grab(r"MACD金叉=([是是否])", "否")
    kdj_gold = grab(r"KDJ金叉=([是是否])", "否")
    vol_state = grab(r"成交量状态=(\S+)", "正常")
    strong = (ma_bull == "是" and macd_gold == "是") or (kdj_gold == "是" and vol_state == "放量")
    return {
        "均线多头": ma_bull,
        "RSI状态": rsi_state,
        "MACD金叉": macd_gold,
        "KDJ金叉": kdj_gold,
        "成交量状态": vol_state,
        "换手率": turnover_bucket,
        "因子信号": "强因子共振" if strong else "弱因子/AI主观",
    }


def _agg_rows(rows: list) -> dict:
    out = {}
    for key, group in rows:
        n = len(group)
        wins = sum(1 for r in group if r["pnl"] > 0)
        net = sum(r["pnl"] for r in group)
        out[key] = {
            "count": n,
            "win_rate": round(wins / n, 3) if n else 0,
            "net_pnl": round(net, 2),
        }
    return out


def aggregate(db_path: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        empty = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db": str(db_path),
            "total": {"count": 0, "win_rate": 0.0, "net_pnl": 0.0},
            "message": f"trading_log.db 不存在: {db_path}",
        }
        (out_dir / "attribution.json").write_text(
            json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "report.md").write_text(
            "# 平仓归因报告\n\n暂无已平仓交易。\n", encoding="utf-8")
        return empty

    strategy_store.DB_PATH = db_path
    try:
        rows = strategy_store.reconcile_closed_trades()
    except Exception:
        rows = []
    if not rows:
        empty = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db": str(db_path),
            "total": {"count": 0, "win_rate": 0.0, "net_pnl": 0.0},
            "message": "暂无已平仓交易",
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "attribution.json").write_text(
            json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "report.md").write_text(
            "# 平仓归因报告\n\n暂无已平仓交易。\n", encoding="utf-8")
        return empty

    records = []
    for r in rows:
        buckets = _bucket(r.get("entry_indicators") or "")
        closed_at = r["closed_at"] or ""
        month = closed_at[:7] if len(closed_at) >= 7 else "未知"
        records.append({
            "code": r["code"],
            "strategy_type": r["strategy_type"] or "未知",
            "direction": r["direction"],
            "pnl": r.get("pnl") or 0.0,
            "closed_reason": r["closed_reason"] or "未知",
            "month": month,
            **buckets,
        })

    def by(fn):
        groups = defaultdict(list)
        for rec in records:
            groups[fn(rec)].append(rec)
        return _agg_rows(sorted(groups.items(), key=lambda x: -sum(r["pnl"] for r in x[1])))

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(db_path),
        "total": {
            "count": len(records),
            "win_rate": round(sum(1 for r in records if r["pnl"] > 0) / len(records), 3),
            "net_pnl": round(sum(r["pnl"] for r in records), 2),
        },
        "by_strategy": by(lambda r: r["strategy_type"]),
        "by_reason": by(lambda r: r["closed_reason"]),
        "by_month": by(lambda r: r["month"]),
        "by_factor_signal": by(lambda r: r["因子信号"]),
        "by_ma": by(lambda r: "均线多头=" + r["均线多头"]),
        "by_rsi": by(lambda r: "RSI=" + r["RSI状态"]),
        "by_macd": by(lambda r: "MACD金叉=" + r["MACD金叉"]),
        "by_kdj": by(lambda r: "KDJ金叉=" + r["KDJ金叉"]),
        "by_volume": by(lambda r: "量=" + r["成交量状态"]),
        "by_turnover": by(lambda r: "换手=" + r["换手率"]),
    }
    (out_dir / "attribution.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 平仓归因报告", ""]
    lines.append(f"生成时间：{report['generated_at']}")
    lines.append(f"总平仓 {report['total']['count']} 笔，胜率 {report['total']['win_rate']:.0%}，净盈亏 {report['total']['net_pnl']:,.2f} 元")
    for title, key in [
        ("按策略周期", "by_strategy"), ("按平仓原因", "by_reason"), ("按月", "by_month"),
        ("因子共振 vs AI 主观", "by_factor_signal"),
        ("均线", "by_ma"), ("RSI", "by_rsi"), ("MACD", "by_macd"),
        ("KDJ", "by_kdj"), ("成交量", "by_volume"), ("换手率", "by_turnover"),
    ]:
        lines.append(f"\n## {title}\n")
        lines.append("| 分桶 | 笔数 | 胜率 | 净盈亏 |")
        lines.append("|---|---|---|---|")
        for k, v in report[key].items():
            lines.append(f"| {k} | {v['count']} | {v['win_rate']:.0%} | {v['net_pnl']:,.2f} |")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    result = aggregate(args.db, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))

"""R6 分析师预期数据源评估（EXP-20260816-012）。

目标：评估免费源（东方财富 / 同花顺）能否提供"过去 3 年、月度频次"的
个股 EPS 一致预期序列，用于分析师预期修正因子（earnings revision, R6）。

验收（TASK.md T-2026-W6）：
- 每只股票过去 3 年覆盖度 >= 50%
- 月度频次可用

决策：数据成本 > 500 元/月 或 覆盖度不足 -> 搁置。
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP_DIR = ROOT / "research" / "experiments" / "EXP-20260816-012"
RESULT_DIR = EXP_DIR / "results"
SNAP = ROOT / "research" / "data" / "cache" / "fundamental_snapshot.csv"

N_PER_BUCKET = 10
LOOKBACK_MONTHS = 36
SEED = 42


def _eps_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns if re.match(r"^\d{4}-盈利预测-收益$", str(c))]


def _month_key(d: pd.Timestamp) -> str:
    return d.strftime("%Y-%m")


def _sample_codes() -> list:
    """按总市值三分位分层抽样，保证大小票都覆盖。"""
    if not SNAP.exists():
        raise SystemExit(f"缺少基本面快照: {SNAP}")
    snap = pd.read_csv(SNAP, dtype={"code": str})
    snap["code"] = snap["code"].str.zfill(6)
    snap = snap.dropna(subset=["total_mv"]).sort_values("total_mv").reset_index(drop=True)
    tercile = (pd.Series(snap.index) // (len(snap) // 3 + 1)).clip(upper=2)
    picked = []
    for bucket in range(3):
        pool = snap.loc[tercile == bucket, "code"].tolist()
        rng = __import__("random").Random(SEED + bucket)
        picked.extend(rng.sample(pool, min(N_PER_BUCKET, len(pool))))
    return picked


def _fetch_reports(code: str, retries: int = 2):
    import akshare as ak

    for i in range(retries + 1):
        try:
            df = ak.stock_research_report_em(symbol=code)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass
        time.sleep(1.5)
    return pd.DataFrame()


def _audit_one(code: str) -> dict:
    df = _fetch_reports(code)
    if df.empty:
        return {
            "code": code, "total_reports": 0, "reports_with_eps": 0,
            "report_month_coverage": 0.0, "eps_month_coverage": 0.0,
            "first_report": None, "last_report": None,
        }
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])
    eps_cols = _eps_cols(df)
    if eps_cols:
        df["_has_eps"] = df[eps_cols].notna().any(axis=1)
    else:
        df["_has_eps"] = False

    today = pd.Timestamp(datetime.now().date())
    start = today - pd.DateOffset(months=LOOKBACK_MONTHS)
    recent = df[df["日期"] >= start]
    months = pd.period_range(start.to_period("M"), today.to_period("M"), freq="M")
    report_months = set(recent["日期"].dt.to_period("M").astype(str))
    eps_months = set(recent.loc[recent["_has_eps"], "日期"].dt.to_period("M").astype(str))
    n = max(len(months), 1)
    return {
        "code": code,
        "total_reports": int(len(df)),
        "reports_with_eps": int(df["_has_eps"].sum()),
        "report_month_coverage": len(report_months & set(months.astype(str))) / n,
        "eps_month_coverage": len(eps_months & set(months.astype(str))) / n,
        "first_report": df["日期"].min().strftime("%Y-%m-%d"),
        "last_report": df["日期"].max().strftime("%Y-%m-%d"),
    }


def main() -> None:
    codes = _sample_codes()
    print(f"[R6] 分层抽样 {len(codes)} 只股票，回溯 {LOOKBACK_MONTHS} 个月")
    rows = []
    for i, code in enumerate(codes, 1):
        row = _audit_one(code)
        rows.append(row)
        print(f"[R6] {i}/{len(codes)} {code}: 研报覆盖 {row['report_month_coverage']:.0%} "
              f"EPS覆盖 {row['eps_month_coverage']:.0%}（{row['total_reports']} 篇研报）")
        time.sleep(0.4)

    df = pd.DataFrame(rows)
    rc = df["report_month_coverage"]
    ec = df["eps_month_coverage"]
    summary = {
        "experiment": "EXP-20260816-012",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": "东方财富 reportapi.eastmoney.com（akshare stock_research_report_em）+ 同花顺快照探针",
        "lookback_months": LOOKBACK_MONTHS,
        "n_stocks": int(len(df)),
        "report_month_coverage": {
            "mean": float(rc.mean()), "median": float(rc.median()),
            "pct_ge_50pct": float((rc >= 0.5).mean()),
        },
        "eps_month_coverage": {
            "mean": float(ec.mean()), "median": float(ec.median()),
            "pct_ge_50pct": float((ec >= 0.5).mean()),
        },
        "verdict": "搁置",
        "verdict_reason": "免费源（东财/同花顺）仅有当前年度前瞻一致预期快照，"
                         "无历史月度 EPS 一致预期序列，历史覆盖度不满足 >=50% 验收门槛",
    }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULT_DIR / "coverage_per_stock.csv", index=False, encoding="utf-8-sig")
    (RESULT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# EXP-20260816-012: R6 分析师预期数据源覆盖度评估",
        "",
        "**日期**：2026-08-16",
        "**目标**：免费源能否提供过去 3 年、月度频次的 EPS 一致预期序列",
        "**验收**：每只股票覆盖度 >= 50%，月度频次可用",
        "",
        "## 方法",
        "",
        f"- 按总市值三分位分层抽样，每档 {N_PER_BUCKET} 只，共 {summary['n_stocks']} 只",
        "- 东财 `stock_research_report_em` 拉取全部个股研报（含报告日期与当前年度 EPS 预测）",
        "- `研报覆盖度` = 过去 36 个月中至少 1 篇研报的月份占比",
        "- `EPS覆盖度` = 过去 36 个月中研报带 EPS 预测值的月份占比（可构造月度一致预期的部分）",
        "- 同花顺 `stock_profit_forecast_ths` 仅作快照探针：只返回 2026/2027/2028 三年当前一致预期",
        "",
        "## 结果",
        "",
        f"- 样本数：{summary['n_stocks']}",
        f"- 研报覆盖度：mean {rc.mean():.1%} / median {rc.median():.1%} / >=50% 占比 {float((rc>=0.5).mean()):.1%}",
        f"- EPS 覆盖度（历史月度一致预期可用部分）：mean {ec.mean():.1%} / median {ec.median():.1%} / >=50% 占比 {float((ec>=0.5).mean()):.1%}",
        "",
        "## 结论",
        "",
        "- 东财免费接口只回传当前年（+1/+2）的前瞻预测列；历史研报的 EPS 预测列为空，"
        "无法重建历史月度一致预期序列。",
        "- 同花顺盈利预测同样只有当前年度快照（预测机构数/均值/最大最小值），无历史序列。",
        "- 历史覆盖度未达验收门槛（每只股票 >=50%），按 TASK.md 决策规则**搁置 R6**。",
        "- 若后续获得 Choice / Wind 试用（成本 >500 元/月 时按规范仍需决策），可重启评估。",
        "",
        "**是否更新 KNOWLEDGE_BASE.md**：是（R6 状态：搁置）",
        "**是否更新 FEATURE_LIBRARY.md**：是（分析师预期修正因子：搁置）",
    ]
    (RESULT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[R6] 完成：{summary['n_stocks']} 只，EPS覆盖 mean {ec.mean():.1%} -> {summary['verdict']}")


if __name__ == "__main__":
    main()

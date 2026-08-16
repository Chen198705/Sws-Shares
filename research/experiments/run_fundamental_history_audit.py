"""EXP-20260816-009：历史估值 + 质量因子的 L1 截面审计。

数据源：fundamental_history.py 拉取的 2018 至今 PE(TTM)/PB/市值/ROE/毛利率。
与 EXP-007 同款 OLS / Ridge / LASSO 交叉验证框架，SPLIT=2023-01-01。
财务指标按披露时限保守后移（availability_date），避免前视。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.data.loader import load_panel
from research.data.fundamental_history import (
    FIN_SUFFIX, VALUATION_SUFFIX,
    valuation_factor_panel, quality_factor_panel,
)
from research.models.ols_factor import run_ols_audit, monthly_fwd_returns
from research.models.regularized import run_regularized_audit
from research.backtest.metrics import rank_ic


FACTORS = ["value_ep", "value_bp", "size_logcap", "quality_roe",
           "quality_gross_margin"]
SPLIT = "2023-01-01"
REPORT_DIR = ROOT / "research" / "experiments" / "EXP-20260816-009" / "results"

def _rank_ic_stats(factor_mat: pd.DataFrame, fwd: pd.DataFrame,
                   dates, split: str) -> dict:
    oos = [d for d in dates if d >= pd.Timestamp(split)]
    ics = []
    for d in oos:
        if d not in factor_mat.index or d not in fwd.index:
            continue
        ic = rank_ic(factor_mat.loc[d], fwd.loc[d])
        if np.isfinite(ic):
            ics.append(ic)
    ics = np.asarray(ics)
    if len(ics) == 0:
        return {"periods": 0}
    t = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if ics.std(ddof=1) > 0 else 0.0
    return {"periods": int(len(ics)), "mean_ic": float(ics.mean()),
            "median_ic": float(np.median(ics)),
            "positive_share": float((ics > 0).mean()), "t_stat": t}


def main(sample: int = 0):
    cache = ROOT / "research" / "data" / "cache"
    qfq = sorted(cache.glob("*_qfq.csv"))
    codes = [f.name.split("_")[0] for f in qfq]
    codes = [c for c in codes if (cache / f"{c}{VALUATION_SUFFIX}").exists()]
    if sample > 0:
        codes = codes[:sample]
    panel = load_panel(codes, "2014-01-01", "2024-12-31", cache, adjust="qfq")
    print(f"[EXP-009] panel stocks = {len(panel)}")

    factor_mats = {}
    for f in FACTORS:
        if f in ("quality_roe", "quality_gross_margin"):
            mat = quality_factor_panel(
                panel, cache, "roe" if f == "quality_roe" else "gross_margin")
        else:
            mat = valuation_factor_panel(panel, cache, f)
        mat = mat.loc[:, mat.notna().any()]
        factor_mats[f] = mat
        print(f"[EXP-009] {f}: {mat.shape[0]} dates x {mat.shape[1]} stocks")

    fwd = monthly_fwd_returns(panel)
    ols = run_ols_audit(panel, factor_mats, FACTORS, SPLIT, fwd)
    reg = run_regularized_audit(
        panel, factor_mats, FACTORS, SPLIT,
        alphas={"ridge": 1.0, "lasso": 0.001}, fwd=fwd)

    all_dates = factor_mats[FACTORS[0]].index
    for f in FACTORS[1:]:
        all_dates = all_dates.union(factor_mats[f].index)
    dates = all_dates.intersection(fwd.index)
    ic = {f: _rank_ic_stats(factor_mats[f], fwd, dates, SPLIT) for f in FACTORS}

    out = {
        "experiment": "EXP-20260816-009",
        "hypothesis": "历史估值/质量因子在 L1 截面收益上有效且稳健",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stocks": len(panel),
        "split_date": SPLIT,
        "factors": FACTORS,
        "ols": ols,
        "regularized": reg,
        "rank_ic_oos": ic,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "metrics.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    _write_report(out)
    print(json.dumps({f: ic[f] for f in FACTORS}, ensure_ascii=False, indent=2))
    return out


def _write_report(out: dict):
    lines = ["# EXP-20260816-009: 历史估值/质量因子 L1 截面审计", ""]
    lines.append(f"生成时间：{out['generated_at']}，股票数：{out['stocks']}，"
                 f"样本外起点：{out['split_date']}")
    lines.append("")
    lines.append("| 因子 | 样本外 mean_coef | OOS positive_share | OOS t(NW) | 样本外 mean_IC | IC t |")
    lines.append("|---|---|---|---|---|---|")
    for f in FACTORS:
        c = (out["ols"]["out_of_sample"].get("coefficients") or {}).get(f) or {}
        r = out["rank_ic_oos"].get(f) or {}
        lines.append(
            f"| {f} | {c.get('mean_coef', '')} | {c.get('positive_share', '')} | "
            f"{c.get('t_stat_nw', '')} | {r.get('mean_ic', '')} | {r.get('t_stat', '')} |")
    lines.append("")
    for method in ("ridge", "lasso"):
        r = out["regularized"][method]["out_of_sample"]
        lines.append(f"## {method.upper()} OOS")
        for f in FACTORS:
            c = (r.get("coefficients") or {}).get(f) or {}
            lines.append(f"- {f}: mean_coef={c.get('mean_coef')}, "
                         f"positive_share={c.get('positive_share')}")
        lines.append("")
    (REPORT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    main(args.sample)

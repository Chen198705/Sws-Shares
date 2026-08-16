"""EXP-20260816-007：Ridge/LASSO 与 OLS 样本外交叉验证（MODEL_LIBRARY M2）。"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.data.loader import load_panel
from research.factors.registry import FACTOR_FUNCS
from research.models.ols_factor import run_ols_audit, monthly_fwd_returns
from research.models.regularized import run_regularized_audit

REPORT = ROOT / "research" / "experiments" / "EXP-20260816-007" / "results"
FACTORS = ["mom_12_1", "mom_6_1", "mom_1", "vol_60d_realized", "vol_20d_atr",
           "liq_20d_turnover", "liq_20d_amt", "liq_amihud_20d",
           "astock_limit_up_5d", "astock_maxdd_60d"]
SPLIT = "2023-01-01"


def main(sample: int = 0):
    cache = ROOT / "research" / "data" / "cache"
    qfq = sorted(cache.glob("*_qfq.csv"))
    codes = [f.name.split("_")[0] for f in qfq]
    if sample > 0:
        codes = codes[:sample]
    panel = load_panel(codes, "2014-01-01", "2024-12-31", cache, adjust="qfq")
    print(f"[EXP-007] panel stocks = {len(panel)}")
    factor_mats = {name: _panel(name, panel) for name in FACTORS}
    fwd = monthly_fwd_returns(panel)

    ols = run_ols_audit(panel, factor_mats, FACTORS, SPLIT, fwd)
    reg = run_regularized_audit(
        panel, factor_mats, FACTORS, SPLIT,
        alphas={"ridge": 1.0, "lasso": 0.001}, fwd=fwd)
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "stocks": len(panel), "split_date": SPLIT,
           "ols": ols, "regularized": reg}
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "metrics.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_report(out)
    print(json.dumps({k: _brief(v) for k, v in out.items()},
                     ensure_ascii=False, indent=2, default=str))
    return out


def _panel(name, panel):
    from research.factors.registry import compute_factor_panel
    return compute_factor_panel(panel, name)


def _brief(d):
    if not isinstance(d, dict):
        return d
    return {k: (v if k != "meta" else f"{len(v)} periods") for k, v in d.items()}


def _write_report(out):
    lines = ["# EXP-20260816-007: OLS / Ridge / LASSO 样本外交叉验证", ""]
    lines.append(f"生成时间：{out['generated_at']}，股票数：{out['stocks']}，样本外起点：{out['split_date']}")
    for model in ("ols", "ridge", "lasso"):
        if model == "ols":
            src = out["ols"]["in_sample"], out["ols"]["out_of_sample"]
            label = "OLS"
        else:
            r = out["regularized"][model]
            src = r["in_sample"], r["out_of_sample"]
            label = f"{model.upper()} (alpha={r['in_sample'].get('alpha')})"
        lines.append(f"\n## {label}\n")
        lines.append("| 因子 | 样本内 mean_coef | 样本外 mean_coef | 样本外 positive_share |")
        lines.append("|---|---|---|---|")
        for f in FACTORS:
            ic = (src[0].get("coefficients") or {}).get(f) or {}
            oc = (src[1].get("coefficients") or {}).get(f) or {}
            lines.append(f"| {f} | {ic.get('mean_coef', ''):.4f} | "
                         f"{oc.get('mean_coef', ''):.4f} | {oc.get('positive_share', ''):.0%} |")
        lines.append(f"\n样本内 avg R2={src[0].get('avg_r2')}, 样本外 avg R2={src[1].get('avg_r2')}")
    (REPORT / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    main(args.sample)

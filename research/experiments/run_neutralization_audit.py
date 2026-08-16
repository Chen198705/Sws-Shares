"""EXP-20260816-008: 行业中性化因子审计（RESEARCH.md H4）。

对比 10 个价格因子在"原始值"与"行业中性化残差"两种口径下的
样本外 OLS 系数稳定性与 rank IC，验证 H4：
"行业中性化后的因子比原始因子更稳健"。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.data.loader import load_panel
from research.factors.registry import compute_factor_panel
from research.factors.neutralize import industry_neutralize_matrix, load_industry_map
from research.models.ols_factor import run_ols_audit, monthly_fwd_returns
from research.backtest.metrics import rank_ic

FACTORS = ["mom_12_1", "mom_6_1", "mom_1", "vol_60d_realized", "vol_20d_atr",
           "liq_20d_turnover", "liq_20d_amt", "liq_amihud_20d",
           "astock_limit_up_5d", "astock_maxdd_60d"]
SPLIT = "2023-01-01"
REPORT_DIR = ROOT / "experiments" / "EXP-20260816-008" / "results"


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
    return {
        "periods": int(len(ics)),
        "mean_ic": float(ics.mean()),
        "median_ic": float(np.median(ics)),
        "positive_share": float((ics > 0).mean()),
        "t_stat": t,
    }


def _coef_summary(ols: dict) -> dict:
    out = {}
    for f in FACTORS:
        c = ols["out_of_sample"]["coefficients"].get(f)
        if c is None:
            out[f] = {}
            continue
        out[f] = {
            "mean_coef": c["mean_coef"],
            "positive_share": c["positive_share"],
            "t_stat_nw": c["t_stat_nw"],
        }
    return out


def main():
    cache = ROOT / "data" / "cache"
    qfq = sorted(cache.glob("*_qfq.csv"))
    codes = [f.name.split("_")[0] for f in qfq]
    panel = load_panel(codes, "2014-01-01", "2024-12-31", cache, adjust="qfq")
    print(f"[EXP-008] panel stocks = {len(panel)}")

    industry_map = load_industry_map()
    mapped = sum(1 for c in codes if industry_map.get(str(c).zfill(6)))
    print(f"[EXP-008] industry mapped = {mapped}/{len(codes)}")

    factor_mats_raw = {name: compute_factor_panel(panel, name) for name in FACTORS}
    factor_mats_neutral = {
        name: industry_neutralize_matrix(mat, industry_map)
        for name, mat in factor_mats_raw.items()
    }

    fwd = monthly_fwd_returns(panel)
    ols_raw = run_ols_audit(panel, factor_mats_raw, FACTORS, SPLIT, fwd)
    ols_neutral = run_ols_audit(panel, factor_mats_neutral, FACTORS, SPLIT, fwd)

    all_dates = factor_mats_raw[FACTORS[0]].index
    for f in FACTORS[1:]:
        all_dates = all_dates.union(factor_mats_raw[f].index)
    dates = all_dates.intersection(fwd.index)

    raw_ic = {f: _rank_ic_stats(factor_mats_raw[f], fwd, dates, SPLIT) for f in FACTORS}
    neutral_ic = {f: _rank_ic_stats(factor_mats_neutral[f], fwd, dates, SPLIT) for f in FACTORS}

    raw_coef = _coef_summary(ols_raw)
    neutral_coef = _coef_summary(ols_neutral)

    comparison = {}
    for f in FACTORS:
        r_c, n_c = raw_coef.get(f, {}), neutral_coef.get(f, {})
        r_i, n_i = raw_ic.get(f, {}), neutral_ic.get(f, {})
        sign_raw = np.sign(r_c.get("mean_coef", 0)) if r_c.get("mean_coef") else 0
        sign_neu = np.sign(n_c.get("mean_coef", 0)) if n_c.get("mean_coef") else 0
        comparison[f] = {
            "raw": {"mean_coef": r_c.get("mean_coef"), "positive_share": r_c.get("positive_share"),
                    "t_stat_nw": r_c.get("t_stat_nw"), "mean_ic": r_i.get("mean_ic"),
                    "ic_t": r_i.get("t_stat")},
            "neutralized": {"mean_coef": n_c.get("mean_coef"), "positive_share": n_c.get("positive_share"),
                            "t_stat_nw": n_c.get("t_stat_nw"), "mean_ic": n_i.get("mean_ic"),
                            "ic_t": n_i.get("t_stat")},
            "oos_sign_consistent": sign_raw == sign_neu,
            "abs_ic_gain": abs(n_i.get("mean_ic") or 0) - abs(r_i.get("mean_ic") or 0),
        }

    report = {
        "experiment": "EXP-20260816-008",
        "hypothesis": "H4 行业中性化后的因子比原始因子更稳健",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stocks": len(panel),
        "industry_mapped": mapped,
        "industries": int(len(set(industry_map.values()))),
        "split_date": SPLIT,
        "ols_raw": {"in_sample_r2": ols_raw["in_sample"].get("avg_r2"),
                    "out_of_sample_r2": ols_raw["out_of_sample"].get("avg_r2")},
        "ols_neutralized": {"in_sample_r2": ols_neutral["in_sample"].get("avg_r2"),
                            "out_of_sample_r2": ols_neutral["out_of_sample"].get("avg_r2")},
        "factors": comparison,
        "sign_flips": sum(1 for v in comparison.values() if not v["oos_sign_consistent"]),
        "abs_ic_improved": sum(1 for v in comparison.values() if v["abs_ic_gain"] > 0),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_report(report)
    print(json.dumps({k: v for k, v in report.items() if k != "factors"},
                     ensure_ascii=False, indent=2, default=str))


def _write_report(report: dict):
    lines = ["# EXP-20260816-008: 行业中性化因子审计", ""]
    lines.append(f"生成时间：{report['generated_at']}，股票数：{report['stocks']}，"
                 f"行业映射：{report['industry_mapped']}/{report['stocks']}（{report['industries']} 个行业）")
    lines.append("")
    lines.append(f"样本外起点：{report['split_date']}")
    lines.append(f"- 原始 OLS 样本外 R²：{report['ols_raw']['out_of_sample_r2']:.4f}")
    lines.append(f"- 中性化 OLS 样本外 R²：{report['ols_neutralized']['out_of_sample_r2']:.4f}")
    lines.append(f"- 样本外系数方向翻转：{report['sign_flips']} 个")
    lines.append(f"- |IC| 改善因子数：{report['abs_ic_improved']}/{len(report['factors'])}")
    lines.append("")
    lines.append("| 因子 | 原始 mean_coef | 中性化 mean_coef | 原始 IC | 中性化 IC | 方向一致 | |IC| 增益 |")
    lines.append("|---|---|---|---|---|---|---|")
    for f, v in report["factors"].items():
        r, n = v["raw"], v["neutralized"]
        ic_gain = f"{v['abs_ic_gain']:+.4f}" if v["abs_ic_gain"] is not None else "-"
        lines.append(
            f"| {f} | {r.get('mean_coef')} | {n.get('mean_coef')} | "
            f"{r.get('mean_ic')} | {n.get('mean_ic')} | {'是' if v['oos_sign_consistent'] else '否'} | {ic_gain} |"
        )
    (REPORT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()

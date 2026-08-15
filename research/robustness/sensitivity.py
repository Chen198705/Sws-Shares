"""参数敏感性（BACKTEST.md R2）：top_quantile / min_listed_days ±20%。"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from research.data.loader import load_panel
from research.data.universe import get_universe
from research.factors.registry import compute_factor_panel
from research.backtest.engine import monthly_rebalance
from research.backtest.metrics import performance


def _oos_perf(nav: pd.Series, split: str) -> dict:
    oos = nav[nav.index >= pd.Timestamp(split)]
    ret = oos.pct_change(fill_method=None).dropna()
    return performance(ret)


def run(cfg: dict, factor_name: str) -> dict:
    exp = cfg["experiment"]
    codes = get_universe(exp)
    panel = load_panel(
        codes, exp["start_date"], exp["end_date"],
        ROOT / cfg["data"]["cache_dir"],
        adjust=cfg["data"].get("adjust", ""),
        retry=cfg["data"].get("retry", 3),
    )
    factor_matrix = compute_factor_panel(panel, factor_name)
    costs = cfg["costs"]
    base = {
        "top_quantile": exp.get("top_quantile", 0.2),
        "min_listed_days": exp.get("min_listed_days", 120),
        "max_holdings": exp.get("max_holdings", 20),
    }
    variants = {"baseline": base}
    # 持仓上限与 top_quantile 联动，避免全市场下被 max_holdings 截断导致变体无效
    variants["top_quantile*1.2"] = {
        **base,
        "top_quantile": base["top_quantile"] * 1.2,
        "max_holdings": max(5, int(base["max_holdings"] * 1.2)),
    }
    variants["top_quantile*0.8"] = {
        **base,
        "top_quantile": base["top_quantile"] * 0.8,
        "max_holdings": max(5, int(base["max_holdings"] * 0.8)),
    }
    variants["min_listed_days*1.2"] = {**base, "min_listed_days": int(base["min_listed_days"] * 1.2)}
    variants["min_listed_days*0.8"] = {**base, "min_listed_days": max(30, int(base["min_listed_days"] * 0.8))}

    results = {}
    for name, kw in variants.items():
        out = monthly_rebalance(
            panel, factor_matrix, costs,
            top_quantile=kw["top_quantile"],
            max_holdings=kw["max_holdings"],
            min_listed_days=kw["min_listed_days"],
            min_price=exp.get("min_price", 3.0),
            max_price=exp.get("max_price", 500.0),
            ascending=str(exp.get("factor_directions", {}).get(factor_name, "desc")) == "asc",
        )
        results[name] = _oos_perf(out["nav"], exp["split_date"])
        print(f"[sensitivity] {name}: oos_ann={results[name].get('annualized_return', 0):.2%} "
              f"sharpe={results[name].get('sharpe', 0):.2f} mdd={results[name].get('max_drawdown', 0):.2%}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/EXP-20260815-002.yaml")
    ap.add_argument("--factor", default="vol_20d_atr")
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / args.config).read_text())
    res = run(cfg, args.factor)
    out_dir = ROOT / "robustness" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"sensitivity_{cfg['experiment']['id']}_{args.factor}.json"
    (out_dir / fname).write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[sensitivity] done -> {out_dir / fname}")


if __name__ == "__main__":
    main()

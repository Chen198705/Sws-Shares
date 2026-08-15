"""实验入口：universe → 数据 → 质量 → 因子 → 回测 → 报告。"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))

from research.data.universe import get_universe
from research.data.loader import load_panel
from research.data.quality import validate_panel
from research.factors.momentum import compute_factor_panel
from research.backtest.engine import monthly_rebalance
from research.backtest.metrics import performance, ic_series
from research.baselines.baselines import bl1_random_walk, bl2_historical_mean, bl3_equal_weight, bl4_simple_momentum


def _split_returns(nav: pd.Series, split_date: str):
    nav = nav[nav.index >= pd.Timestamp(split_date)]
    ret = nav.pct_change(fill_method=None).dropna()
    return ret


def run(cfg: dict):
    exp_id = cfg["experiment"]["id"]
    report_dir = ROOT / cfg.get("report_dir", f"experiments/{exp_id}/results")
    report_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{exp_id}] universe = {cfg['experiment']['universe']}")
    codes = get_universe(cfg["experiment"])
    print(f"[{exp_id}] codes = {len(codes)}")

    panel = load_panel(
        codes,
        cfg["experiment"]["start_date"],
        cfg["experiment"]["end_date"],
        ROOT / cfg["data"]["cache_dir"],
        adjust=cfg["data"].get("adjust", ""),
        retry=cfg["data"].get("retry", 3),
    )
    print(f"[{exp_id}] panel stocks = {len(panel)}")

    quality = validate_panel(panel)
    (report_dir / "quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2, default=str))
    print(f"[{exp_id}] quality issues = {quality['_summary']['with_issues']}")

    costs = cfg["costs"]
    factor_matrix = compute_factor_panel(panel, cfg["experiment"]["factor"])
    print(f"[{exp_id}] factor rows = {len(factor_matrix)}")

    split = cfg["experiment"]["split_date"]
    results = {}
    strategies = {
        "BL1_random_walk": bl1_random_walk(panel, costs, cfg["experiment"]),
        "BL2_historical_mean": bl2_historical_mean(panel, costs, cfg["experiment"]),
        "BL3_equal_weight": bl3_equal_weight(panel, costs, cfg["experiment"]),
        "BL4_simple_momentum": bl4_simple_momentum(panel, costs, cfg["experiment"]),
        "FACTOR_" + cfg["experiment"]["factor"]: monthly_rebalance(
            panel, factor_matrix, costs,
            top_quantile=cfg["experiment"].get("top_quantile", 0.2),
            max_holdings=cfg["experiment"].get("max_holdings", 20),
            min_listed_days=cfg["experiment"].get("min_listed_days", 120),
            min_price=cfg["experiment"].get("min_price", 3.0),
            max_price=cfg["experiment"].get("max_price", 500.0),
        ),
    }

    summary = {}
    for name, out in strategies.items():
        nav = out["nav"]
        ret = nav.pct_change().dropna()
        perf_all = performance(ret)
        perf_oos = performance(_split_returns(nav, split))
        ic = out["ic"]
        summary[name] = {
            "in_sample": perf_all,
            "out_of_sample": perf_oos,
            "rank_ic_mean": float(ic.mean()) if len(ic) else None,
            "rank_ic_t": float(ic.mean() / (ic.std() / len(ic) ** 0.5)) if len(ic) > 1 and ic.std() > 0 else None,
            "cash_ratio": out["cash_ratio"],
            "periods": len(ret),
        }
        out["nav"].to_csv(report_dir / f"{name}_nav.csv")
        if len(ic):
            ic.to_csv(report_dir / f"{name}_ic.csv")
        print(f"[{exp_id}] {name}: oos_ann={perf_oos.get('annualized_return', 0):.2%} sharpe={perf_oos.get('sharpe', 0):.2f} mdd={perf_oos.get('max_drawdown', 0):.2%}")

    (report_dir / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    def _fmt(p):
        if not p:
            return "-"
        return (
            f"年化 {p.get('annualized_return', 0):.2%} / "
            f"波动 {p.get('annualized_vol', 0):.2%} / "
            f"夏普 {p.get('sharpe', 0):.2f} / "
            f"最大回撤 {p.get('max_drawdown', 0):.2%}"
        )

    lines = [f"# {exp_id} 实验结果", "", "| 策略 | 样本内 | 样本外 | rank IC | 现金占比 | 期数 |", "|---|---|---|---|---|---|"]
    for name, s in summary.items():
        ic = f"{s['rank_ic_mean']:.3f}" if s["rank_ic_mean"] is not None else "-"
        if s["rank_ic_t"] is not None:
            ic += f" (t={s['rank_ic_t']:.2f})"
        lines.append(
            f"| {name} | {_fmt(s['in_sample'])} | {_fmt(s['out_of_sample'])} | {ic} | {s['cash_ratio']:.2%} | {s['periods']} |"
        )
    (report_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[{exp_id}] done -> {report_dir}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/EXP-20260815-001.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / args.config).read_text())
    run(cfg)


if __name__ == "__main__":
    main()

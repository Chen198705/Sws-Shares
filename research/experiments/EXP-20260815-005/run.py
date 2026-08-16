"""EXP-20260815-005: OLS 多因子截面回归（MODEL_LIBRARY M1）。

全市场 qfq 上对 10 个因子做逐月截面 OLS，输出样本内/样本外平均系数、
Newey-West t 与平均 R2，与 rank IC 交叉验证。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent))

from research.data.loader import load_panel
from research.data.universe import get_universe
from research.factors.registry import compute_factor_panel
from research.models.ols_factor import run_ols_audit


def main():
    exp_dir = Path(__file__).parent
    result_dir = exp_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load((ROOT / "configs" / "EXP-20260815-003.yaml").read_text())
    exp = cfg["experiment"]
    print(f"[EXP-005] universe = {exp['universe']}")
    codes = get_universe(exp)
    print(f"[EXP-005] codes = {len(codes)}")
    panel = load_panel(
        codes, exp["start_date"], exp["end_date"],
        ROOT / cfg["data"]["cache_dir"],
        adjust=cfg["data"].get("adjust", ""),
        retry=cfg["data"].get("retry", 3),
    )
    print(f"[EXP-005] panel stocks = {len(panel)}")
    factors = exp.get("factors")
    factor_mats = {}
    for fname in factors:
        factor_mats[fname] = compute_factor_panel(panel, fname)
        print(f"[EXP-005] factor {fname} rows = {len(factor_mats[fname])}")
    report = run_ols_audit(panel, factor_mats, factors, exp["split_date"])
    report["experiment"] = "EXP-20260815-005"
    report["hypothesis"] = "M1 OLS 多因子回归：因子系数在样本外保持显著"
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    (result_dir / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

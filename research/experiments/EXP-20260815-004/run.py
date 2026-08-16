"""EXP-20260815-004: GARCH(1,1)/GJR-GARCH 波动率预测 vs 历史波动率（H6）。

在上证综指日线上滚动训练，比较 MSE / QLIKE / 方向准确率。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent))

from research.data.index import fetch_index_daily
from research.models.garch import compare_vol_models, fit_garch


def main():
    exp_dir = Path(__file__).parent
    result_dir = exp_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "data" / "cache"
    close = fetch_index_daily("sh000001", "2014-01-01", "2024-12-31", cache)
    ret = close.pct_change(fill_method=None).dropna()

    full = fit_garch(ret, model="GARCH")
    gjr = fit_garch(ret, model="GARCH", o=1)
    cmp_garch = compare_vol_models(ret, window=500, step=20, model="GARCH")
    cmp_gjr = compare_vol_models(ret, window=500, step=20, model="GARCH", o=1)

    report = {
        "experiment": "EXP-20260815-004",
        "hypothesis": "H6: GARCH(1,1) 在 A 股波动率预测上优于历史波动率",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "index": "sh000001",
        "window": "2014-01-01 ~ 2024-12-31",
        "rolling_window_days": 500,
        "garch_fit": full,
        "gjr_garch_fit": gjr,
        "compare_garch": cmp_garch,
        "compare_gjr": cmp_gjr,
    }
    (result_dir / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()


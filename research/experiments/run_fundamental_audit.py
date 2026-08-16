"""EXP-20260816-006：Tier1 价值/规模/质量因子截面覆盖审计（L0）。

数据来自 research/data/fundamental.py 生成的全市场快照。
本步骤只做因子实现、注册、数据覆盖与分位特征审计；
历史估值日线在免费数据源上暂不可得，L1/L2 收益验证待财务数据管线。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.factors.registry import FACTOR_FUNCS

SNAPSHOT = ROOT / "research" / "data" / "cache" / "fundamental_snapshot.csv"
REPORT = ROOT / "research" / "experiments" / "EXP-20260816-006" / "results"


def audit() -> dict:
    if not SNAPSHOT.exists():
        raise SystemExit("请先运行: python3 research/data/fundamental.py 生成快照")
    snap = pd.read_csv(SNAPSHOT, dtype={"code": str})
    snap["code"] = snap["code"].str.zfill(6)

    # 因子快照：每只股票一行（截面），因子函数接受单行 DataFrame
    rows = []
    for name in ("value_ep", "value_bp", "value_dp", "size_logcap",
                 "quality_roe", "quality_gross_margin"):
        fn = FACTOR_FUNCS[name]
        vals = []
        for _, row in snap.iterrows():
            try:
                s = fn(row.to_frame().T)
                vals.append(float(s.iloc[0]) if pd.notna(s.iloc[0]) else np.nan)
            except Exception:
                vals.append(np.nan)
        rows.append({"factor": name, "values": vals})

    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "snapshot": str(SNAPSHOT), "stocks": len(snap), "factors": []}
    lines = ["# EXP-20260816-006: Tier1 价值/规模/质量因子截面审计 (L0)", ""]
    for item in rows:
        fname = item["factor"]
        v = pd.Series(item["values"], dtype=float)
        n = int(v.notna().sum())
        pct = n / len(snap) * 100 if len(snap) else 0
        desc = {
            "factor": fname,
            "coverage": n,
            "coverage_pct": round(pct, 2),
            "mean": round(float(v.mean()), 4) if n else None,
            "median": round(float(v.median()), 4) if n else None,
        }
        out["factors"].append(desc)
        lines.append(f"## {fname}")
        lines.append(f"- 覆盖 {n}/{len(snap)} ({pct:.1f}%)，均值 {desc['mean']}，中位数 {desc['median']}")
        q = v.dropna().quantile([0.2, 0.4, 0.6, 0.8])
        lines.append("- 分位值: " + ", ".join(f"{k:.0%}: {val:.4f}" for k, val in q.items()))
        lines.append("")

    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))

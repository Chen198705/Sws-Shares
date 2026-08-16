"""行业中性化合成数据测试：残差应消除行业均值差异。"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.factors.neutralize import industry_neutralize_matrix


def main():
    rng = np.random.RandomState(42)
    codes = [f"60000{i}" for i in range(1, 11)] + [f"00000{i}" for i in range(1, 11)]
    industry = {c: ("A" if c.startswith("6") else "B") for c in codes}
    dates = pd.date_range("2020-01-01", periods=5, freq="D")
    mat = pd.DataFrame(index=dates, columns=codes, dtype=float)
    for c in codes:
        base = 1.0 if industry[c] == "A" else -1.0
        mat[c] = base + rng.normal(0, 0.2, size=len(dates))
    mat.iloc[1, 0] = np.inf
    mat.iloc[2, -1] = np.nan
    raw_before = mat.copy()
    neutral = industry_neutralize_matrix(mat, industry, min_stocks=10)
    assert mat.equals(raw_before), "中性化不应就地修改输入矩阵"
    assert not np.isfinite(neutral.iloc[1, 0])  # inf 原样保留（缺失处理）
    assert np.isnan(neutral.iloc[2, -1])        # NaN 原样保留
    for d in dates:
        a = neutral.loc[d, [c for c in codes if industry[c] == "A"]].replace([np.inf, -np.inf], np.nan).mean()
        b = neutral.loc[d, [c for c in codes if industry[c] == "B"]].replace([np.inf, -np.inf], np.nan).mean()
        assert abs(a) < 0.3 and abs(b) < 0.3, (d, a, b)
    print("[test_neutralize] OK: industry means removed")


if __name__ == "__main__":
    main()

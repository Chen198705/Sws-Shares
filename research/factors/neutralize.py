"""行业中性化（FEATURE_LIBRARY：行业中性化可选但强烈推荐）。

对每个截面日的因子值做行业哑变量 OLS 回归，取残差作为中性化因子：
    factor_neutral = regress_out(factor, dummy_industries)

避免把"行业 beta"误当成 alpha。行业映射来自基本面快照管线
（research/data/fundamental.py -> research/data/cache/industry_map.json）。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INDUSTRY_MAP = ROOT / "data" / "cache" / "industry_map.json"


def load_industry_map(path=None) -> dict:
    p = Path(path) if path else INDUSTRY_MAP
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    return {str(k): str(v) for k, v in data.items()}


def industry_neutralize_matrix(factor_mat: pd.DataFrame,
                              industry_map: dict = None,
                              min_stocks: int = 30) -> pd.DataFrame:
    """对因子宽表（index=日期, columns=股票代码）逐截面做行业中性化。

    行业哑变量 drop_first 以避免与截距共线；某日有效股票数不足或
    因子几乎无变异时保留原值（不做伪残差）。
    """
    industry_map = load_industry_map() if industry_map is None else industry_map
    codes = [str(c) for c in factor_mat.columns]
    inds = [industry_map.get(str(c).zfill(6), "未分类") for c in codes]
    out = factor_mat.copy()
    for d in factor_mat.index:
        row = factor_mat.loc[d].to_numpy(dtype=float).copy()
        ok = np.isfinite(row)
        if int(ok.sum()) < min_stocks or np.nanstd(row[ok]) == 0:
            continue
        # 每个截面日只保留该日有效的股票，且剔除常量行业哑变量，
        # 避免全缺失行业引入秩亏/病态矩阵导致系数溢出。
        inds_ok = [inds[j] for j, flag in enumerate(ok) if flag]
        dummies = pd.get_dummies(
            pd.Categorical(inds_ok), prefix="ind", drop_first=True
        )
        dummies = dummies.loc[:, dummies.std(axis=0) > 0]
        X = np.ascontiguousarray(
            np.column_stack([np.ones(int(ok.sum())),
                             dummies.to_numpy(dtype=float)])
        )
        lo, hi = np.nanquantile(row[ok], [0.01, 0.99])
        y = np.clip(row[ok], lo, hi).reshape(-1, 1)
        try:
            beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        with np.errstate(all="ignore"):
            resid = y - X @ beta
        row[ok] = np.where(np.isfinite(resid[:, 0]), resid[:, 0], np.nan)
        out.loc[d] = row
    return out

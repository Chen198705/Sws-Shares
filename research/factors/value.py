"""价值类 Tier1 因子：盈利收益率 / 账面市值比 / 股息率。"""

import numpy as np
import pandas as pd


def _series(df: pd.DataFrame, *cols) -> pd.Series:
    for c in cols:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(index=df.index, dtype=float)


def value_ep(df: pd.DataFrame) -> pd.Series:
    """盈利收益率 = 1 / PE(TTM)，越高越便宜。"""
    pe = _series(df, "pe_ttm", "pe", "市盈率-动态")
    out = 1.0 / pe.replace(0, np.nan)
    return out.where(pe > 0)


def value_bp(df: pd.DataFrame) -> pd.Series:
    """账面市值比 = 1 / PB，越高越便宜。"""
    pb = _series(df, "pb", "市净率")
    return 1.0 / pb.replace(0, np.nan)


def value_dp(df: pd.DataFrame) -> pd.Series:
    """股息率（百分数），越高越便宜。"""
    return _series(df, "dv_ratio", "股息率")

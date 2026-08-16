"""规模类 Tier1 因子：log 总市值（小盘 = 低值）。"""

import numpy as np
import pandas as pd


def size_logcap(df: pd.DataFrame) -> pd.Series:
    """log(总市值)。市值列缺失时返回 NaN。"""
    if "total_mv" in df.columns:
        mv = pd.to_numeric(df["total_mv"], errors="coerce")
    elif "总市值" in df.columns:
        mv = pd.to_numeric(df["总市值"], errors="coerce")
    else:
        return pd.Series(index=df.index, dtype=float)
    return np.log(mv.replace(0, np.nan))

"""A 股特色因子：涨跌停计数 / 滚动最大回撤。"""

import numpy as np
import pandas as pd


def _limit_pct(code: str) -> float:
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def astock_limit_up_5d(df: pd.DataFrame) -> pd.Series:
    """过去 5 日涨停次数（含近似封板判定）。"""
    code = str(df["code"].iloc[0]) if "code" in df.columns else ""
    limit = _limit_pct(code)
    if "pct_chg" not in df.columns:
        return pd.Series(index=df.index, dtype=float)
    hit = df["pct_chg"] >= (limit * 100 - 0.5)
    return hit.rolling(5).sum().astype(float)


def astock_maxdd_60d(df: pd.DataFrame) -> pd.Series:
    """60 日最大回撤幅度（>=0，越小越稳）。"""
    close = df["close"]
    roll_max = close.rolling(60, min_periods=20).max()
    dd = close / roll_max - 1
    return (-dd).rolling(60, min_periods=20).max()

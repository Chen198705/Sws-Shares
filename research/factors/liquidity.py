"""流动性类因子：liq_20d_turnover / liq_20d_amt。"""

import numpy as np
import pandas as pd


def liq_20d_turnover(df: pd.DataFrame) -> pd.Series:
    """20 日平均换手率（%）。"""
    if "turnover" not in df.columns:
        return pd.Series(index=df.index, dtype=float)
    return df["turnover"].rolling(20).mean()


def liq_20d_amt(df: pd.DataFrame) -> pd.Series:
    """20 日平均成交额（元）。"""
    if "amount" not in df.columns:
        return pd.Series(index=df.index, dtype=float)
    return df["amount"].rolling(20).mean()


def liq_amihud_20d(df: pd.DataFrame) -> pd.Series:
    """20 日 Amihud 非流动性：mean(|ret| / 成交额) * 1e8。"""
    if "amount" not in df.columns:
        return pd.Series(index=df.index, dtype=float)
    ret = df["close"].pct_change().abs()
    amount = pd.to_numeric(df["amount"], errors="coerce").replace(0, np.nan)
    illiq = (ret / amount).astype(float)
    return (illiq * 1e8).rolling(20).mean()

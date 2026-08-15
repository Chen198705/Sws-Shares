"""流动性类因子：liq_20d_turnover / liq_20d_amt。"""

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

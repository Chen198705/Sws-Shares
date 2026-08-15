"""波动率类因子：vol_60d_realized / vol_20d_atr。"""

import numpy as np
import pandas as pd


def vol_60d_realized(df: pd.DataFrame) -> pd.Series:
    """60 日已实现波动率（年化）。"""
    ret = df["close"].pct_change()
    return ret.rolling(60).std() * np.sqrt(252)


def vol_20d_atr(df: pd.DataFrame) -> pd.Series:
    """20 日 ATR / 收盘价。"""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(20).mean()
    return atr / df["close"]

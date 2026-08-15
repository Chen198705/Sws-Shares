"""规则法 regime 识别（对应 REGIME.md 方法 1）。"""

import numpy as np
import pandas as pd


def detect_regime(close: pd.Series, lookback: int = 60) -> str:
    if len(close) < lookback:
        return "❓ 转换期"
    ma_s = close.rolling(20).mean().iloc[-1]
    ma_l = close.rolling(lookback).mean().iloc[-1]
    vol = close.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252)
    cur = close.iloc[-1]
    if cur > ma_l * 1.05 and ma_s > ma_l:
        return "🐂 牛市 (高波动)" if vol >= 0.20 else "🐂 牛市"
    if cur < ma_l * 0.95 and ma_s < ma_l:
        return "🐻 熊市"
    if abs(cur / ma_l - 1) < 0.05:
        return "🌊 震荡市"
    return "❓ 转换期"


def regime_series(close: pd.Series) -> pd.Series:
    return pd.Series([detect_regime(close.iloc[:i]) for i in range(1, len(close) + 1)], index=close.index)

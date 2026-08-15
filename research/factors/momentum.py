"""动量类因子：mom_12_1 / mom_6_1 / mom_1，按日线计算。"""

import pandas as pd


def momentum(df: pd.DataFrame, months: int, skip: int = 1) -> pd.Series:
    """12-1 动量 = 过去 12 个月收益（剔除最近 1 个月）。"""
    close = df["close"]
    lookback = months * 21  # 粗略按 21 个交易日/月
    skip_days = skip * 21
    return close.shift(skip_days) / close.shift(skip_days + lookback) - 1


def mom_12_1(df: pd.DataFrame) -> pd.Series:
    return momentum(df, 12, 1)


def mom_6_1(df: pd.DataFrame) -> pd.Series:
    return momentum(df, 6, 1)


def mom_1(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    return close / close.shift(21) - 1


def compute_factor_panel(panel: dict, factor_name: str = "mom_6_1") -> pd.DataFrame:
    """兼容别名，委托给因子注册表。"""
    from research.factors.registry import compute_factor_panel as _compute
    return _compute(panel, factor_name)

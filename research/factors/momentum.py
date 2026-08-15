"""动量类因子：mom_12_1 / mom_6_1 / mom_1，按日线计算。"""

import pandas as pd


def momentum(close: pd.Series, months: int, skip: int = 1) -> pd.Series:
    """12-1 动量 = 过去 12 个月收益（剔除最近 1 个月）。"""
    lookback = months * 21  # 粗略按 21 个交易日/月
    skip_days = skip * 21
    return close.shift(skip_days) / close.shift(skip_days + lookback) - 1


def mom_12_1(close: pd.Series) -> pd.Series:
    return momentum(close, 12, 1)


def mom_6_1(close: pd.Series) -> pd.Series:
    return momentum(close, 6, 1)


def mom_1(close: pd.Series) -> pd.Series:
    return close / close.shift(21) - 1


def compute_factor_panel(panel: dict, factor_name: str = "mom_6_1") -> pd.DataFrame:
    """按股票计算因子，返回 {date: code: factor} 宽表。"""
    fn = {"mom_12_1": mom_12_1, "mom_6_1": mom_6_1, "mom_1": mom_1}[factor_name]
    series = {}
    for code, df in panel.items():
        df = df.sort_values("date").dropna(subset=["close"])
        s = fn(df.set_index("date")["close"])
        series[code] = s
    out = pd.DataFrame(series)
    return out

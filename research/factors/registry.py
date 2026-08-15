"""因子注册表：统一按 df（date 索引）计算因子宽表。"""

import pandas as pd

from research.factors.momentum import mom_12_1, mom_6_1, mom_1
from research.factors.volatility import vol_60d_realized, vol_20d_atr
from research.factors.liquidity import liq_20d_turnover, liq_20d_amt, liq_amihud_20d
from research.factors.astock import astock_limit_up_5d, astock_maxdd_60d


FACTOR_FUNCS = {
    "mom_12_1": mom_12_1,
    "mom_6_1": mom_6_1,
    "mom_1": mom_1,
    "vol_60d_realized": vol_60d_realized,
    "vol_20d_atr": vol_20d_atr,
    "liq_20d_turnover": liq_20d_turnover,
    "liq_20d_amt": liq_20d_amt,
    "liq_amihud_20d": liq_amihud_20d,
    "astock_limit_up_5d": astock_limit_up_5d,
    "astock_maxdd_60d": astock_maxdd_60d,
}


def compute_factor_panel(panel: dict, factor_name: str = "mom_6_1") -> pd.DataFrame:
    """按股票计算因子，返回 {date: code: factor} 宽表。"""
    fn = FACTOR_FUNCS[factor_name]
    series = {}
    for code, df in panel.items():
        df = df.sort_values("date").dropna(subset=["close"]).set_index("date")
        series[code] = fn(df)
    return pd.DataFrame(series)

"""绩效指标：年化收益/波动/夏普/回撤/Calmar/Sortino/IC。"""

import numpy as np
import pandas as pd
from scipy import stats


TRADING_DAYS = 252


def _annualize(period: float, periods_per_year: float) -> float:
    return (1 + period) ** periods_per_year - 1


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    roll_max = equity.cummax()
    dd = equity / roll_max - 1
    return float(dd.min())


def performance(returns: pd.Series, rf: float = 0.0) -> dict:
    """returns: 日收益率序列（或月收益率，periods 由调用方控制）。"""
    if returns.empty:
        return {}
    ret = returns.dropna()
    n = len(ret)
    periods_per_year = TRADING_DAYS if (ret.index.diff().median().days if hasattr(ret.index.diff().median(), "days") else 0) <= 2 else 12
    total = (1 + ret).prod() - 1
    ann_ret = (1 + total) ** (periods_per_year / n) - 1
    ann_vol = ret.std(ddof=1) * np.sqrt(periods_per_year)
    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else 0.0
    equity = (1 + ret).cumprod()
    mdd = max_drawdown(equity)
    calmar = ann_ret / abs(mdd) if mdd < 0 else np.nan
    downside = ret[ret < 0].std(ddof=1) * np.sqrt(periods_per_year) if (ret < 0).any() else 0.0
    sortino = (ann_ret - rf) / downside if downside > 0 else np.nan
    win_rate = float((ret > 0).mean()) if n else 0.0
    t_stat = float(ret.mean() / (ret.std(ddof=1) / np.sqrt(n))) if ret.std(ddof=1) > 0 else 0.0
    return {
        "periods": n,
        "total_return": float(total),
        "annualized_return": float(ann_ret),
        "annualized_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "max_drawdown": float(mdd),
        "calmar": float(calmar) if calmar == calmar else None,
        "sortino": float(sortino) if sortino == sortino else None,
        "win_rate": win_rate,
        "t_stat": t_stat,
    }


def rank_ic(factor: pd.Series, forward_ret: pd.Series) -> float:
    """因子值与下一期收益的 Spearman 秩相关。"""
    df = pd.concat([factor, forward_ret], axis=1).dropna()
    if len(df) < 5:
        return float("nan")
    ic, _ = stats.spearmanr(df.iloc[:, 0], df.iloc[:, 1])
    return float(ic)


def ic_series(factor_matrix: pd.DataFrame, fwd_returns: pd.DataFrame, dates: list) -> pd.Series:
    """逐期 rank IC。"""
    ics = []
    for d in dates:
        if d not in factor_matrix.index or d not in fwd_returns.index:
            continue
        ics.append(rank_ic(factor_matrix.loc[d], fwd_returns.loc[d]))
    return pd.Series(ics, index=dates[:len(ics)], dtype=float)

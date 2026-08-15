"""月度再平衡回测引擎：T+1、涨跌停、交易成本。"""

from datetime import timedelta

import numpy as np
import pandas as pd


def _limit_up_down(code: str, prev_close: float, cur_close: float) -> tuple[bool, bool]:
    """按板块粗略判断涨跌停（主板 10%、创业板/科创板 20%、ST 5%）。"""
    if code.startswith(("300", "301", "688")):
        limit = 0.20
    elif code.startswith(("8", "4", "92")):
        limit = 0.30
    else:
        limit = 0.10
    if not prev_close or prev_close <= 0:
        return False, False
    chg = (cur_close - prev_close) / prev_close
    return chg >= limit - 0.005, chg <= -(limit - 0.005)


def build_matrices(panel: dict) -> dict:
    """把 panel 转成统一日历矩阵。"""
    frames = {}
    for field in ["open", "close", "volume", "pct_chg"]:
        series = {}
        for code, df in panel.items():
            s = df.set_index("date")[field] if field in df.columns else pd.Series(dtype=float)
            series[code] = s
        frames[field] = pd.DataFrame(series).sort_index()
    return frames


def monthly_rebalance(
    panel: dict,
    factor_matrix: pd.DataFrame,
    costs: dict,
    top_quantile: float = 0.2,
    max_holdings: int = 20,
    min_listed_days: int = 120,
    min_price: float = 3.0,
    max_price: float = 500.0,
):
    """月度再平衡、次日开盘成交、T+1 持有到下一期。返回组合净值/持仓/IC。"""
    mats = build_matrices(panel)
    close, open_, volume = mats["close"], mats["open"], mats["volume"]
    cal = close.dropna(how="all").index
    rebalance_dates = cal.to_series().groupby(cal.to_period("M")).apply(lambda s: s.iloc[-1])
    rebalance_dates = list(rebalance_dates)

    nav_dates = []
    nav = []
    holdings_log = []
    fwd_returns = pd.DataFrame(index=cal, columns=close.columns, dtype=float)
    valid_close = close.reindex(cal)
    fwd_returns.iloc[:-1] = (valid_close.shift(-1) / valid_close - 1).iloc[:-1]

    cash_ratio_log = []
    for i, d in enumerate(rebalance_dates[:-1]):
        entry = cal[cal > d]
        if len(entry) == 0:
            break
        entry_date = entry[0]
        if entry_date not in factor_matrix.index:
            continue
        factor = factor_matrix.loc[entry_date]
        listed_days = close.loc[:d].notna().sum()
        eligible = factor.notna() & (listed_days >= min_listed_days)
        eligible &= close.loc[entry_date].notna() & open_.loc[entry_date].notna()
        eligible &= volume.loc[entry_date].fillna(0) > 0
        prev_close = close.shift(1).loc[entry_date]
        ups = pd.Series(False, index=close.columns)
        downs = pd.Series(False, index=close.columns)
        for c in close.columns[eligible]:
            if prev_close.get(c) and close.loc[entry_date].get(c):
                u, dn = _limit_up_down(c, prev_close[c], close.loc[entry_date][c])
                ups[c], downs[c] = u, dn
        eligible &= ~ups
        price_ok = close.loc[entry_date].between(min_price, max_price)
        eligible &= price_ok

        # 选 top 分位
        scores = factor[eligible].dropna()
        if scores.empty:
            continue
        n_sel = min(max(1, int(len(scores) * top_quantile)), max_holdings)
        selected = scores.sort_values(ascending=False).head(n_sel).index
        w = 1.0 / len(selected)

        # 持有期收益（entry 开盘买入，下一 rebalance 后开盘卖出）
        end = rebalance_dates[i + 1]
        end_date = cal[cal > end]
        exit_date = end_date[0] if len(end_date) else cal[-1]
        period_ret = 0.0
        realized = 0.0
        for c in selected:
            p0 = open_.loc[entry_date, c]
            p1 = open_.loc[exit_date, c] if exit_date in open_.index else close.loc[cal[cal <= end][-1], c]
            if pd.notna(p0) and pd.notna(p1) and p0 > 0 and p1 > 0:
                period_ret += w * (p1 / p0 - 1)
                realized += 1
        tradable = realized / len(selected) if len(selected) else 0.0
        # 成本只对实际成交仓位收取（双边佣金 + 卖出印花税 + 滑点）
        cost = costs["commission"] * 2 + costs["stamp_duty"] + costs["slippage"] * 2 + costs["transfer_fee"] * 2
        period_ret -= cost * tradable
        nav_dates.append(exit_date)
        nav.append(1.0 if not nav else nav[-1] * (1 + period_ret))
        holdings_log.append({"date": str(entry_date.date()), "holdings": [str(c) for c in selected], "period_ret": period_ret})
        cash_ratio_log.append(tradable)

    nav_series = pd.Series(nav, index=pd.DatetimeIndex(nav_dates), name="nav")
    # IC：每个 entry 日期的因子值与次期收益
    ics = []
    ic_dates = []
    for i, d in enumerate(rebalance_dates[:-1]):
        entry = cal[cal > d]
        if len(entry) == 0:
            break
        entry_date = entry[0]
        if entry_date not in factor_matrix.index:
            continue
        nxt = rebalance_dates[i + 1]
        exit_date = cal[cal > nxt]
        exit_date = exit_date[0] if len(exit_date) else cal[-1]
        fwd = close.loc[exit_date] / open_.loc[entry_date] - 1
        f = factor_matrix.loc[entry_date]
        df = pd.concat([f, fwd], axis=1).dropna()
        if len(df) >= 5 and df.iloc[:, 0].nunique() > 1:
            ics.append(df.iloc[:, 0].corr(df.iloc[:, 1], method="spearman"))
            ic_dates.append(entry_date)
    return {
        "nav": nav_series,
        "holdings": holdings_log,
        "ic": pd.Series(ics, index=pd.DatetimeIndex(ic_dates), name="rank_ic"),
        "cash_ratio": float(np.mean(cash_ratio_log)) if cash_ratio_log else 1.0,
        "fwd_returns": fwd_returns,
        "rebalance_dates": rebalance_dates,
    }

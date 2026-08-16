"""OLS 多因子截面回归（MODEL_LIBRARY M1）。

逐月截面回归 R_i,t = alpha + sum(beta_k * factor_k,t) + epsilon，聚合各期系数，
报告样本内/样本外平均系数、Newey-West t 与平均 R2，作为 rank IC 之外的
因子有效性交叉证据与风险分解。
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm

from research.backtest.engine import build_matrices


def monthly_fwd_returns(panel: dict, horizon_days: int = 21) -> pd.DataFrame:
    """月末收盘信号 -> 次期收益（close-to-close，与回测对齐但使用月末对齐）。"""
    mats = build_matrices(panel)
    close = mats["close"]
    cal = close.dropna(how="all").index
    rebalance_dates = list(
        cal.to_series().groupby(cal.to_period("M")).apply(lambda s: s.iloc[-1])
    )
    fwd = pd.DataFrame(index=cal, columns=close.columns, dtype=float)
    # 月末 d 的收益 = close[d + horizon_days] / close[d] - 1（无前视：d 收盘已知）
    shifted = close.shift(-horizon_days)
    fwd.loc[rebalance_dates] = (shifted.loc[rebalance_dates] / close.loc[rebalance_dates] - 1)
    return fwd


def cross_sectional_ols(factor_mats: Dict[str, pd.DataFrame], fwd_returns: pd.DataFrame,
                        factors: List[str], dates: Optional[List] = None,
                        winsorize: float = 0.01, zscore: bool = True,
                        min_nobs: int = 30) -> dict:
    """逐期截面 OLS，返回系数表与聚合统计。"""
    if dates is None:
        all_dates = factor_mats[factors[0]].index
        for f in factors[1:]:
            all_dates = all_dates.union(factor_mats[f].index)
        dates = all_dates.intersection(fwd_returns.index)
    coef_rows = []
    meta = []
    for d in dates:
        try:
            x = pd.DataFrame({f: factor_mats[f].loc[d] for f in factors})
        except KeyError:
            continue
        x = x.dropna(axis=0, how="any")
        y = fwd_returns.loc[d].reindex(x.index)
        df = pd.concat([y.rename("y"), x], axis=1).dropna()
        if len(df) < min_nobs:
            continue
        yv = df["y"].clip(-0.21, 0.21)  # 涨跌停/新股噪声粗清洗
        xv = df[factors].astype(float)
        if winsorize:
            xv = xv.clip(xv.quantile(winsorize), xv.quantile(1 - winsorize), axis=1)
        if zscore:
            xv = (xv - xv.mean()) / (xv.std(ddof=0) + 1e-12)
        X = sm.add_constant(xv)
        res = sm.OLS(yv, X).fit()
        row = {"date": d, "r2": float(res.rsquared), "nobs": int(res.nobs)}
        for f in factors:
            try:
                row[f"coef_{f}"] = float(res.params.get(f, np.nan))
                row[f"t_{f}"] = float(res.tvalues.get(f, np.nan))
                row[f"p_{f}"] = float(res.pvalues.get(f, np.nan))
            except Exception:
                row[f"coef_{f}"] = np.nan
                row[f"t_{f}"] = np.nan
                row[f"p_{f}"] = np.nan
        coef_rows.append(row)
        meta.append({"date": d, "r2": row["r2"], "nobs": row["nobs"]})
    if not coef_rows:
        return {"ok": False, "reason": "no_cross_sections"}
    tbl = pd.DataFrame(coef_rows).set_index("date")
    summary = {"ok": True, "periods": len(tbl), "avg_r2": float(tbl["r2"].mean()),
               "avg_nobs": int(tbl["nobs"].mean()), "factors": factors,
               "coefficients": {}, "meta": meta}
    for f in factors:
        coefs = tbl[f"coef_{f}"].dropna()
        ts = tbl[f"t_{f}"].dropna()
        if coefs.empty:
            continue
        # Newey-West 调整的 t（用系数序列直接 HAC）
        cser = pd.Series(coefs.values)
        m = sm.OLS(cser, np.ones(len(cser))).fit(cov_type="HAC", cov_kwds={"maxlags": 4})
        summary["coefficients"][f] = {
            "mean_coef": float(coefs.mean()),
            "median_coef": float(coefs.median()),
            "positive_share": float((coefs > 0).mean()),
            "t_stat_nw": float(m.tvalues.iloc[0]),
            "p_value_nw": float(m.pvalues.iloc[0]),
            "mean_t": float(ts.mean()),
            "sig_share": float((abs(ts) > 2).mean()),
        }
    return summary


def run_ols_audit(panel: dict, factor_mats: Dict[str, pd.DataFrame], factors: List[str],
                  split_date: str, fwd: Optional[pd.DataFrame] = None) -> dict:
    """样本内/样本外分段 OLS 审计，输出报告 dict。"""
    if fwd is None:
        fwd = monthly_fwd_returns(panel)
    all_dates = factor_mats[factors[0]].index
    for f in factors[1:]:
        all_dates = all_dates.union(factor_mats[f].index)
    dates = all_dates.intersection(fwd.index)
    in_dates = [d for d in dates if d < pd.Timestamp(split_date)]
    oos_dates = [d for d in dates if d >= pd.Timestamp(split_date)]
    return {
        "in_sample": cross_sectional_ols(factor_mats, fwd, factors, in_dates),
        "out_of_sample": cross_sectional_ols(factor_mats, fwd, factors, oos_dates),
    }


if __name__ == "__main__":
    print("ols_factor module")

"""正则化截面回归（MODEL_LIBRARY M2：Ridge / LASSO）。

与 ols_factor 同一输入契约：因子矩阵 (date × stock) + 未来收益矩阵，
逐期做标准化截面回归，Ridge 用闭式解，LASSO 用坐标下降（纯 numpy）。
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from research.models.ols_factor import monthly_fwd_returns


def _standardize(x: np.ndarray) -> np.ndarray:
    return (x - x.mean(axis=0)) / (x.std(axis=0, ddof=0) + 1e-12)


def _ridge_fit(x: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    n = x.shape[0]
    x = np.column_stack([np.ones(n), _standardize(x)])
    xtx = x.T @ x
    pen = np.eye(xtx.shape[0]) * alpha
    pen[0, 0] = 0.0
    return np.linalg.solve(xtx + pen, x.T @ y)


def _lasso_fit(x: np.ndarray, y: np.ndarray, alpha: float,
               max_iter: int = 500, tol: float = 1e-6) -> np.ndarray:
    n, p = x.shape
    x = _standardize(x)
    beta = np.zeros(p)
    intercept = float(np.mean(y))
    for _ in range(max_iter):
        old = beta.copy()
        for j in range(p):
            r = y - intercept - x @ beta + x[:, j] * beta[j]
            rho = x[:, j] @ r
            beta[j] = np.sign(rho) * max(0.0, abs(rho) - alpha) / max(n, 1)
        if np.max(np.abs(beta - old)) < tol:
            break
    return np.concatenate([[intercept], beta])


def cross_sectional_regularized(factor_mats: Dict[str, pd.DataFrame],
                                fwd_returns: pd.DataFrame,
                                factors: List[str],
                                method: str = "ridge",
                                alpha: float = 1.0,
                                dates: Optional[List] = None,
                                winsorize: float = 0.01,
                                min_nobs: int = 30) -> dict:
    """逐期截面 Ridge/LASSO，输出与 OLS 审计同构的汇总。"""
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
        yv = df["y"].clip(-0.21, 0.21).to_numpy(dtype=float)
        xv = df[factors].astype(float).to_numpy(dtype=float)
        if winsorize:
            lo = np.nanquantile(xv, winsorize, axis=0)
            hi = np.nanquantile(xv, 1 - winsorize, axis=0)
            xv = np.clip(xv, lo, hi)
        if method == "ridge":
            beta = _ridge_fit(xv, yv, alpha)
        elif method == "lasso":
            beta = _lasso_fit(xv, yv, alpha)
        else:
            raise ValueError(f"unknown method: {method}")
        pred = np.column_stack([np.ones(len(xv)), _standardize(xv)]) @ beta
        ss_res = float(np.sum((yv - pred) ** 2))
        ss_tot = float(np.sum((yv - yv.mean()) ** 2))
        row = {"date": d, "r2": 1 - ss_res / max(ss_tot, 1e-12),
               "nobs": int(len(df))}
        for j, f in enumerate(factors):
            row[f"coef_{f}"] = float(beta[j + 1])
        coef_rows.append(row)
        meta.append({"date": d, "r2": row["r2"], "nobs": row["nobs"]})
    if not coef_rows:
        return {"ok": False, "reason": "no_cross_sections"}
    tbl = pd.DataFrame(coef_rows).set_index("date")
    summary = {"ok": True, "method": method, "alpha": alpha,
               "periods": len(tbl), "avg_r2": float(tbl["r2"].mean()),
               "avg_nobs": int(tbl["nobs"].mean()), "factors": factors,
               "coefficients": {}, "meta": meta}
    for f in factors:
        coefs = tbl[f"coef_{f}"].dropna()
        if coefs.empty:
            continue
        summary["coefficients"][f] = {
            "mean_coef": float(coefs.mean()),
            "median_coef": float(coefs.median()),
            "positive_share": float((coefs > 0).mean()),
            "abs_mean_coef": float(coefs.abs().mean()),
        }
    return summary


def run_regularized_audit(panel: dict,
                          factor_mats: Dict[str, pd.DataFrame],
                          factors: List[str],
                          split_date: str,
                          alphas: Optional[dict] = None,
                          fwd: Optional[pd.DataFrame] = None) -> dict:
    if fwd is None:
        fwd = monthly_fwd_returns(panel)
    all_dates = factor_mats[factors[0]].index
    for f in factors[1:]:
        all_dates = all_dates.union(factor_mats[f].index)
    dates = all_dates.intersection(fwd.index)
    in_dates = [d for d in dates if d < pd.Timestamp(split_date)]
    oos_dates = [d for d in dates if d >= pd.Timestamp(split_date)]
    alphas = alphas or {"ridge": 1.0, "lasso": 0.001}
    out = {}
    for method in ("ridge", "lasso"):
        out[method] = {
            "in_sample": cross_sectional_regularized(
                factor_mats, fwd, factors, method, alphas[method], in_dates),
            "out_of_sample": cross_sectional_regularized(
                factor_mats, fwd, factors, method, alphas[method], oos_dates),
        }
    return out

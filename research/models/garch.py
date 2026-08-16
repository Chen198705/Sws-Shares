"""GARCH(1,1) / GJR-GARCH 波动率模型（MODEL_LIBRARY M3，H6）。

目标：用 GARCH 预测次日波动率，并与历史波动率（rolling 20d std）比较，
判断在 A 股宽基指数上是否更优（MSE / QLIKE / 方向准确率）。
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from arch import arch_model


def _realized_vol(returns: pd.Series, window: int = 20) -> pd.Series:
    return returns.rolling(window).std() * np.sqrt(252)


def _annualize_daily(daily_vol: float) -> float:
    return float(daily_vol) * np.sqrt(252)


def fit_garch(returns: pd.Series, model: str = "GARCH", p: int = 1, o: int = 0,
              q: int = 1, horizon: int = 1) -> dict:
    """拟合 GARCH/GJR-GARCH，返回参数与下一期波动率预测。"""
    clean = returns.dropna()
    if len(clean) < 60:
        return {"ok": False, "reason": "insufficient_data"}
    try:
        am = arch_model(clean * 100.0, mean="Constant", vol=model, p=p, o=o, q=q,
                        dist="Normal")
        res = am.fit(disp="off", show_warning=False)
        fc = res.forecast(horizon=horizon)
        var_next = float(fc.variance.iloc[-1, 0]) / 10000.0  # 从百分比方差还原
        params = res.params.to_dict()
        omega = params.get("omega", 0.0)
        alpha = params.get("alpha[1]", 0.0)
        beta = params.get("beta[1]", 0.0)
        gamma = params.get("gamma[1]", 0.0)
        return {
            "ok": True,
            "model": model,
            "params": {
                "omega": float(omega),
                "alpha": float(alpha),
                "beta": float(beta),
                "gamma": float(gamma) if o else None,
                "persistence": float(alpha + beta + 0.5 * gamma) if o else float(alpha + beta),
            },
            "loglik": float(res.loglikelihood),
            "nobs": int(res.nobs),
            "last_cond_vol": _annualize_daily(np.sqrt(res.conditional_volatility.iloc[-1]) / 100.0),
            "next_vol_forecast": _annualize_daily(np.sqrt(max(var_next, 1e-12))),
        }
    except Exception as e:  # arch 数值失败时留给调用方记录
        return {"ok": False, "reason": str(e)[:200]}


def rolling_forecast(returns: pd.Series, window: int = 500, step: int = 20,
                     model: str = "GARCH", o: int = 0) -> pd.DataFrame:
    """滚动训练 GARCH，输出每步次日波动率预测 vs 已实现波动率。"""
    clean = returns.dropna()
    rows = []
    for i in range(window, len(clean), step):
        train = clean.iloc[i - window:i]
        target_date = clean.index[i]
        out = fit_garch(train, model=model, o=o, horizon=1)
        if not out["ok"]:
            continue
        rv = _realized_vol(clean.iloc[max(0, i - 20):i])
        realized = rv.iloc[-1] if len(rv) else np.nan
        rows.append({
            "date": target_date,
            "pred_garch": out["next_vol_forecast"],
            "realized_20d": realized,
            "alpha": out["params"]["alpha"],
            "beta": out["params"]["beta"],
            "persistence": out["params"]["persistence"],
        })
    return pd.DataFrame(rows).set_index("date")


def compare_vol_models(returns: pd.Series, window: int = 500, step: int = 20,
                       model: str = "GARCH", o: int = 0) -> dict:
    """GARCH vs 历史波动率：MSE / QLIKE / 方向准确率。"""
    clean = returns.dropna()
    hist = _realized_vol(clean)
    fc = rolling_forecast(clean, window=window, step=step, model=model, o=o)
    if fc.empty:
        return {"ok": False, "reason": "no_forecasts"}
    # actual = 预测日当天的 20d 已实现波动率；pred_hist = 预测日前一日已实现波动率
    df = fc.join(hist.rename("realized_next"), how="inner").dropna(
        subset=["realized_20d", "realized_next"])
    if df.empty:
        return {"ok": False, "reason": "no_aligned_data"}
    # 基准：上一期 realized 20d 作为次日预测
    df["pred_hist"] = df["realized_20d"]
    actual = df["realized_next"]
    mse_g = float(((df["pred_garch"] - actual) ** 2).mean())
    mse_h = float(((df["pred_hist"] - actual) ** 2).mean())
    qlike_g = float((actual / df["pred_garch"] - np.log(actual / df["pred_garch"]) - 1).mean())
    qlike_h = float((actual / df["pred_hist"] - np.log(actual / df["pred_hist"]) - 1).mean())
    # 方向准确率：预测波动率相对上一期上升/下降是否与实现一致
    dg = ((df["pred_garch"].diff() > 0) == (actual.diff() > 0)).mean()
    dh = ((df["pred_hist"].diff() > 0) == (actual.diff() > 0)).mean()
    return {
        "ok": True,
        "model": model,
        "periods": int(len(df)),
        "mse_garch": mse_g,
        "mse_hist": mse_h,
        "mse_ratio": float(mse_g / mse_h) if mse_h > 0 else None,
        "qlike_garch": qlike_g,
        "qlike_hist": qlike_h,
        "direction_acc_garch": float(dg),
        "direction_acc_hist": float(dh),
        "better_by_mse": mse_g < mse_h,
        "better_by_qlike": qlike_g < qlike_h,
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "data/cache/index_sh000001.csv"
    df = pd.read_csv(path, parse_dates=["date"])
    ret = df.set_index("date")["close"].pct_change(fill_method=None)
    print(compare_vol_models(ret))

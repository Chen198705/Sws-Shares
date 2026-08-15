"""基于宽基指数的 regime 识别与状态输出。"""

import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

from research.regime.detect import detect_regime


def regime_series(close: pd.Series, lookback: int = 60) -> pd.Series:
    """逐日标注 regime（O(n^2) 只用于标注周期，数据量大时改为月末抽样）。"""
    labels = []
    for i in range(1, len(close) + 1):
        labels.append(detect_regime(close.iloc[:i], lookback=lookback))
    return pd.Series(labels, index=close.index)


def monthly_regime_labels(close: pd.Series, lookback: int = 60) -> pd.DataFrame:
    """每个交易日的 regime + 月末汇总，返回 {date: label} 与 {month: label}。"""
    daily = regime_series(close, lookback)
    monthly = daily.to_frame("regime").groupby(daily.index.to_period("M")).tail(1)
    return daily, monthly["regime"]


def regime_metrics(close: pd.Series, lookback: int = 60) -> dict:
    if len(close) < lookback:
        return {}
    ma_s = close.rolling(20).mean().iloc[-1]
    ma_l = close.rolling(lookback).mean().iloc[-1]
    vol = close.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252)
    return {
        "close": float(close.iloc[-1]),
        "ma20": float(ma_s),
        f"ma{lookback}": float(ma_l),
        "vol_20d_annualized": float(vol),
        "deviation_ma_long": float(close.iloc[-1] / ma_l - 1),
        "state": detect_regime(close, lookback),
    }


def save_regime_state(close: pd.Series, out_path: Path, lookback: int = 60):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    daily, monthly = monthly_regime_labels(close, lookback)
    last_days = daily.tail(30)
    state = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "index": "sh000001",
        "metrics": regime_metrics(close, lookback),
        "last_30d": [{"date": str(d.date()), "regime": r} for d, r in last_days.items()],
        "monthly": [{"month": str(p), "regime": r} for p, r in monthly.items()],
    }
    out_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state

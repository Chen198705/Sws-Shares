"""因子拥挤度监控（Horenstein 思路：夏普下降/波动放大/收益转负）。"""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from research.factors.registry import FACTOR_FUNCS, compute_factor_panel


def _winsorize_z(series: pd.Series) -> pd.Series:
    lo, hi = series.quantile([0.01, 0.99])
    clipped = series.clip(lo, hi)
    std = clipped.std()
    if not std or np.isnan(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return (clipped - clipped.mean()) / std


def factor_returns(panel: dict, factor_name: str, min_stocks: int = 20) -> pd.Series:
    """日度因子多空收益：z_t * r_{t->t+1} 的截面均值。"""
    z = compute_factor_panel(panel, factor_name).apply(_winsorize_z, axis=1)
    closes = pd.concat(
        [df.set_index("date")["close"] for df in panel.values()],
        axis=1,
    ).sort_index()
    fwd = closes.pct_change(fill_method=None).shift(-1)
    common = z.index.intersection(fwd.index)
    z = z.loc[common]
    fwd = fwd.loc[common]
    n = z.notna().sum(axis=1)
    num = (z * fwd).sum(axis=1)
    den = z.abs().sum(axis=1).replace(0, np.nan)
    fr = num / den
    fr[n < min_stocks] = np.nan
    return fr.dropna()


def crowding_metrics(factor_ret: pd.Series) -> dict:
    if len(factor_ret) < 90:
        return {"available": False, "note": "样本不足 90 日"}
    w = 60
    prev_w = 120
    recent = factor_ret.tail(w)
    prev = factor_ret.iloc[-prev_w - w:-w] if len(factor_ret) >= prev_w + w else pd.Series(dtype=float)
    sharpe = recent.mean() / recent.std() * np.sqrt(252) if recent.std() else 0.0
    vol = recent.std() * np.sqrt(252)
    prev_sharpe = prev.mean() / prev.std() * np.sqrt(252) if len(prev) and prev.std() else 0.0
    prev_vol = prev.std() * np.sqrt(252) if len(prev) else np.nan
    flags = []
    if prev_sharpe > 0 and sharpe < prev_sharpe * 0.5:
        flags.append("夏普较前120日下降>50%")
    if np.isfinite(prev_vol) and vol > prev_vol * 1.3:
        flags.append("波动较前120日放大>30%")
    if recent.mean() < 0:
        flags.append("近60日因子收益转负")
    return {
        "available": True,
        "sharpe_60d": round(float(sharpe), 3),
        "vol_60d": round(float(vol), 4),
        "sharpe_prev_120d": round(float(prev_sharpe), 3),
        "vol_prev_120d": round(float(prev_vol), 4) if np.isfinite(prev_vol) else None,
        "factor_ret_60d": round(float(recent.mean()), 6),
        "flags": flags,
        "crowded": len(flags) >= 2,
        "periods": int(len(recent)),
    }


def run(panel: dict, out_path: Path = None) -> dict:
    out_path = Path(out_path) if out_path else ROOT / "export" / "factor_crowding.json"
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stocks": len(panel),
        "factors": {},
    }
    for name in FACTOR_FUNCS:
        try:
            fr = factor_returns(panel, name)
            report["factors"][name] = crowding_metrics(fr)
        except Exception as e:
            report["factors"][name] = {"available": False, "error": str(e)[:200]}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run({}), ensure_ascii=False, indent=2))

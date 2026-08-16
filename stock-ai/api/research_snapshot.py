"""研究层基本面快照只读访问（PB/市值），供信号层 scanner/analyzer 共用。"""

import time
from pathlib import Path

import pandas as pd


_SNAP_CACHE = {"ts": 0.0, "df": None}


def fundamental_snapshot():
    """全市场基本面快照（code -> pb/total_mv），5 分钟缓存；缺失返回空表。"""
    now = time.time()
    if _SNAP_CACHE["df"] is not None and now - _SNAP_CACHE["ts"] <= 300:
        return _SNAP_CACHE["df"]
    p = Path(__file__).resolve().parents[2] / "research" / "data" / "cache" / "fundamental_snapshot.csv"
    try:
        if p.exists():
            df = pd.read_csv(p, usecols=["code", "pb", "total_mv"],
                             dtype={"code": str})
            df["code"] = df["code"].str.zfill(6)
            _SNAP_CACHE["df"] = df.set_index("code")
        else:
            _SNAP_CACHE["df"] = pd.DataFrame()
    except Exception:
        _SNAP_CACHE["df"] = pd.DataFrame()
    _SNAP_CACHE["ts"] = now
    return _SNAP_CACHE["df"]


def value_bp_metric(code: str):
    """返回 (pb, bp)；无快照/无 PB 时返回 (None, None)。"""
    snap = fundamental_snapshot()
    if snap.empty or code not in snap.index:
        return None, None
    pb = snap.loc[code, "pb"]
    try:
        pb = float(pb)
    except (TypeError, ValueError):
        return None, None
    if pd.isna(pb) or pb <= 0:
        return None, None
    return pb, 1.0 / pb

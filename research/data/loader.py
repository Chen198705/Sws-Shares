"""AKShare 日线加载器：拉取 + 标准化 + 本地缓存。"""

import json
import time
from pathlib import Path

import akshare as ak
import pandas as pd


COLUMNS = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "涨跌幅": "pct_chg",
    "换手率": "turnover",
}


def _normalize(df: pd.DataFrame, code: str) -> pd.DataFrame:
    df = df.rename(columns=COLUMNS)
    keep = ["date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = code
    for c in ["open", "close", "high", "low", "amount", "pct_chg", "turnover"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    if "pct_chg" not in df.columns and len(df):
        df["pct_chg"] = df["close"].pct_change() * 100
    return df.sort_values("date").reset_index(drop=True)


def _sina_symbol(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def fetch_daily_em(code: str, start: str, end: str, adjust: str = "", retry: int = 3):
    last_err = None
    for i in range(retry):
        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust=adjust,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return _normalize(df, code)
        except Exception as e:
            last_err = e
            time.sleep(1 + i * 2)
    raise RuntimeError(f"eastmoney failed: {last_err}")


def fetch_daily_sina(code: str, start: str, end: str, adjust: str = ""):
    """新浪接口兜底，字段较少但覆盖更稳。"""
    df = ak.stock_zh_a_daily(
        symbol=_sina_symbol(code),
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"date": "date", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume", "amount": "amount", "turnover": "turnover"})
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = code
    for c in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "pct_chg" not in df.columns:
        df["pct_chg"] = df["close"].pct_change() * 100
    return df.sort_values("date").reset_index(drop=True)


def fetch_daily(code: str, start: str, end: str, adjust: str = "", retry: int = 3):
    try:
        return fetch_daily_em(code, start, end, adjust, retry)
    except Exception as e:
        time.sleep(1)
        return fetch_daily_sina(code, start, end, adjust)


def _known_failures(cache_dir: Path, adjust: str) -> set:
    f = cache_dir / f"_fetch_failures_{adjust or 'raw'}.json"
    if not f.exists():
        return set()
    try:
        data = json.loads(f.read_text())
    except Exception:
        return set()
    return {str(x.get("code")) for x in data if isinstance(x, dict)}


def load_panel(codes, start, end, cache_dir, adjust="", retry=3):
    """返回 {code: DataFrame}；命中缓存直接读取。"""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    panel = {}
    failures = []
    skip = _known_failures(cache, adjust)
    for code in codes:
        f = cache / f"{code}_{adjust or 'raw'}.csv"
        if f.exists():
            df = pd.read_csv(f, parse_dates=["date"])
            panel[code] = df
            continue
        if code in skip:
            continue
        try:
            df = fetch_daily(code, start, end, adjust, retry)
            if not df.empty:
                df.to_csv(f, index=False)
                panel[code] = df
            else:
                failures.append({"code": code, "reason": "empty"})
        except Exception as e:
            failures.append({"code": code, "reason": str(e)[:200]})
        time.sleep(0.3)
    if failures:
        (cache / "_fetch_failures.json").write_text(
            __import__("json").dumps(failures, ensure_ascii=False, indent=2)
        )
        print(f"[data] {len(failures)} 只股票拉取失败: {[f['code'] for f in failures]}")
    return panel

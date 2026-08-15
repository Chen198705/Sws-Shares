"""宽基指数日线（AKShare），用于 regime 识别与归因上下文。"""

import time
from pathlib import Path

import akshare as ak
import pandas as pd


def fetch_index_daily(symbol: str = "sh000001", start: str = "2014-01-01",
                      end: str = None, cache_dir: Path = None,
                      retry: int = 3) -> pd.Series:
    """返回 {date: close} 序列；优先缓存。"""
    cache_dir = Path(cache_dir) if cache_dir else Path("data/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"index_{symbol}.csv"
    if cache_file.exists():
        df = pd.read_csv(cache_file, parse_dates=["date"])
        s = df.set_index("date")["close"].sort_index()
    else:
        last_err = None
        for i in range(retry):
            try:
                df = ak.stock_zh_index_daily(symbol=symbol)
                df = df.rename(columns={"date": "date", "close": "close"})
                df["date"] = pd.to_datetime(df["date"])
                df = df[["date", "close"]].dropna()
                df.to_csv(cache_file, index=False)
                s = df.set_index("date")["close"].sort_index()
                break
            except Exception as e:
                last_err = e
                time.sleep(1 + i * 2)
        else:
            raise RuntimeError(f"index fetch failed: {last_err}")
    s = s[s.index >= pd.Timestamp(start)]
    if end:
        s = s[s.index <= pd.Timestamp(end)]
    return s

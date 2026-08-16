"""历史估值/财务指标管线（EXP-009 数据源）。

估值：ak.stock_value_em —— 2018 年至今逐日 PE(TTM)/PB/市值，断点续传。
财务：ak.stock_financial_analysis_indicator —— 2018 年至今季度 ROE/毛利率，
按报告期披露时限保守后移再前向填充，避免前视。
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "research" / "data" / "cache"
VALUATION_SUFFIX = "_valuation_em.csv"
FIN_SUFFIX = "_fin_hist.csv"
START_YEAR = 2018

# 报告月份 -> 披露时限（天）：一季报 4/30、中报 8/31、三季报 10/31、年报 4/30
AVAILABILITY_LAG = {3: 45, 6: 75, 9: 45, 12: 120}


def _clean_code(c: str) -> str:
    return str(c).zfill(6)


def fetch_valuation_history(code: str) -> pd.DataFrame:
    """单只股票历史估值：数据日期 / 收盘 / 总市值 / PE(TTM) / PB。"""
    df = ak.stock_value_em(symbol=_clean_code(code))
    if df is None or df.empty:
        return pd.DataFrame()
    keep = ["数据日期", "当日收盘价", "当日涨跌幅", "总市值", "流通市值",
            "PE(TTM)", "PE(静)", "市净率"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df.columns = ["date", "close", "pct_chg", "total_mv", "circ_mv",
                  "pe_ttm", "pe_static", "pb"]
    df["date"] = pd.to_datetime(df["date"])
    for c in ["close", "pct_chg", "total_mv", "circ_mv", "pe_ttm",
              "pe_static", "pb"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["code"] = _clean_code(code)
    return df.sort_values("date").reset_index(drop=True)


def fetch_financial_history(code: str) -> pd.DataFrame:
    """单只股票历史财务指标：报告期 / ROE / 销售毛利率。"""
    df = ak.stock_financial_analysis_indicator(
        symbol=_clean_code(code), start_year=str(START_YEAR)
    )
    if df is None or df.empty:
        return pd.DataFrame()
    keep = [c for c in ["日期", "净资产收益率(%)", "销售毛利率(%)"]
            if c in df.columns]
    df = df[keep].copy()
    df.columns = ["date", "roe", "gross_margin"]
    df["date"] = pd.to_datetime(df["date"])
    for c in ["roe", "gross_margin"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["code"] = _clean_code(code)
    return df.dropna(subset=["roe", "gross_margin"]).sort_values("date")


def availability_date(report_date: pd.Timestamp) -> pd.Timestamp:
    """按报告期映射可用日期（年报/中报/季报披露时限，含周末缓冲）。"""
    lag = AVAILABILITY_LAG.get(report_date.month, 75)
    return report_date + pd.Timedelta(days=lag)


def load_cached(cache_dir: Path, suffix: str, code: str) -> pd.DataFrame:
    f = cache_dir / f"{_clean_code(code)}{suffix}"
    if not f.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(f, parse_dates=["date"], dtype={"code": str})
        return df
    except Exception:
        return pd.DataFrame()


def fetch_one(code: str, with_fin: bool = True, cache_dir: Path = None) -> dict:
    """拉取并缓存单只股票；已有缓存则跳过，返回状态。"""
    cache_dir = Path(cache_dir) if cache_dir else CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)
    code = _clean_code(code)
    val_f = cache_dir / f"{code}{VALUATION_SUFFIX}"
    fin_f = cache_dir / f"{code}{FIN_SUFFIX}"
    status = {"code": code}
    if val_f.exists():
        status["valuation"] = "cached"
    else:
        try:
            df = fetch_valuation_history(code)
            if df.empty:
                status["valuation"] = "empty"
            else:
                df.to_csv(val_f, index=False, encoding="utf-8")
                status["valuation"] = "ok"
        except Exception as e:
            status["valuation"] = f"error:{str(e)[:120]}"
        time.sleep(0.3)
    if with_fin:
        if fin_f.exists():
            status["financial"] = "cached"
        else:
            try:
                df = fetch_financial_history(code)
                if df.empty:
                    status["financial"] = "empty"
                else:
                    df.to_csv(fin_f, index=False, encoding="utf-8")
                    status["financial"] = "ok"
            except Exception as e:
                status["financial"] = f"error:{str(e)[:120]}"
            time.sleep(0.3)
    return status


def fetch_all(codes, with_fin: bool = True, cache_dir: Path = None,
              workers: int = 4) -> list:
    """断点续传全市场；返回失败清单。估值/财务均为网络 IO，可多线程加速。"""
    cache_dir = Path(cache_dir) if cache_dir else CACHE
    fail_f = cache_dir / "_fundamental_history_failures.json"
    known = {}
    if fail_f.exists():
        try:
            known = {x["code"]: x for x in json.loads(fail_f.read_text())}
        except Exception:
            known = {}
    failures = []
    done = 0
    if workers > 1 and len(codes) > 32:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fetch_one, c, with_fin, cache_dir): c
                    for c in codes}
            for fut in as_completed(futs):
                try:
                    st = fut.result()
                except Exception as e:
                    st = {"code": futs[fut], "error": str(e)[:120]}
                done += 1
                if "error" in str(st.get("valuation", "")) or \
                        "error" in str(st.get("financial", "")) or \
                        "error" in st:
                    failures.append(st)
                if done % 100 == 0:
                    print(f"[fundamental_history] {done}/{len(codes)} "
                          f"{datetime.now().strftime('%H:%M:%S')}", flush=True)
    else:
        for code in codes:
            code = _clean_code(code)
            st = fetch_one(code, with_fin, cache_dir)
            done += 1
            if "error" in str(st.get("valuation", "")) or \
                    "error" in str(st.get("financial", "")):
                failures.append(st)
            if done % 50 == 0:
                print(f"[fundamental_history] {done}/{len(codes)} "
                      f"{datetime.now().strftime('%H:%M:%S')}", flush=True)
    if failures:
        fail_f.write_text(json.dumps(failures, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[fundamental_history] done: {done} codes, "
          f"{len(failures)} failures", flush=True)
    return failures


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只拉前 N 只（调试）")
    ap.add_argument("--skip-fin", action="store_true", help="只拉估值不拉财务")
    ap.add_argument("--codes-file", default="", help="代码清单文件（一行一个）")
    ap.add_argument("--workers", type=int, default=4, help="并发拉取线程数")
    args = ap.parse_args()

    if args.codes_file:
        raw = Path(args.codes_file).read_text().split()
        codes = [_clean_code(c) for c in raw]
    else:
        qfq = sorted(CACHE.glob("*_qfq.csv"))
        codes = [f.name.split("_")[0] for f in qfq]
    if args.limit > 0:
        codes = codes[:args.limit]
    print(f"[fundamental_history] targets: {len(codes)}", flush=True)
    fail = fetch_all(codes, with_fin=not args.skip_fin, workers=args.workers)
    print(f"[fundamental_history] failures: {len(fail)}")

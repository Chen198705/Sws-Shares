"""数据质量校验，按 Claude DATA_SOURCE.md 执行。"""

import pandas as pd


def validate_df(df: pd.DataFrame, code: str) -> list:
    issues = []
    if df.empty:
        return [f"{code}: empty"]
    n = len(df)
    if df["close"].isna().sum() / n > 0.05:
        issues.append(f"{code}: close missing >5%")
    if "volume" in df.columns and df["volume"].notna().sum() / n < 0.95:
        issues.append(f"{code}: volume incomplete")
    if (df["high"] < df["low"]).any():
        issues.append(f"{code}: high<low rows={int((df['high'] < df['low']).sum())}")
    if (df["high"] < df["close"]).any() or (df["low"] > df["close"]).any():
        issues.append(f"{code}: OHLC inconsistent")
    if "pct_chg" in df.columns:
        q = df["pct_chg"].abs().quantile(0.999)
        if q > 20:
            issues.append(f"{code}: extreme pct_chg p99.9={q:.1f}%")
    return issues


def validate_panel(panel: dict) -> dict:
    report = {}
    all_issues = []
    for code, df in panel.items():
        issues = validate_df(df, code)
        if issues:
            all_issues += issues
        report[code] = {"rows": len(df), "start": str(df["date"].min().date()), "end": str(df["date"].max().date()), "issues": issues}
    report["_summary"] = {"stocks": len(panel), "with_issues": len(all_issues), "issues": all_issues[:20]}
    return report

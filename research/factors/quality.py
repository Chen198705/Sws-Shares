"""质量类 Tier1 因子：ROE / 销售毛利率。"""

import pandas as pd


def quality_roe(df: pd.DataFrame) -> pd.Series:
    """净资产收益率（百分数），越高越好。"""
    for c in ("roe", "净资产收益率", "净资产收益率-摊薄"):
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(index=df.index, dtype=float)


def quality_gross_margin(df: pd.DataFrame) -> pd.Series:
    """销售毛利率（百分数），越高越好。"""
    for c in ("gross_margin", "销售毛利率"):
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(index=df.index, dtype=float)

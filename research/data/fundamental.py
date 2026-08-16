"""基本面快照管线：全市场估值快照 + 业绩报表 → 因子截面与行业映射。"""

import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "research" / "data" / "cache"
API_INDUSTRY = ROOT / "stock-ai" / "api" / "data" / "industry_map.json"
VALUATION_SUFFIX = "_valuation_em.csv"


def fetch_spot_snapshot() -> pd.DataFrame:
    """全市场实时快照：最新价 / 总市值 / 流通市值 / PE(动态) / PB。"""
    try:
        df = ak.stock_zh_a_spot_em()
        keep = ["代码", "名称", "最新价", "总市值", "流通市值", "市盈率-动态", "市净率"]
        df = df[[c for c in keep if c in df.columns]].copy()
        df.columns = ["code", "name", "close", "total_mv", "circ_mv", "pe", "pb"]
        df["code"] = df["code"].astype(str).str.zfill(6)
        for c in ["close", "total_mv", "circ_mv", "pe", "pb"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception:
        # 东财 push2 偶发不可达时切腾讯快照（总市值/流通市值单位为亿元，无 PB）
        df = ak.stock_zh_a_spot_tx()
        out = pd.DataFrame({
            "code": df["code"].astype(str).str[2:].str.zfill(6),
            "name": df["name"],
            "close": pd.to_numeric(df.get("zxj"), errors="coerce"),
            "total_mv": pd.to_numeric(df.get("zsz"), errors="coerce") * 1e8,
            "circ_mv": pd.to_numeric(df.get("ltsz"), errors="coerce") * 1e8,
            "pe": pd.to_numeric(df.get("pe_ttm"), errors="coerce"),
            "pb": pd.Series(index=df.index, dtype=float),
        })
        return out.dropna(subset=["code"])


def fetch_yjbb(period: str = "20260331") -> pd.DataFrame:
    """全市场业绩报表：ROE / 销售毛利率 / 所处行业。"""
    df = ak.stock_yjbb_em(date=period)
    keep = ["股票代码", "股票简称", "净资产收益率", "销售毛利率", "每股收益", "每股净资产", "所处行业"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df.columns = ["code", "name", "roe", "gross_margin", "eps", "bps", "industry"]
    df["code"] = df["code"].astype(str).str.zfill(6)
    for c in ["roe", "gross_margin", "eps", "bps"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _backfill_pb_from_cache(snap: pd.DataFrame) -> pd.DataFrame:
    """用研究层历史估值缓存回填市净率（东财源不可达时的离线兜底）。"""
    pb = {}
    for f in CACHE.glob(f"*{VALUATION_SUFFIX}"):
        try:
            df = pd.read_csv(f, usecols=["pb"], dtype=float)
            val = df["pb"].dropna().iloc[-1]
            pb[f.name[:6]] = float(val)
        except Exception:
            continue
    if not pb:
        return snap
    pb_df = pd.DataFrame({"code": list(pb), "pb_cache": list(pb.values())})
    snap = snap.merge(pb_df, on="code", how="left")
    snap["pb"] = snap["pb"].fillna(snap["pb_cache"])
    return snap.drop(columns=["pb_cache"])


def build_fundamental_snapshot(period: str = "20260331",
                               out: Path = None) -> dict:
    """合并估值快照与业绩报表，落盘 CSV，并生成行业映射 JSON。"""
    spot = fetch_spot_snapshot()
    yjbb = fetch_yjbb(period)
    snap = spot.merge(
        yjbb[["code", "roe", "gross_margin", "industry"]], on="code", how="left"
    )
    snap = _backfill_pb_from_cache(snap)
    out = Path(out) if out else CACHE / "fundamental_snapshot.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    snap.to_csv(out, index=False, encoding="utf-8")

    industry = {str(r.code): str(r.industry) for r in snap.itertuples()
                if pd.notna(r.industry)}
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": period,
        "stocks": int(len(snap)),
        "with_industry": len(industry),
        "source": ["ak.stock_zh_a_spot_em", "ak.stock_yjbb_em"],
    }
    (out.parent / "fundamental_snapshot_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    for dst in (CACHE / "industry_map.json", API_INDUSTRY):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(industry, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return meta


if __name__ == "__main__":
    print(json.dumps(build_fundamental_snapshot(), ensure_ascii=False, indent=2))

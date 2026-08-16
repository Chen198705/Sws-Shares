"""EXP-20260816-010：L2 分层复验 + 换手衰减 + 容量测试。

对已通过 L1 的因子（低波动 / 低换手 / 低回撤 / value_bp）按：
  1) regime（牛/熊/震荡）分层 IC
  2) 行业组内 IC（行业内选股能力）
  3) 市值三分位 IC
  4) 持有期 1/2/3 个月换手衰减
  5) 容量：10 万 vs 1 亿初始资金下的样本外绩效与最低佣金成本拖累
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.data.loader import load_panel
from research.data.fundamental_history import (
    VALUATION_SUFFIX, valuation_factor_panel,
)
from research.factors.registry import compute_factor_panel
from research.models.ols_factor import monthly_fwd_returns
from research.backtest.engine import monthly_rebalance
from research.backtest.metrics import rank_ic, performance


FACTORS = ["vol_60d_realized", "vol_20d_atr", "liq_20d_turnover",
           "astock_maxdd_60d", "value_bp"]
PRICE_FACTORS = set(FACTORS) - {"value_bp"}
SPLIT = "2023-01-01"
REPORT_DIR = ROOT / "research" / "experiments" / "EXP-20260816-010" / "results"
COSTS = {"stamp_duty": 0.001, "commission": 0.00025,
         "min_commission": 5.0, "slippage": 0.001,
         "transfer_fee": 0.00001}


def _ic_stats(ics) -> dict:
    ics = np.asarray([x for x in ics if np.isfinite(x)], dtype=float)
    if len(ics) == 0:
        return {"periods": 0}
    t = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) \
        if ics.std(ddof=1) > 0 else 0.0
    return {
        "periods": int(len(ics)),
        "mean_ic": float(ics.mean()),
        "median_ic": float(np.median(ics)),
        "positive_share": float((ics > 0).mean()),
        "t_stat": t,
    }


def _date_ics(factor_mat: pd.DataFrame, fwd: pd.DataFrame,
              dates) -> list:
    out = []
    for d in dates:
        if d not in factor_mat.index or d not in fwd.index:
            continue
        ic = rank_ic(factor_mat.loc[d], fwd.loc[d])
        if np.isfinite(ic):
            out.append(ic)
    return out


def _regime_buckets(cache: Path) -> dict:
    """month -> regime bucket（bull/bear/range/transition）。"""
    f = ROOT / "research" / "export" / "regime_history.csv"
    if not f.exists():
        return {}
    hist = pd.read_csv(f, encoding="utf-8-sig", parse_dates=["date"])
    hist = hist.dropna(subset=["rule"]).set_index("date")["rule"].sort_index()
    out = {}
    for d, label in hist.items():
        if "牛" in label:
            b = "bull"
        elif "熊" in label:
            b = "bear"
        elif "震荡" in label:
            b = "range"
        else:
            b = "transition"
        out[pd.Timestamp(d).to_period("M").asfreq("M").start_time] = b
    return out


def _industry_map() -> dict:
    f = ROOT / "stock-ai" / "api" / "data" / "industry_map.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def _industry_ic(factor_mat: pd.DataFrame, fwd: pd.DataFrame, dates,
                 ind_map: dict, min_size: int = 20) -> dict:
    """逐月行业内 rank IC（仅行业样本 >= min_size），聚合为行业分层证据。"""
    per_date = []
    for d in dates:
        if d not in factor_mat.index or d not in fwd.index:
            continue
        f = factor_mat.loc[d].dropna()
        fr = fwd.loc[d].reindex(f.index).dropna()
        df = pd.concat([f, fr], axis=1).dropna()
        if len(df) < min_size:
            continue
        inds = pd.Series({c: ind_map.get(str(c).zfill(6), "未知")
                          for c in df.index})
        df["ind"] = inds.reindex(df.index)
        ics = []
        for _, g in df.groupby("ind"):
            if len(g) < min_size or g.iloc[:, 0].nunique() <= 1:
                continue
            ic = g.iloc[:, 0].corr(g.iloc[:, 1], method="spearman")
            if np.isfinite(ic):
                ics.append(ic)
        if ics:
            per_date.append(float(np.mean(ics)))
    return _ic_stats(per_date)


def _cap_tercile_ic(factor_mat: pd.DataFrame, fwd: pd.DataFrame, dates,
                    size_mat: pd.DataFrame) -> dict:
    """按 log 市值三分位分组计算组内 IC。"""
    out = {"small": [], "mid": [], "large": []}
    for d in dates:
        if d not in factor_mat.index or d not in fwd.index:
            continue
        f = factor_mat.loc[d].dropna()
        fr = fwd.loc[d].reindex(f.index).dropna()
        df = pd.concat([f, fr], axis=1).dropna()
        if d not in size_mat.index:
            continue
        sz = size_mat.loc[d].reindex(df.index).dropna()
        if len(sz) < 90:  # 每个三分位至少约 30 只
            continue
        try:
            labels = pd.qcut(sz, 3, labels=["small", "mid", "large"])
        except ValueError:
            continue
        tmp = df.loc[sz.index].copy()
        tmp["bucket"] = labels
        for b in ("small", "mid", "large"):
            g = tmp[tmp["bucket"] == b]
            if len(g) < 5 or g.iloc[:, 0].nunique() <= 1:
                continue
            ic = g.iloc[:, 0].corr(g.iloc[:, 1], method="spearman")
            if np.isfinite(ic):
                out[b].append(ic)
    return {k: _ic_stats(v) for k, v in out.items()}


def _oos_perf(nav: pd.Series, split: str) -> dict:
    oos = nav[nav.index >= pd.Timestamp(split)]
    ret = oos.pct_change(fill_method=None).dropna()
    return performance(ret)


def _capacity(factor_mat: pd.DataFrame, panel: dict, ascending: bool) -> dict:
    """10 万 vs 1 亿资金样本外绩效，验证最低佣金对小资金拖累。"""
    out = {}
    for capital, tag in ((1e5, "small_100k"), (1e8, "large_100m")):
        res = monthly_rebalance(
            panel, factor_mat, COSTS,
            top_quantile=0.2, max_holdings=20, min_listed_days=120,
            min_price=3.0, max_price=500.0, ascending=ascending,
            initial_capital=capital,
        )
        perf = _oos_perf(res["nav"], SPLIT)
        notional = capital / 20
        min_bind = notional * COSTS["commission"] < COSTS["min_commission"]
        out[tag] = {
            "oos_annualized": perf.get("annualized_return"),
            "oos_sharpe": perf.get("sharpe"),
            "oos_max_drawdown": perf.get("max_drawdown"),
            "periods": perf.get("periods"),
            "min_commission_binding": bool(min_bind),
        }
    return out


def main(sample: int = 0) -> dict:
    cache = ROOT / "research" / "data" / "cache"
    qfq = sorted(cache.glob("*_qfq.csv"))
    codes = [f.name.split("_")[0] for f in qfq]
    codes = [c for c in codes if (cache / f"{c}{VALUATION_SUFFIX}").exists()]
    if sample > 0:
        codes = codes[:sample]
    panel = load_panel(codes, "2014-01-01", "2024-12-31", cache, adjust="qfq")
    print(f"[EXP-010] panel stocks = {len(panel)}")

    factor_mats = {}
    for f in FACTORS:
        if f in PRICE_FACTORS:
            mat = compute_factor_panel(panel, f)
        else:
            mat = valuation_factor_panel(panel, cache, f)
        mat = mat.loc[:, mat.notna().any()]
        factor_mats[f] = mat
        print(f"[EXP-010] {f}: {mat.shape[0]} dates x {mat.shape[1]} stocks")

    size_mat = valuation_factor_panel(panel, cache, "size_logcap")
    size_mat = size_mat.loc[:, size_mat.notna().any()]

    fwd21 = monthly_fwd_returns(panel, horizon_days=21)
    fwd42 = monthly_fwd_returns(panel, horizon_days=42)
    fwd63 = monthly_fwd_returns(panel, horizon_days=63)
    all_dates = factor_mats[FACTORS[0]].index
    for f in FACTORS[1:]:
        all_dates = all_dates.union(factor_mats[f].index)
    dates = sorted(all_dates.intersection(fwd21.index))
    oos = [d for d in dates if d >= pd.Timestamp(SPLIT)]
    reg_buckets = _regime_buckets(cache)
    ind_map = _industry_map()

    result = {
        "experiment": "EXP-20260816-010",
        "hypothesis": "L1 有效因子在 regime/行业/市值分层与容量约束下保持稳健",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stocks": len(panel),
        "split_date": SPLIT,
        "factors": FACTORS,
        "oos_ic": {},
        "regime_ic": {},
        "industry_ic": {},
        "cap_tercile_ic": {},
        "turnover_decay": {},
        "capacity": {},
    }
    for f in FACTORS:
        mat = factor_mats[f]
        result["oos_ic"][f] = _ic_stats(_date_ics(mat, fwd21, oos))
        result["turnover_decay"][f] = {
            "1m": _ic_stats(_date_ics(mat, fwd21, oos)),
            "2m": _ic_stats(_date_ics(mat, fwd42, oos)),
            "3m": _ic_stats(_date_ics(mat, fwd63, oos)),
        }
        by_regime = {}
        for b in ("bull", "bear", "range", "transition"):
            bd = [d for d in dates
                  if reg_buckets.get(pd.Timestamp(d).to_period("M").asfreq("M").start_time) == b]
            if bd:
                by_regime[b] = _ic_stats(_date_ics(mat, fwd21, bd))
        result["regime_ic"][f] = by_regime
        result["industry_ic"][f] = _industry_ic(mat, fwd21, oos, ind_map)
        result["cap_tercile_ic"][f] = _cap_tercile_ic(
            mat, fwd21, oos, size_mat)

    # 容量测试只跑两个代表性方向：低换手（asc）与 value_bp（desc）
    pan18 = {c: df[df["date"] >= "2018-01-01"] for c, df in panel.items()}
    result["capacity"]["liq_20d_turnover"] = _capacity(
        factor_mats["liq_20d_turnover"], pan18, ascending=True)
    result["capacity"]["value_bp"] = _capacity(
        factor_mats["value_bp"], pan18, ascending=False)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    _write_report(result)
    print(f"[EXP-010] done -> {REPORT_DIR}")
    return result


def _fmt_ic(st: dict) -> str:
    if not st.get("periods"):
        return "-"
    return (f"{st['mean_ic']:+.4f} (t={st['t_stat']:.2f}, "
            f"pos={st['positive_share']:.0%}, n={st['periods']})")


def _write_report(out: dict):
    lines = ["# EXP-20260816-010: L2 分层复验 + 换手衰减 + 容量测试", ""]
    lines.append(f"生成时间：{out['generated_at']}，股票数：{out['stocks']}，"
                 f"样本外起点：{out['split_date']}")
    lines.append("")
    lines.append("## OOS rank IC（2023-01-01 起）")
    lines.append("| 因子 | OOS mean_IC | IC t | 正命中率 | 期数 |")
    lines.append("|---|---|---|---|---|")
    for f in out["factors"]:
        s = out["oos_ic"][f]
        lines.append(f"| {f} | {s.get('mean_ic', ''):+.4f} | "
                     f"{s.get('t_stat', ''):.2f} | "
                     f"{s.get('positive_share', 0):.0%} | {s.get('periods', 0)} |")
    lines.append("")
    lines.append("## 按 regime 分层（2018-2024 全样本）")
    lines.append("| 因子 | bull | bear | range | transition |")
    lines.append("|---|---|---|---|---|")
    for f in out["factors"]:
        r = out["regime_ic"][f]
        lines.append(f"| {f} | {_fmt_ic(r.get('bull', {}))} | "
                     f"{_fmt_ic(r.get('bear', {}))} | "
                     f"{_fmt_ic(r.get('range', {}))} | "
                     f"{_fmt_ic(r.get('transition', {}))} |")
    lines.append("")
    lines.append("## 按市值三分位（OOS）")
    lines.append("| 因子 | small | mid | large |")
    lines.append("|---|---|---|---|")
    for f in out["factors"]:
        c = out["cap_tercile_ic"][f]
        lines.append(f"| {f} | {_fmt_ic(c.get('small', {}))} | "
                     f"{_fmt_ic(c.get('mid', {}))} | "
                     f"{_fmt_ic(c.get('large', {}))} |")
    lines.append("")
    lines.append("## 行业内 IC（OOS，行业样本 >= 20）")
    lines.append("| 因子 | 行业组内 mean_IC | IC t | 期数 |")
    lines.append("|---|---|---|---|")
    for f in out["factors"]:
        s = out["industry_ic"][f]
        lines.append(f"| {f} | {s.get('mean_ic', ''):+.4f} | "
                     f"{s.get('t_stat', ''):.2f} | {s.get('periods', 0)} |")
    lines.append("")
    lines.append("## 换手衰减（OOS mean_IC）")
    lines.append("| 因子 | 1m | 2m | 3m |")
    lines.append("|---|---|---|---|")
    for f in out["factors"]:
        d = out["turnover_decay"][f]
        lines.append(f"| {f} | {d['1m'].get('mean_ic', ''):+.4f} | "
                     f"{d['2m'].get('mean_ic', ''):+.4f} | "
                     f"{d['3m'].get('mean_ic', ''):+.4f} |")
    lines.append("")
    lines.append("## 容量测试（10 万 vs 1 亿，月度 20 只，OOS）")
    lines.append("| 因子 | 资金 | 年化 | 夏普 | 最大回撤 | 最低佣金生效 |")
    lines.append("|---|---|---|---|---|---|")
    for f, caps in out["capacity"].items():
        for tag, c in caps.items():
            label = "10万" if tag == "small_100k" else "1亿"
            lines.append(f"| {f} | {label} | {c['oos_annualized']:+.2%} | "
                         f"{c['oos_sharpe']:.2f} | {c['oos_max_drawdown']:.2%} | "
                         f"{'是' if c['min_commission_binding'] else '否'} |")
    (REPORT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    main(args.sample)

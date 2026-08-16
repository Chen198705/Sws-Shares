"""EXP-011 政策事件 CAR 事件研究（真实数据版）。

方法遵循 Claude 的 EXP-011 规范（POLICY_EVENTS.md + config.yaml）：
1. 市场模型 R_i = alpha + beta * R_market + epsilon，估计窗口 [-120, -30]，
   事件前 5 日剔除防污染（实际估计窗口 [-120, -35]）。
2. 事件窗口主检验为 [-1, +5]，同时输出 [-1,+1] / [-5,+5] / [-5,+20]。
3. AR 横截面聚合 AAR，CAR = sum(AAR)。
4. t = CAR / (std(AAR) / sqrt(n_days))，单边 p 按政策类型的预期方向。
5. 每个政策类型内部对事件 p 做 BH 校正；决策只看 BH 校正后 p。
6. 入模条件：n >= min_samples、方向一致率 >= 80%、BH 显著率 >= 50%。

数据纪律：
- 缺失数据一律剔除，不做 fillna(0)。
- 估计窗口观测不足 30 天的股票剔除。
- 事件窗口任一交易日无有效收益的股票剔除。
- 剔除数量写入报告，供审计。

市场基准：项目缓存中的上证指数（index_sh000001.csv）。EXP-011 配置默认
000300.SH，但当前缓存没有沪深 300 指数文件，因此用上证指数作为市场代理，
此偏差记录在报告与 DECISIONS.md 中。
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EVENTS_V1 = ROOT / "research" / "data" / "policy_events.csv"
INDEX = ROOT / "research" / "data" / "cache" / "index_sh000001.csv"
CACHE = ROOT / "research" / "data" / "cache"
OUT_DIR = ROOT / "research" / "experiments" / "EXP-20260816-011" / "results"

EST_START, EST_END = -120, -30
EXCLUDE_DAYS = 5
MIN_EST_OBS = 30
MIN_STOCKS = 50
ALPHA_BH = 0.10
DIR_CONSISTENCY_MIN = 0.80
WINDOWS = [(-1, 1), (-1, 5), (-5, 5), (-5, 20)]
PRIMARY_WINDOW = (-1, 5)

# 与 EXP-011 config.yaml 的 policy_types 对齐；v1 特有的 mp_rrr_cut+broad 按宽松
# 默认 positive 处理（样本只有 1，本身不可能入模）。
POLICY_TYPES = {
    "mp_rrr_cut": {"name": "央行降准", "expected_direction": "positive", "min_samples": 5},
    "mp_rate_cut": {"name": "央行降息", "expected_direction": "positive", "min_samples": 5},
    "mp_lpr_cut": {"name": "LPR下调", "expected_direction": "positive", "min_samples": 3},
    "tax_change": {"name": "印花税调整", "expected_direction": "mixed", "min_samples": 3},
    "ipo_suspend": {"name": "IPO暂停", "expected_direction": "positive", "min_samples": 3},
    "ipo_resume": {"name": "IPO重启", "expected_direction": "negative", "min_samples": 3},
    "industry_plan": {"name": "产业规划", "expected_direction": "positive", "min_samples": 3},
}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bh(p_values: list[float]) -> list[float]:
    """标准 Benjamini-Hochberg 校正，保持单调。"""
    if not p_values:
        return []
    m = len(p_values)
    order = np.argsort(p_values)
    sorted_p = np.asarray(p_values, dtype=float)[order]
    adjusted = np.minimum.accumulate((sorted_p * m / np.arange(1, m + 1))[::-1])[::-1]
    adjusted = np.minimum(adjusted, 1.0)
    out = np.empty(m)
    out[order] = adjusted
    return out.tolist()


def _load_index() -> tuple[pd.Series, pd.Series]:
    df = pd.read_csv(INDEX, parse_dates=["date"])
    close = df.set_index("date")["close"].astype(float).sort_index()
    ret = np.log(close).diff().dropna()
    return close, ret


def _load_returns_panel(index_ret: pd.Series) -> pd.DataFrame:
    """从 qfq 缓存构建全市场对数收益面板，缺失保持 NaN。"""
    calendar = index_ret.index
    closes: dict[str, pd.Series] = {}
    errors = 0
    files = sorted(CACHE.glob("*_qfq.csv"))
    for f in files:
        code = f.name.replace("_qfq.csv", "")
        try:
            df = pd.read_csv(f, usecols=["date", "close"], parse_dates=["date"])
            df = df.dropna(subset=["close"]).drop_duplicates("date")
            df = df.set_index("date")["close"].astype(float).sort_index()
            s = np.log(df).reindex(calendar).diff()
            s = s.replace([np.inf, -np.inf], np.nan)
            closes[code] = s
        except Exception:
            errors += 1
    if errors:
        print(f"[data] {errors}/{len(files)} 个文件读取失败，已跳过")
    panel = pd.DataFrame(closes)
    print(f"[data] qfq 缓存 {len(files)} 个，面板 {panel.shape}")
    return panel


def _one_event_ar(
    panel: pd.DataFrame,
    market: pd.Series,
    t0_loc: int,
) -> tuple[np.ndarray | None, dict]:
    """对一个事件计算 [-5,+20] 的日 AAR 序列（缺失剔除）。"""
    est = slice(t0_loc + EST_START, t0_loc + EST_END - EXCLUDE_DAYS)
    win = slice(t0_loc - 5, t0_loc + 21)
    y_est = panel.iloc[est].to_numpy(dtype=float)
    y_win = panel.iloc[win].to_numpy(dtype=float)
    x_est = market.iloc[est].to_numpy(dtype=float)
    x_win = market.iloc[win].to_numpy(dtype=float)

    est_ok = np.sum(~np.isnan(y_est), axis=0) >= MIN_EST_OBS
    win_ok = ~np.isnan(y_win).any(axis=0)
    valid = est_ok & win_ok
    n_total = int(len(valid))
    n_est_valid = int(valid.sum())
    if n_est_valid < MIN_STOCKS:
        return None, {"n_total": n_total, "n_est_valid": n_est_valid}

    y_est = y_est[:, valid]
    y_win = y_win[:, valid]

    # 每只股票只用其自身有效的估计窗口观测计算 OLS，避免 NaN 传染。
    mask = ~np.isnan(y_est)
    n_obs = mask.sum(axis=0).astype(float)
    sum_y = np.where(mask, y_est, 0.0).sum(axis=0)
    sum_x = np.where(mask, x_est[:, None], 0.0).sum(axis=0)
    sum_xx = np.where(mask, x_est[:, None] ** 2, 0.0).sum(axis=0)
    sum_xy = np.where(mask, x_est[:, None] * y_est, 0.0).sum(axis=0)
    mean_x = sum_x / n_obs
    mean_y = sum_y / n_obs
    var_x = (sum_xx - n_obs * mean_x ** 2) / n_obs
    cov = (sum_xy - n_obs * mean_x * mean_y) / n_obs
    beta = np.where(var_x > 1e-12, cov / var_x, 0.0)
    alpha = mean_y - beta * mean_x

    ar = y_win - alpha - beta * x_win[:, None]
    aar = ar.mean(axis=1)
    return aar, {
        "n_total": n_total,
        "n_est_valid": int(valid.sum()),
        "n_stocks_min": int(valid.sum()),
        "n_obs_est": int(np.median(n_obs)),
    }


def _car_test(aar: np.ndarray, lo: int, hi: int, direction: str) -> dict | None:
    """从 AAR 序列取窗口计算 CAR / t / p。窗口内任一日无有效截面则失败。"""
    start = lo + 5
    end = hi + 6
    seg = aar[start:end]
    if len(seg) == 0 or np.isnan(seg).any():
        return None
    car = float(seg.sum())
    n_days = len(seg)
    std = float(seg.std(ddof=1))
    t = car / (std / math.sqrt(n_days)) if std > 0 else 0.0
    if direction == "negative":
        p = float(_norm_cdf(t))
    elif direction == "mixed":
        p = float(2 * (1 - _norm_cdf(abs(t))))
    else:
        p = float(1 - _norm_cdf(t))
    return {"car": car, "t": t, "p": p, "n_days": n_days}


def _run() -> dict:
    ev = pd.read_csv(EVENTS_V1, dtype={"event_id": str, "event_date": str})
    ev["event_date"] = pd.to_datetime(ev["event_date"])
    print(f"[events] v1 清单 {len(ev)} 条")

    _, market = _load_index()
    calendar = market.index
    panel = _load_returns_panel(market)

    detail_rows = []
    skipped = 0
    for row in ev.itertuples(index=False):
        rec = {
            "event_id": row.event_id,
            "event_date": row.event_date.strftime("%Y-%m-%d"),
            "policy_type": row.policy_type,
            "scope": row.scope,
            "description": row.description,
        }
        pos = calendar.searchsorted(pd.Timestamp(row.event_date))
        if pos >= len(calendar):
            skipped += 1
            detail_rows.append(rec | {"status": "数据不足", "t0": None})
            continue
        t0 = calendar[pos]
        t0_loc = calendar.get_loc(t0)
        if t0_loc + 20 >= len(calendar) or t0_loc + EST_START < 0:
            skipped += 1
            detail_rows.append(rec | {"status": "数据不足", "t0": str(t0.date())})
            continue
        aar, info = _one_event_ar(panel, market, t0_loc)
        if aar is None:
            skipped += 1
            detail_rows.append(rec | {"status": "样本不足", "t0": str(t0.date()), **info})
            continue

        cfg = POLICY_TYPES.get(row.policy_type, {})
        direction = cfg.get("expected_direction", "positive")
        rec |= {"t0": str(t0.date()), "status": "OK", **info}
        tests = {}
        for lo, hi in WINDOWS:
            r = _car_test(aar, lo, hi, direction)
            if r is None:
                tests[f"car_{lo}_{hi}"] = None
            else:
                tests[f"car_{lo}_{hi}"] = round(r["car"], 8)
                if (lo, hi) == PRIMARY_WINDOW:
                    rec |= {"car": r["car"], "t_stat": r["t"], "p_value": r["p"]}
        rec |= tests
        detail_rows.append(rec)

    det_df = pd.DataFrame(detail_rows)

    # 每个政策类型：用主窗口 [-1,+5] 的逐事件 CAR / p 汇总。
    summaries = {}
    for ptype, grp in det_df[det_df["status"] == "OK"].groupby("policy_type"):
        cfg = POLICY_TYPES.get(ptype, {})
        direction = cfg.get("expected_direction", "positive")
        min_samples = int(cfg.get("min_samples", 5))
        cars = grp["car"].astype(float)
        pvals = grp["p_value"].astype(float).tolist()
        bh = _bh(pvals)
        if direction == "negative":
            consistency = float((cars < 0).mean())
        elif direction == "mixed":
            consistency = float((np.sign(cars) == np.sign(cars.mean())).mean()) if len(cars) else 0.0
        else:
            consistency = float((cars > 0).mean())
        rejection = float(np.mean(np.asarray(bh) < ALPHA_BH))
        n = int(len(grp))
        if n < min_samples:
            decision = "样本不足"
            reason = f"样本数 {n} < 最低要求 {min_samples}，无法入模"
        elif consistency < DIR_CONSISTENCY_MIN:
            decision = "拒入"
            reason = f"方向一致率 {consistency:.1%} < {DIR_CONSISTENCY_MIN:.0%}"
        elif rejection < 0.5:
            decision = "拒入"
            reason = f"BH 校正后显著率 {rejection:.1%} < 50%（多数事件不显著）"
        else:
            decision = "入模"
            reason = f"样本 {n}、方向一致率 {consistency:.1%}、BH 显著率 {rejection:.1%}"
        summaries[ptype] = {
            "policy_type": ptype,
            "name": cfg.get("name", ptype),
            "n_events": n,
            "mean_car": round(float(cars.mean()), 6),
            "median_car": round(float(np.median(cars)), 6),
            "std_car": round(float(cars.std(ddof=1)), 6) if n > 1 else 0.0,
            "direction_consistency": round(consistency, 4),
            "rejection_rate_bh": round(rejection, 4),
            "raw_p_values": [round(x, 4) for x in pvals],
            "bh_adjusted_p_values": [round(x, 4) for x in bh],
            "decision": decision,
            "decision_reason": reason,
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    det_df.to_csv(OUT_DIR / "events_detail.csv", index=False, encoding="utf-8-sig")

    # car_per_event.csv：与 Claude 骨架输出对齐（主窗口）。
    rows = []
    for _, r in det_df[det_df["status"] == "OK"].iterrows():
        rows.append({
            "event_id": r["event_id"],
            "policy_type": r["policy_type"],
            "event_date": r["t0"],
            "window": "-1...+5",
            "car": round(float(r["car"]), 4),
            "t_stat": round(float(r["t_stat"]), 3),
            "p_value": round(float(r["p_value"]), 4),
            "n_obs_estimation": int(r["n_obs_est"]),
        })
    pd.DataFrame(rows).to_csv(OUT_DIR / "car_per_event.csv", index=False, encoding="utf-8-sig")

    # car_summary.csv / summary.json：与 Claude 骨架输出对齐。
    sum_rows = [{
        "policy_type": s["policy_type"],
        "n_events": s["n_events"],
        "mean_car": s["mean_car"],
        "median_car": s["median_car"],
        "std_car": s["std_car"],
        "direction_consistency": s["direction_consistency"],
        "rejection_rate_bh": s["rejection_rate_bh"],
        "decision": s["decision"],
        "decision_reason": s["decision_reason"],
    } for s in summaries.values()]
    pd.DataFrame(sum_rows).to_csv(OUT_DIR / "car_summary.csv", index=False, encoding="utf-8-sig")
    json_out = {
        "experiment": "EXP-20260816-011",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "events_version": "v1",
        "events_path": str(EVENTS_V1),
        "data_snapshot": "2026-08-16 cache",
        "market_proxy": "index_sh000001 (上证指数；config 默认 000300.SH 但缓存无此文件)",
        "method": "market model CAR, est [-120,-35], primary window [-1,+5], BH 校正",
        "acceptance": {
            "min_samples": 5,
            "direction_consistency_min": DIR_CONSISTENCY_MIN,
            "alpha_bh": ALPHA_BH,
            "rejection_rate_min": 0.5,
        },
        "summaries": summaries,
        "n_events_skipped": skipped,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(json_out, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(json_out, det_df)

    print(f"[EXP-011] done -> {OUT_DIR}")
    for s in summaries.values():
        print(f"  {s['policy_type']:<18} n={s['n_events']:>2} mean_car={s['mean_car']:+.4f} "
              f"dir={s['direction_consistency']:.0%} bh_sig={s['rejection_rate_bh']:.0%} {s['decision']}")
    return json_out


def _write_report(out: dict, det_df: pd.DataFrame) -> None:
    lines = [
        "# EXP-20260816-011: 政策事件 CAR 事件研究（真实数据）",
        "",
        f"- 生成时间：{out['generated_at']}",
        f"- 数据快照：{out['data_snapshot']}",
        f"- 事件清单：{out['events_version']}（预注册，未事后改动）",
        f"- 市场基准：{out['market_proxy']}",
        f"- 方法：{out['method']}",
        f"- 入模条件：样本≥5（部分类型 3）、方向一致率≥{out['acceptance']['direction_consistency_min']:.0%}、"
        f"BH 校正后 p<{out['acceptance']['alpha_bh']} 且显著率≥{out['acceptance']['rejection_rate_min']:.0%}",
        "",
        "## 1. 政策类型汇总",
        "",
        "| 政策类型 | 样本数 | mean CAR | 方向一致 | BH 显著率 | 决策 | 依据 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in out["summaries"].values():
        lines.append(
            f"| `{s['policy_type']}` | {s['n_events']} | {s['mean_car']:+.4f} | "
            f"{s['direction_consistency']:.1%} | {s['rejection_rate_bh']:.1%} | "
            f"**{s['decision']}** | {s['decision_reason']} |"
        )
    lines += ["", "## 2. 逐事件 CAR（主窗口 [-1,+5]）", "",
              "| event_id | 日期(t0) | 类型 | CAR | t | p |", "|---|---|---|---|---|---|"]
    for _, r in det_df[det_df["status"] == "OK"].iterrows():
        lines.append(
            f"| {r['event_id']} | {r['t0']} | {r['policy_type']} | "
            f"{r['car']:+.4f} | {r['t_stat']:+.3f} | {r['p_value']:.4f} |"
        )
    lines += ["", "## 3. 数据与透明度说明", "",
              "- 缺失数据处理：剔除，不填充 0；估计窗口 <30 观测的股票剔除，"
              "事件窗口任一日无有效收益的股票剔除（详见 events_detail.csv 的 n_est_valid / n_stocks_min）。",
              "- 事件日取公告日后的第一个交易日；公告日在非交易日的按下一交易日计。",
              "- 2015-02-28（降准）与 2015-03-01（降息）映射到同一交易日 2015-03-02，"
              "按预注册清单各自独立计为一次事件，同日多重事件混杂风险已在报告中保留。",
              "- 市场基准使用上证指数，原因：缓存无 000300.SH；与 config.yaml 的默认代理存在偏差，"
              "后续如补充沪深 300 缓存应复跑确认。",
              f"- 本次跳过 {out['n_events_skipped']} 条事件（数据范围不足或样本不足）。",
              "",
              "## 4. 结论",
              "",
              "- 决策只看 BH 校正后结果；raw p 仅作参考。",
              "- 任何入模类型仅代表 L1 事件研究证据，不构成实盘建议；下一步进入 L2 验证。",
    ]
    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", type=int, default=0, help="调试：只跑前 N 条事件")
    args = ap.parse_args()
    _run()

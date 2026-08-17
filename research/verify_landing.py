"""三层架构落地校验：研究层契约 → 信号层因子 → 执行层调度，逐项断言。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "stock-ai" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

import pandas as pd


def check(ok: bool, label: str, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" | {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"落地校验失败: {label}")


def main() -> None:
    contract_path = ROOT / "research" / "export" / "strategy_params.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    check(contract.get("version", "").startswith("2026-08"), "契约版本", contract.get("version"))
    check(contract.get("regime", {}).get("state", ""), "regime 已写入")

    factors = contract.get("factor_constraints") or []
    check(len(factors) == 16, "16 因子约束", f"{len(factors)} factors")
    vb = next((f for f in factors if f["id"] == "value_bp"), None)
    check(vb is not None and vb.get("weight", 0) > 0, "value_bp 权重", str(vb and vb.get("weight")))
    check("通过" in (vb or {}).get("status", "") or "有效" in (vb or {}).get("status", ""), "value_bp 状态", (vb or {}).get("status", ""))

    pf = contract.get("policy_factors") or []
    check(len(pf) >= 5, "政策因子契约", f"{len(pf)} factors")
    p1 = next((f for f in pf if f["id"] == "policy_industry_plan_car5"), None)
    check(p1 is not None and "active" in (p1 or {}).get("status", ""), "policy_industry_plan_car5 登记",
          str(p1 and p1.get("status")))
    check(float(p1.get("weight") or 0) > 0, "政策因子已激活", f"weight={p1.get('weight')}")
    rl = contract.get("risk_limits") or {}
    check(all(k in rl for k in ("max_position_pct", "min_cash_pct", "single_stock_pct")), "风险上限契约")
    check(bool(contract.get("horizon_weights")), "周期权重契约")

    snap = pd.read_csv(ROOT / "research" / "data" / "cache" / "fundamental_snapshot.csv",
                      dtype={"code": str})
    pb_cov = float(snap["pb"].notna().mean())
    check(pb_cov >= 0.60, "PB 覆盖率", f"{pb_cov:.1%} ({int(snap['pb'].notna().sum())}/{len(snap)})")

    from research_snapshot import value_bp_metric
    pb, bp = value_bp_metric("600519")
    check(pb and pb > 0 and bp and bp > 0, "value_bp_metric 实盘样本", f"PB={pb:.2f} BP={bp:.2f}")

    from ai_client import _value_factor_line
    line = _value_factor_line("600519")
    check("研究层价值因子" in line, "AI 提示词注入", line.strip())

    from ai_client import _policy_overlay_text
    # D-006：policy_industry_plan_car5 权重已改为 0.05，检查配置正确（文本内容取决于窗口内是否有事件）
    from ai_client import get_research_overlay
    pfs = get_research_overlay().get("policy_factors") or []
    active = [f for f in pfs if float(f.get("weight") or 0) > 0]
    check(len(active) >= 1, "政策因子激活", f"{active[0]['id']} weight={active[0]['weight']}")

    from market_scanner import score_stock
    ind = {"量比": 1.2, "RSI(14)": 55, "MACD金叉": False, "KDJ金叉": False,
           "均线多头": True, "MACD状态": "多头", "KDJ状态": "正常"}
    stock = {"最新价": 1300.0, "涨跌幅": 0.5, "成交额": 1.2e9, "股票名": "贵州茅台"}
    hist = pd.DataFrame({"close": [1300.0 + i * 2 for i in range(30)]})
    res = score_stock("600519", ind, 1.8, stock, hist)
    check(any("研究层" in r for r in res["reasons"]), "scanner 消费研究层因子", " ".join(res["reasons"][:4]))

    from strategy_store import get_effective_params
    params = get_effective_params()
    check(params.short_stop_loss == -0.03 and params.short_take_profit == 0.08, "短线止损止盈映射",
          f"{params.short_stop_loss}/{params.short_take_profit}")

    import trading_bot
    check(hasattr(trading_bot, "run_scheduled_reports"), "11:30/15:05 报告调度")
    check(hasattr(trading_bot, "_research_regime_bucket"), "执行层消费研究层 regime")

    import stock_report
    check(hasattr(stock_report, "_NO_PROXY"), "飞书报告直连 opener")

    print("落地校验全部通过")


if __name__ == "__main__":
    main()

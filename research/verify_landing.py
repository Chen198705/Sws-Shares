"""
三层架构落地校验：研究层契约 → 信号层因子 → 执行层调度，逐项断言。

断言规范：
- 每条会随决策变化的断言前必须加 # ASSERT[decision=D-XXX, status=...] 注释
- 详见 /Users/chenjianhui/Claude/Projects/Stocks/handover/VERIFY_LANDING_ASSERTION_STYLE.md
"""

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

    # ASSERT[decision=PENDING, status=after]
    # 契约版本由 research 层 build_strategy_params.py 生成，2026-08-17.1 = D-006 更新后版本
    check(contract.get("version", "").startswith("2026-08"), "契约版本", contract.get("version"))
    # ASSERT[decision=PENDING, status=after]
    # regime 由 research 层每日刷新，不依赖单次决策
    check(contract.get("regime", {}).get("state", ""), "regime 已写入")

    factors = contract.get("factor_constraints") or []
    # ASSERT[decision=D-006, status=after]
    # 16 因子约束为 EXP-009/010 结果，不随政策因子决策变化
    check(len(factors) == 16, "16 因子约束", f"{len(factors)} factors")
    vb = next((f for f in factors if f["id"] == "value_bp"), None)
    # ASSERT[decision=D-006, status=after]
    # value_bp 为 EXP-009/010 独立验证，不受 D-006 影响
    check(vb is not None and vb.get("weight", 0) > 0, "value_bp 权重", str(vb and vb.get("weight")))
    check("通过" in (vb or {}).get("status", "") or "有效" in (vb or {}).get("status", ""),
          "value_bp 状态", (vb or {}).get("status", ""))

    pf = contract.get("policy_factors") or []
    # ASSERT[decision=D-006, status=after]
    # 政策因子契约由 EXP-011 研究结果决定，6 项登记
    check(len(pf) >= 5, "政策因子契约", f"{len(pf)} factors")
    p1 = next((f for f in pf if f["id"] == "policy_industry_plan_car5"), None)
    # ASSERT[decision=D-006, status=after]
    # policy_industry_plan_car5 已入模，status 应含 "active"
    # 决策回退时需先 D-006-revert 决策，再改此断言
    check(p1 is not None and "active" in (p1 or {}).get("status", ""), "policy_industry_plan_car5 登记",
          str(p1 and p1.get("status")))
    # ASSERT[decision=D-006, status=after]
    # D-006 决策后 weight 应 > 0（L2 验证前保守设为 0.05）
    check(float(p1.get("weight") or 0) > 0, "政策因子已激活", f"weight={p1.get('weight')}")

    rl = contract.get("risk_limits") or {}
    # ASSERT[decision=PENDING, status=after]
    # 风险上限由 research 层 regime 决定，不依赖单次决策
    check(all(k in rl for k in ("max_position_pct", "min_cash_pct", "single_stock_pct")), "风险上限契约")
    check(bool(contract.get("horizon_weights")), "周期权重契约")

    snap = pd.read_csv(ROOT / "research" / "data" / "cache" / "fundamental_snapshot.csv",
                      dtype={"code": str})
    pb_cov = float(snap["pb"].notna().mean())
    # ASSERT[decision=PENDING, status=after]
    # PB 覆盖率为基础数据校验，不依赖决策
    check(pb_cov >= 0.60, "PB 覆盖率", f"{pb_cov:.1%} ({int(snap['pb'].notna().sum())}/{len(snap)})")

    from research_snapshot import value_bp_metric
    pb, bp = value_bp_metric("600519")
    # ASSERT[decision=PENDING, status=after]
    # value_bp_metric 为只读实盘数据查询
    check(pb and pb > 0 and bp and bp > 0, "value_bp_metric 实盘样本", f"PB={pb:.2f} BP={bp:.2f}")

    from ai_client import _value_factor_line
    line = _value_factor_line("600519")
    # ASSERT[decision=D-006, status=after]
    # AI 提示词价值因子注入不受 D-006 影响（独立于政策因子）
    check("研究层价值因子" in line, "AI 提示词注入", line.strip())

    from ai_client import get_research_overlay
    pfs = get_research_overlay().get("policy_factors") or []
    active = [f for f in pfs if float(f.get("weight") or 0) > 0]
    # ASSERT[decision=D-006, status=after]
    # D-006 激活后至少 1 个政策因子 weight > 0
    check(len(active) >= 1, "政策因子激活", f"{active[0]['id']} weight={active[0]['weight']}")

    from market_scanner import score_stock
    ind = {"量比": 1.2, "RSI(14)": 55, "MACD金叉": False, "KDJ金叉": False,
           "均线多头": True, "MACD状态": "多头", "KDJ状态": "正常"}
    stock = {"最新价": 1300.0, "涨跌幅": 0.5, "成交额": 1.2e9, "股票名": "贵州茅台"}
    hist = pd.DataFrame({"close": [1300.0 + i * 2 for i in range(30)]})
    res = score_stock("600519", ind, 1.8, stock, hist)
    # ASSERT[decision=D-006, status=after]
    # scanner 消费研究层因子由 value_bp 和 policy_factors 共同决定
    check(any("研究层" in r for r in res["reasons"]), "scanner 消费研究层因子", " ".join(res["reasons"][:4]))

    from strategy_store import get_effective_params
    params = get_effective_params()
    # ASSERT[decision=D-006, status=after]
    # 止损止盈参数由迭代引擎调整，不受 D-006 政策因子决策影响
    check(params.short_stop_loss == -0.03 and params.short_take_profit == 0.08, "短线止损止盈映射",
          f"{params.short_stop_loss}/{params.short_take_profit}")

    import trading_bot
    # ASSERT[decision=PENDING, status=after]
    # 报告调度和 regime 消费为长期功能，不依赖单次决策
    check(hasattr(trading_bot, "run_scheduled_reports"), "11:30/15:05 报告调度")
    check(hasattr(trading_bot, "_research_regime_bucket"), "执行层消费研究层 regime")

    import stock_report
    check(hasattr(stock_report, "_NO_PROXY"), "飞书报告直连 opener")

    print("落地校验全部通过")


if __name__ == "__main__":
    main()

"""研究层 → 信号层只读契约生成器。

读因子状态与 regime 状态，输出 strategy_params.json，信号层只读消费，不回写。
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FACTOR_STATE = ROOT / "export" / "factor_state.json"
REGIME_STATE = ROOT / "export" / "regime_state.json"
CROWDING = ROOT / "export" / "factor_crowding.json"
REGIME_HISTORY = ROOT / "export" / "regime_history.csv"
OUT = ROOT / "export" / "strategy_params.json"


def _horizon_weights(regime: str) -> dict:
    """按 regime 给出短/中/长线参考权重（保守，不随市场情绪剧烈摆动）。"""
    if "熊" in regime:
        return {"short": 0.5, "medium": 0.3, "long": 0.2}
    if "牛" in regime:
        return {"short": 0.6, "medium": 0.3, "long": 0.1}
    return {"short": 0.5, "medium": 0.3, "long": 0.2}


def _regime_history_summary() -> dict:
    if not REGIME_HISTORY.exists():
        return {}
    hist = pd.read_csv(REGIME_HISTORY, parse_dates=["date"])
    hist["month"] = hist["date"].dt.to_period("M").astype(str)
    last12 = hist[hist["month"] >= str(pd.Timestamp.now().to_period("M") - 11)]
    return {
        "months": sorted(last12["month"].unique().tolist()),
        "rule": last12.groupby("month")["rule"].last().to_dict(),
        "hmm": last12.groupby("month")["hmm"].last().to_dict(),
    }


def build() -> dict:
    factor_state = json.loads(FACTOR_STATE.read_text()) if FACTOR_STATE.exists() else {"factors": []}
    regime_state = json.loads(REGIME_STATE.read_text()) if REGIME_STATE.exists() else {}
    crowding = json.loads(CROWDING.read_text()) if CROWDING.exists() else {}
    metrics = regime_state.get("metrics", {})
    regime_label = metrics.get("state", "❓ 转换期")

    contract = {
        "version": "2026-08-15.1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "confidence": factor_state.get("confidence", "L0"),
        "regime": {
            "state": regime_label,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "metrics": metrics,
            "history": _regime_history_summary(),
            "hmm": {
                "state_share": (regime_state.get("hmm") or {}).get("params", {}).get("state_share"),
            },
        },
        "factor_constraints": factor_state.get("factors", []),
        "crowding": crowding.get("factors", {}),
        "risk_limits": {
            "max_position_pct": 0.70,
            "single_stock_pct": 0.25,
            "min_cash_pct": 0.30,
        },
        "horizon_weights": _horizon_weights(regime_label),
    }
    OUT.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    return contract


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))

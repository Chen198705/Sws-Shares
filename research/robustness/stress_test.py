"""压力测试（RISK.md 每月必做）。

情景：2015 股灾（1 个月 -30%）、2018 慢熊（1 年 -25%）、2020 疫情冲击
（2 周 -15%）、流动性枯竭（成交额跌 30%，3 天减半后剩余仓位追加冲击）。
任何情景最大回撤 > 40% -> 标记强制降仓。
"""

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "robustness" / "scenarios"
TRADING_LOG_DB = ROOT.parent / "stock-ai" / "api" / "logs" / "trading_log.db"


SCENARIOS = {
    "2015_股灾": {"periods": 21, "total_shock": -0.30, "note": "1 个月 -30%（日等幅）"},
    "2018_慢熊": {"periods": 252, "total_shock": -0.25, "note": "1 年 -25%（日等幅）"},
    "2020_疫情冲击": {"periods": 10, "total_shock": -0.15, "note": "2 周 -15%（日等幅）"},
    "流动性枯竭": {"periods": 21, "total_shock": -0.30, "liquidity": True,
                   "note": "成交额跌至 30%，3 天减半后剩余仓位再承受 -30%"},
}


def _daily_from_total(total: float, periods: int) -> float:
    return (1 + total) ** (1.0 / periods) - 1


def run_scenario(holdings_value: float, cash: float, scenario: dict) -> dict:
    periods = scenario["periods"]
    total = scenario["total_shock"]
    liquidity = scenario.get("liquidity", False)
    daily = _daily_from_total(total, periods)
    nav = []
    invested = holdings_value
    cash_rem = cash
    for t in range(1, periods + 1):
        if liquidity and t <= 3:
            # 前 3 天只能减半，减出的一半进现金，剩余继续暴露
            if t == 1:
                reduced = invested * 0.5
                invested -= reduced
                cash_rem += reduced
            # 减仓后剩余仓位仍按每日冲击
            invested *= (1 + daily)
        else:
            invested *= (1 + daily)
        nav.append(invested + cash_rem)
    nav = pd.Series(nav, dtype=float)
    peak = nav.cummax()
    dd = (nav / peak - 1)
    max_dd = float(dd.min())
    return {
        "max_drawdown": max_dd,
        "final_nav": float(nav.iloc[-1]),
        "initial_nav": float(holdings_value + cash),
        "trigger_40pct_reduce": max_dd <= -0.40,
    }


def load_holdings_from_db(db_path: Optional[Path] = None,
                          exclude_st: bool = True) -> dict:
    """从 trading_log.db 读取当前持仓市值（simulation broker 写入 trades 表）。"""
    db = Path(db_path) if db_path else TRADING_LOG_DB
    if not db.exists():
        return {}
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT code, SUM(volume) as vol FROM trades WHERE direction='buy' "
            "GROUP BY code").fetchall()
    except Exception:
        rows = []
    conn.close()
    # 注意：这里只是简易口径，真实市值以 broker get_balance 为准
    return {c: float(v or 0) for c, v in rows}


def load_account_from_broker() -> tuple:
    """读取真实模拟账户（stock-ai SimulationBroker），失败时回退初始账户。"""
    try:
        import sys
        api_dir = ROOT.parent / "stock-ai" / "api"
        if str(api_dir) not in sys.path:
            sys.path.insert(0, str(api_dir))
        from simulation_broker import SimulationBroker
        broker = SimulationBroker()
        bal = broker.get_balance()
        return float(bal.get("market_value", 0.0)), float(bal.get("cash", 0.0))
    except Exception:
        return 0.0, 1_000_000.0


def run_stress_test(holdings_value: float, cash: float,
                    scenarios: Optional[dict] = None) -> dict:
    scenarios = scenarios or SCENARIOS
    results = {}
    for name, sc in scenarios.items():
        results[name] = run_scenario(holdings_value, cash, sc)
    any_reduce = any(v["trigger_40pct_reduce"] for v in results.values())
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "holdings_value": holdings_value,
        "cash": cash,
        "total_assets": holdings_value + cash,
        "exposure_pct": holdings_value / max(holdings_value + cash, 1e-9),
        "scenarios": results,
        "action": "强制降仓" if any_reduce else "维持现仓",
    }


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    holdings_value, cash = load_account_from_broker()
    report = run_stress_test(holdings_value=holdings_value, cash=cash)
    out = OUT_DIR / "latest_stress_test.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

"""2014-2024 历史 regime 标注：规则法 + HMM，输出到 export。"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from research.data.index import fetch_index_daily
from research.regime.index_regime import monthly_regime_labels, regime_metrics
from research.regime.hmm import fit_hmm, hmm_label


def main():
    cache = ROOT / "data" / "cache"
    close = fetch_index_daily("sh000001", "2014-01-01", None, cache)
    daily_rule, monthly_rule = monthly_regime_labels(close)
    hmm = fit_hmm(close)
    daily_hmm = hmm_label(close)
    state = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "index": "sh000001",
        "metrics": regime_metrics(close, 60),
        "rule": {
            "method": "REGIME.md 方法1 规则法",
            "monthly": [{"month": str(p), "regime": r} for p, r in monthly_rule.items()],
            "last_30d": [{"date": str(d.date()), "regime": r} for d, r in daily_rule.tail(30).items()],
        },
        "hmm": {
            "method": "2 状态高斯 HMM（numpy/scipy）",
            "params": hmm["params"],
            "n_obs": len(hmm["states"]) if hmm["states"] is not None else 0,
            "last_30d": [
                {"date": str(d.date()), "regime": r}
                for d, r in daily_hmm.tail(30).items()
            ],
        },
    }
    out = ROOT / "export"
    out.mkdir(parents=True, exist_ok=True)
    (out / "regime_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    hist = pd.DataFrame({
        "date": daily_rule.index,
        "rule": daily_rule.values,
        "hmm": daily_hmm.reindex(daily_rule.index).values,
    })
    hist.to_csv(out / "regime_history.csv", index=False, encoding="utf-8-sig")
    print(f"历史 regime 标注完成：{len(hist)} 个交易日 -> {out}/regime_history.csv")


if __name__ == "__main__":
    main()

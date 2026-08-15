"""研究层每日收盘后刷新：regime + 拥挤度 + 契约 + 归因。"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from research.data.loader import load_panel
from research.export.build_strategy_params import build
from research.regime.annotate import main as annotate_main
from research.attribution.aggregate import aggregate
from research.monitor.crowding import run as crowding_run


def _cached_codes(adjust: str = "qfq") -> list:
    cache = ROOT / "data" / "cache"
    return sorted(f.name.split("_")[0] for f in cache.glob(f"*_{adjust}.csv"))


def main():
    t0 = time.time()
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    steps = []

    annotate_main()
    steps.append("regime")

    codes = _cached_codes("qfq")
    if codes:
        panel = load_panel(codes, "2014-01-01", "2024-12-31", ROOT / "data" / "cache", adjust="qfq")
        crowding_run(panel)
        steps.append(f"crowding({len(panel)}stocks)")
    else:
        (ROOT / "export" / "factor_crowding.json").write_text(
            '{"generated_at": null, "stocks": 0, "factors": {}}', encoding="utf-8")
        steps.append("crowding(skip)")

    contract = build()
    steps.append("contract")

    db = ROOT.parent / "stock-ai" / "api" / "logs" / "trading_log.db"
    attr = aggregate(db, ROOT / "attribution" / "reports")
    steps.append("attribution")

    line = (f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] daily refresh ok: {', '.join(steps)} "
            f"| regime={contract['regime']['state']} | version={contract['version']} "
            f"| elapsed={time.time()-t0:.1f}s")
    print(line)
    (log_dir / "daily_refresh.log").open("a", encoding="utf-8").write(line + "\n")
    return attr


if __name__ == "__main__":
    main()

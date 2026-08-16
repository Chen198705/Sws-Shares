"""行业映射与行业集中度风控（RISK.md：单一行业持仓 <= 总仓位 30%）。

行业快照由研究层 research/data/fundamental.py 生成（东财业绩报表“所处行业”），
执行层只读使用；映射文件缺失时降级跳过行业检查，不影响其他风控。
"""

import json
import time
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"
INDUSTRY_MAP_PATH = DATA_DIR / "industry_map.json"
_cache = {"ts": 0.0, "map": None}


def load_industry_map(ttl_seconds: int = 3600) -> dict:
    now = time.time()
    if _cache["map"] is not None and now - _cache["ts"] < ttl_seconds:
        return _cache["map"]
    m = {}
    if INDUSTRY_MAP_PATH.exists():
        try:
            m = json.loads(INDUSTRY_MAP_PATH.read_text())
        except Exception:
            m = {}
    _cache.update({"ts": now, "map": m})
    return m


def sector_concentration_ok(code: str, proposed_value: float,
                            positions: list, total_assets: float,
                            limit: float = 0.30) -> tuple:
    """返回 (ok, industry, same_sector_value, limit_value)。

    positions 使用 broker 持仓对象（stock_code / current_price / volume）。
    """
    if total_assets <= 0:
        return True, "", 0.0, 0.0
    industry_map = load_industry_map()
    if not industry_map:
        return True, "", 0.0, 0.0
    ind = industry_map.get(str(code).zfill(6), "")
    if not ind:
        return True, "", 0.0, 0.0
    same = 0.0
    for pos in positions:
        pc = getattr(pos, "stock_code", "")
        if industry_map.get(str(pc).zfill(6), "") == ind:
            same += float(getattr(pos, "current_price", 0) or 0) * float(getattr(pos, "volume", 0) or 0)
    proposed = float(proposed_value or 0)
    limit_value = total_assets * limit
    return (same + proposed) <= limit_value, ind, same, limit_value

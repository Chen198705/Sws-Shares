#!/usr/bin/env python3
"""A 股交易日历。

背景：原先 is_trading_day() 只判断"是不是周一至周五"，遇到工作日节假日
（如 2026-09-25 中秋、2026-10-01~10-07 国庆）会误判为交易日，导致机器人
在休市日拿昨收价伪造成交。这里用 JQData 权威交易日历落盘 + 内存缓存，
离线也能判断。

数据文件：reports/trading_calendar.json
结构：{"generated_at": "...", "days": {"2026": ["2026-01-05", ...]}}

刷新方式（需 JQData 凭证）：
    python market_calendar.py --refresh
未命中缓存的年份自动退回"周一至周五"的宽松判断，不会阻塞交易。
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

CALENDAR_PATH = Path(__file__).parent / "reports" / "trading_calendar.json"

_year_sets = {}
_loaded = False


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if not CALENDAR_PATH.exists():
            return
        payload = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
        for year, days in (payload.get("days") or {}).items():
            _year_sets[str(year)] = {str(d) for d in days}
    except Exception as e:
        print("[market_calendar] 交易日历加载失败，退回工作日判断: %s" % e)


def reload():
    """清缓存后重新读取，用于手工刷新日历后热加载。"""
    global _loaded
    _loaded = False
    _year_sets.clear()
    _load()


def _as_date(d):
    if d is None:
        return datetime.now().date()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def has_calendar(d=None):
    """该日期所属年份是否存在权威日历数据。"""
    _load()
    return _as_date(d).strftime("%Y") in _year_sets


def is_trading_day(d=None):
    """是否为 A 股交易日。有权威日历按日历判断，没有则按工作日宽松判断。"""
    day = _as_date(d)
    if day.weekday() >= 5:
        return False
    _load()
    year_days = _year_sets.get(day.strftime("%Y"))
    if year_days is None:
        return True
    return day.isoformat() in year_days


def next_trading_day(d=None, include_self=False):
    """下一个交易日（默认不含当天）。"""
    day = _as_date(d)
    if include_self and is_trading_day(day):
        return day
    for _ in range(370):
        day = day + timedelta(days=1)
        if is_trading_day(day):
            return day
    raise RuntimeError("未来 370 天内找不到交易日，请刷新交易日历")


def calendar_summary():
    """给巡检用的日历摘要。"""
    _load()
    today = datetime.now().date()
    return {
        "path": str(CALENDAR_PATH),
        "loaded_years": sorted(_year_sets.keys()),
        "today": today.isoformat(),
        "today_is_trading_day": is_trading_day(today),
        "next_trading_day": next_trading_day(today).isoformat(),
    }


def _refresh(years):
    """用 JQData 拉取指定年份的交易日并落盘。需要 JQ_USERNAME / JQ_PASSWORD。"""
    import os
    from jqdatasdk import auth, get_trade_days

    env = {}
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    auth(env.get("JQ_USERNAME") or os.getenv("JQ_USERNAME", ""),
         env.get("JQ_PASSWORD") or os.getenv("JQ_PASSWORD", ""))

    payload = {"generated_at": datetime.now().isoformat(),
               "source": "jqdatasdk.get_trade_days", "days": {}}
    for year in years:
        days = get_trade_days("%d-01-01" % year, "%d-12-31" % year)
        payload["days"][str(year)] = [d.strftime("%Y-%m-%d") for d in days]
        print("  %d: %d 个交易日" % (year, len(payload["days"][str(year)])))

    CALENDAR_PATH.parent.mkdir(exist_ok=True)
    CALENDAR_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入 %s" % CALENDAR_PATH)


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        this_year = datetime.now().year
        _refresh(range(this_year - 1, this_year + 3))
        reload()
    print(json.dumps(calendar_summary(), ensure_ascii=False, indent=2))

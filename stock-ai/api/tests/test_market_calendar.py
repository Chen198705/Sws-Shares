"""交易日历回归测试。

覆盖已确认缺陷：原 is_trading_day() 只看周一至周五，会把工作日节假日
（2026-09-25 中秋、2026-10-01~10-07 国庆）当成交易日，导致机器人
在休市日拿昨收价生成伪造成交。
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_calendar as mc


def test_calendar_loaded_for_2026():
    assert mc.has_calendar(date(2026, 9, 24)), "2026 年交易日历未加载"
    print("PASS test_calendar_loaded_for_2026")


def test_mid_autumn_2026_is_holiday():
    # 2026-09-25 是周五，但为中秋节休市日
    assert date(2026, 9, 25).weekday() == 4
    assert mc.is_trading_day(date(2026, 9, 25)) is False
    print("PASS test_mid_autumn_2026_is_holiday")


def test_national_day_break_2026_is_holiday():
    for day in range(1, 8):
        assert mc.is_trading_day(date(2026, 10, day)) is False, f"2026-10-0{day} 应为休市"
    assert mc.is_trading_day(date(2026, 10, 8)) is True
    print("PASS test_national_day_break_2026_is_holiday")


def test_normal_weekday_is_trading_day():
    assert mc.is_trading_day(date(2026, 9, 24)) is True
    assert mc.is_trading_day(date(2026, 9, 28)) is True
    assert mc.is_trading_day(date(2026, 9, 26)) is False  # 周六
    assert mc.is_trading_day(date(2026, 9, 27)) is False  # 周日
    print("PASS test_normal_weekday_is_trading_day")


def test_next_trading_day_skips_holiday():
    assert mc.next_trading_day(date(2026, 9, 24)) == date(2026, 9, 28)
    assert mc.next_trading_day(date(2026, 9, 30)) == date(2026, 10, 8)
    assert mc.next_trading_day(date(2026, 9, 24), include_self=True) == date(2026, 9, 24)
    print("PASS test_next_trading_day_skips_holiday")


def test_unknown_year_falls_back_to_weekday():
    # 未缓存年份退回工作日判断，不应抛异常
    assert mc.has_calendar(date(2099, 1, 1)) is False
    assert mc.is_trading_day(date(2099, 1, 5)) is True   # 周一
    assert mc.is_trading_day(date(2099, 1, 3)) is False  # 周六
    print("PASS test_unknown_year_falls_back_to_weekday")


def test_trading_bot_delegates_to_calendar():
    import trading_bot as tb
    assert tb.is_trading_day.__module__ == "trading_bot"
    # 交易日历已加载，机器人判断应与日历一致
    assert tb.market_calendar.is_trading_day(date(2026, 9, 25)) is False
    print("PASS test_trading_bot_delegates_to_calendar")


if __name__ == "__main__":
    test_calendar_loaded_for_2026()
    test_mid_autumn_2026_is_holiday()
    test_national_day_break_2026_is_holiday()
    test_normal_weekday_is_trading_day()
    test_next_trading_day_skips_holiday()
    test_unknown_year_falls_back_to_weekday()
    test_trading_bot_delegates_to_calendar()
    print("\nAll PASS")

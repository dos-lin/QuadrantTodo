"""recurrence 纯逻辑单元测试（无 Qt 依赖，可纯 python 运行）。"""

from __future__ import annotations

from datetime import date, datetime

from quadrant_todo.recurrence import (
    compute_relative_reminder,
    defer_due_date,
    format_period_label,
    natural_to_rrule,
    next_boundary,
    period_range,
    resolve_reminder,
    rrule_label,
)


def test_period_range_week_monday_to_sunday():
    # 周三 2026-09-02 → 周一 2026-08-31 ~ 周日 2026-09-06（end 为开区间 = 09-07）
    start, end = period_range("week", date(2026, 9, 2))
    assert start == date(2026, 8, 31)
    assert end == date(2026, 9, 7)
    # 含周一（start）与周日（end 前一天）
    assert start.weekday() == 0
    assert (end - __import__("datetime").timedelta(days=1)).weekday() == 6


def test_period_range_month():
    start, end = period_range("month", date(2026, 2, 15))
    assert start == date(2026, 2, 1)
    assert end == date(2026, 3, 1)


def test_period_range_year():
    start, end = period_range("year", date(2026, 6, 1))
    assert start == date(2026, 1, 1)
    assert end == date(2027, 1, 1)


def test_format_period_label():
    assert format_period_label("week", date(2026, 9, 2)) == "2026 年第 36 周"
    assert format_period_label("month", date(2026, 9, 2)) == "2026 年 9 月"
    assert format_period_label("year", date(2026, 9, 2)) == "2026 年"


def test_next_boundary_daily():
    assert next_boundary(date(2026, 9, 1), "daily") == date(2026, 9, 2)


def test_next_boundary_weekly_next_monday():
    # 周三 → 下周一
    assert next_boundary(date(2026, 9, 2), "weekly") == date(2026, 9, 7)
    # 周一 → 下周一（+7）
    assert next_boundary(date(2026, 8, 31), "weekly") == date(2026, 9, 7)
    # 周日 → 次日周一
    assert next_boundary(date(2026, 9, 6), "weekly") == date(2026, 9, 7)


def test_next_boundary_monthly_next_month_first():
    assert next_boundary(date(2026, 9, 15), "monthly") == date(2026, 10, 1)
    # 12 月跨年
    assert next_boundary(date(2026, 12, 5), "monthly") == date(2027, 1, 1)


def test_next_boundary_custom_weekday():
    # 每工作日：周三 9/2 之后下一个工作日是 9/3(周四)
    rule = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert next_boundary(date(2026, 9, 2), "custom", rule) == date(2026, 9, 3)
    # 周五之后下一个工作日是下周一 9/7
    assert next_boundary(date(2026, 9, 4), "custom", rule) == date(2026, 9, 7)


def test_next_boundary_custom_interval_days():
    assert next_boundary(date(2026, 9, 1), "custom", "FREQ=DAILY;INTERVAL=3") == date(2026, 9, 4)


def test_defer_due_date_monthly_end_of_month():
    # 1/31 +1 月 → 2 月没有 31，顺延为 2 月最后一日
    assert defer_due_date(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    # 闰年 1/31 +1 月 → 2/29
    assert defer_due_date(date(2024, 1, 31), "monthly") == date(2024, 2, 29)
    # 普通月
    assert defer_due_date(date(2026, 3, 15), "monthly") == date(2026, 4, 15)


def test_defer_due_date_daily_weekly():
    assert defer_due_date(date(2026, 9, 1), "daily") == date(2026, 9, 2)
    assert defer_due_date(date(2026, 9, 1), "weekly") == date(2026, 9, 8)


def test_defer_due_date_no_due():
    assert defer_due_date(None, "daily") is None


def test_natural_to_rrule():
    assert natural_to_rrule("每工作日") == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert natural_to_rrule("每2周") == "FREQ=WEEKLY;INTERVAL=2"
    assert natural_to_rrule("每 3 天") == "FREQ=DAILY;INTERVAL=3"
    assert natural_to_rrule("每月15日") == "FREQ=MONTHLY;BYMONTHDAY=15"
    assert natural_to_rrule("每月 15 号") == "FREQ=MONTHLY;BYMONTHDAY=15"
    assert natural_to_rrule("每周一") == "FREQ=WEEKLY;BYDAY=MO"
    assert natural_to_rrule("随便写写") is None


def test_rrule_label():
    assert rrule_label("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") == "每工作日"
    assert rrule_label("FREQ=DAILY;INTERVAL=3") == "每 3 天"
    assert rrule_label("FREQ=MONTHLY;BYMONTHDAY=15") == "每月 15 日"
    assert rrule_label("FREQ=WEEKLY;INTERVAL=2") == "每 2 周"


def test_compute_relative_reminder():
    due = date(2026, 9, 10)
    # 提前 60 分钟 → 9/9 23:00
    assert compute_relative_reminder(due, 60) == datetime(2026, 9, 9, 23, 0)
    assert compute_relative_reminder(None, 60) is None
    assert compute_relative_reminder(due, None) is None


def test_resolve_reminder():
    due = date(2026, 9, 10)
    manual = datetime(2026, 9, 9, 8, 0)
    assert resolve_reminder("none", due, manual, 60) is None
    assert resolve_reminder("manual", due, manual, 60) == manual
    assert resolve_reminder("relative", due, manual, 60) == datetime(2026, 9, 9, 23, 0)
    # relative 无 dueDate → None
    assert resolve_reminder("relative", None, manual, 60) is None

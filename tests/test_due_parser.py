"""标题日期解析测试（PRD F2.10）。

固定基准日 TODAY = 2026-08-30，让「当月/当年是否已过」可确定性断言。
"""

from datetime import date

import pytest

from quadrant_todo.due_parser import parse_due_from_title

TODAY = date(2026, 8, 30)


# ---------------------------------------------------------------- 完整年月日


@pytest.mark.parametrize(
    "raw",
    [
        "交报告 2026-09-15",
        "交报告 2026/9/15",
        "交报告 2026.9.15",
        "交报告 2026年9月15日",
        "交报告 2026年9月15号",
        "2026年9月15日 交报告",
    ],
)
def test_full_date_parsed(raw: str) -> None:
    result = parse_due_from_title(raw, TODAY)
    assert result.due_date == date(2026, 9, 15), raw


def test_full_date_not_rolled_even_if_past() -> None:
    """完整年月日带显式年份，即使已过期也保留原意（不自动顺延）。"""
    result = parse_due_from_title("补交材料 2026-01-05", TODAY)
    assert result.due_date == date(2026, 1, 5)


def test_invalid_full_date_ignored() -> None:
    """无法构成合法日期时不解析，按普通文本处理。"""
    result = parse_due_from_title("开会 2026年2月30日", TODAY)
    assert result.due_date is None
    assert result.title == "开会 2026年2月30日"


# ---------------------------------------------------------------- 月 + 日


def test_month_day_defaults_to_current_year() -> None:
    """未来日期：默认当年。"""
    result = parse_due_from_title("交报告 9月15日", TODAY)
    assert result.due_date == date(2026, 9, 15)
    assert result.title == "交报告"


def test_month_day_past_rolls_to_next_year() -> None:
    """当年的这一天已过 → 顺延到下一年，避免刚创建就已逾期。"""
    result = parse_due_from_title("交报告 3月5日", TODAY)
    assert result.due_date == date(2027, 3, 5)


def test_month_day_today_not_rolled() -> None:
    """正好是今天，不算「已过」，不顺延。"""
    result = parse_due_from_title("开会 8月30日", TODAY)
    assert result.due_date == date(2026, 8, 30)


@pytest.mark.parametrize("raw", ["交报告 9.15", "交报告 9/15"])
def test_month_day_numeric_separator(raw: str) -> None:
    result = parse_due_from_title(raw, TODAY)
    assert result.due_date == date(2026, 9, 15), raw


# ---------------------------------------------------------------- 只有日


def test_day_only_defaults_to_current_month() -> None:
    """当月该天尚未过去 → 用当月。"""
    result = parse_due_from_title("交房租 31号", TODAY)
    assert result.due_date == date(2026, 8, 31)
    assert result.title == "交房租"


def test_day_only_past_rolls_to_next_month() -> None:
    """当月该天已过 → 顺延到下个月。"""
    result = parse_due_from_title("交报告 3号", TODAY)
    assert result.due_date == date(2026, 9, 3)


def test_day_only_today_not_rolled() -> None:
    result = parse_due_from_title("开会 30号", TODAY)
    assert result.due_date == date(2026, 8, 30)


def test_day_only_rolls_across_year_boundary() -> None:
    """12 月输入已过的日子 → 进位到次年 1 月。"""
    result = parse_due_from_title("年度总结 1号", date(2026, 12, 30))
    assert result.due_date == date(2027, 1, 1)


def test_day_only_clamps_to_month_end() -> None:
    """当月没有这一天（2 月没有 31 号）→ 取当月最后一天。"""
    result = parse_due_from_title("月度结算 31号", date(2026, 2, 10))
    assert result.due_date == date(2026, 2, 28)


def test_day_only_out_of_range_ignored() -> None:
    result = parse_due_from_title("开会 45号", TODAY)
    assert result.due_date is None


# ---------------------------------------------------------------- 相对词


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("交报告 今天", date(2026, 8, 30)),
        ("交报告 明天", date(2026, 8, 31)),
        ("交报告 后天", date(2026, 9, 1)),
        ("交报告 大后天", date(2026, 9, 2)),
    ],
)
def test_relative_words(raw: str, expected: date) -> None:
    assert parse_due_from_title(raw, TODAY).due_date == expected


# ---------------------------------------------------------------- 标题清理


def test_date_fragment_stripped_from_title() -> None:
    result = parse_due_from_title("  9月15日 交季度报告  ", TODAY)
    assert result.title == "交季度报告"
    assert result.matched == "9月15日"


def test_empty_after_strip_falls_back_to_raw() -> None:
    """标题只剩日期时不能变空，回退保留原文。"""
    result = parse_due_from_title("9月15日", TODAY)
    assert result.title == "9月15日"
    assert result.due_date == date(2026, 9, 15)


def test_pure_text_untouched() -> None:
    result = parse_due_from_title("整理桌面", TODAY)
    assert result.title == "整理桌面"
    assert result.due_date is None
    assert result.matched == ""


# ---------------------------------------------------------------- 防误伤


@pytest.mark.parametrize(
    "raw",
    [
        "发布 v1.2.3",
        "3-5天完成",
        "第3季度复盘",
        "买5号电池",
        "坐3号线",
        "联系1号楼物业",
    ],
)
def test_not_false_positive(raw: str) -> None:
    """版本号、区间、序号等不应被当成日期。"""
    assert parse_due_from_title(raw, TODAY).due_date is None, raw


def test_empty_input() -> None:
    result = parse_due_from_title("", TODAY)
    assert result.title == ""
    assert result.due_date is None


# ---------------------------------------------------------------- 象限联动


def test_parsed_date_drives_quadrant() -> None:
    """识别出的截止日期应直接决定象限（PRD 3.3 派生逻辑）。"""
    from quadrant_todo.models import Task
    from quadrant_todo.quadrant import Quadrant

    # 重要任务 + 远期日期 → Q2（重要不紧急）
    far = parse_due_from_title("写方案 12月20日", TODAY)
    task = Task(title=far.title, importance=True, due_date=far.due_date)
    assert task.quadrant(TODAY, 2) is Quadrant.Q2

    # 重要任务 + 近期日期（明天）→ Q1（重要且紧急）
    near = parse_due_from_title("写方案 明天", TODAY)
    task = Task(title=near.title, importance=True, due_date=near.due_date)
    assert task.quadrant(TODAY, 2) is Quadrant.Q1

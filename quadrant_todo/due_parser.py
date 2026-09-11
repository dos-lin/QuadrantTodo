"""从任务标题中智能识别截止日期（PRD F2.10）。

本模块是**无状态纯函数**，不依赖 Qt 与数据库，与 quadrant.py 同级，
因此可以被单元测试完整覆盖。

识别规则（按优先级从高到低，命中「位置最靠前」的片段）：

    1. 完整年月日  2026-09-15 / 2026/9/15 / 2026年9月15日  → 原样使用，不做顺延
    2. 月 + 日     9月15日 / 9.15 / 9/15                  → 默认当年；早于今天则顺延到下一年
    3. 只有日      15日 / 15号                            → 默认当月；当月该天已过则顺延到下个月
    4. 相对词      今天 / 明天 / 后天 / 大后天             → 按今天推算

设计取舍：
    * 顺延规则是为了避免「刚创建就已逾期」。用户写「3月5日」的意图几乎不可能是
      过去的 3 月 5 日，而是即将到来的那个 3 月 5 日。
    * 完整年月日不做顺延，因为年份是用户显式给出的，属于明确意图。
    * 命中的日期片段**保留在标题中**（2026-09-11 调整）：用户输入的标题即所见，
      日期仅被识别为截止日期，不从标题里删掉。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Tuple

__all__ = ["ParsedTitle", "parse_due_from_title"]


@dataclass(frozen=True)
class ParsedTitle:
    """标题解析结果。"""

    #: 标题（保留用户输入的原文，含日期片段；2026-09-11 起不再剥离）
    title: str
    #: 识别到的截止日期；未识别到为 None
    due_date: Optional[date]
    #: 命中的原文片段，用于界面提示；未识别到为 ""
    matched: str


# ---------------------------------------------------------------- 正则模式

#: 完整年月日：2026-09-15 / 2026/9/15 / 2026.9.15 / 2026年9月15日
_FULL_DATE = re.compile(
    r"(?P<y>(?:19|20)\d{2})\s*[-/.年]\s*(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})\s*[日号]?"
)

#: 月日（中文）：9月15日 / 9月15号 / 9月15
_MONTH_DAY_CN = re.compile(r"(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*[日号]?")

#: 月日（数字）：9.15 / 9/15。
#: 前向断言排除 1.2.3 这类版本号，后向断言排除 v1.2 中已被吃掉的 1.2
_MONTH_DAY_NUM = re.compile(
    r"(?<![\d./\-vV年月季周])(?P<m>\d{1,2})\s*[/.]\s*(?P<d>\d{1,2})(?!\s*[/.]\s*\d)"
)

#: 只有日：15日 / 15号。
#: 后向断言排除「2026年9月15日」里已被完整模式吃掉的 15日；
#: 前向断言排除「5号电池」「3号线」这类复合词
_DAY_ONLY = re.compile(
    r"(?<![\d./\-年月])(?P<d>\d{1,2})\s*(?:[日]|号(?!\s*[线楼电池床]))"
)

#: 相对日期词
_RELATIVE = re.compile(r"(?P<rel>今天|今日|明天|明日|后天|大后天)")

_RELATIVE_DAYS = {
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
    "大后天": 3,
}

#: 顺序即优先级：位置相同时取更靠前的模式
_PATTERNS = (_FULL_DATE, _MONTH_DAY_CN, _MONTH_DAY_NUM, _DAY_ONLY, _RELATIVE)


# ---------------------------------------------------------------- 内部工具


def _exact_date(year: int, month: int, day: int) -> Optional[date]:
    """严格构造日期，非法（如 2 月 30 日）返回 None。"""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _clamp_date(year: int, month: int, day: int) -> Optional[date]:
    """构造日期，day 超出当月天数时取当月最后一天。"""
    if not (1 <= month <= 12) or day < 1:
        return None
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _next_month(year: int, month: int) -> Tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _best_match(text: str):
    """返回 (match, pattern_index)；无命中返回 None。取位置最靠前、优先级最高的片段。"""
    best = None
    best_key: Optional[Tuple[int, int]] = None
    for priority, pattern in enumerate(_PATTERNS):
        match = pattern.search(text)
        if match is None:
            continue
        key = (match.start(), priority)
        if best_key is None or key < best_key:
            best, best_key = match, key
    return best


def _resolve(match, today: date) -> Optional[date]:
    """把命中的片段换算成具体日期；无法构成合法日期返回 None。"""
    groups = match.groupdict()

    # 1. 完整年月日——年份是显式意图，不做顺延
    if groups.get("y"):
        return _exact_date(int(groups["y"]), int(groups["m"]), int(groups["d"]))

    # 2. 相对词
    if groups.get("rel"):
        return today + timedelta(days=_RELATIVE_DAYS[groups["rel"]])

    # 3. 月 + 日——默认当年，早于今天则顺延到下一年
    if groups.get("m") is not None:
        month, day = int(groups["m"]), int(groups["d"])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        candidate = _exact_date(today.year, month, day)
        if candidate is None:  # 如 2 月 30 日
            return None
        if candidate < today:
            candidate = _exact_date(today.year + 1, month, day)
        return candidate

    # 4. 只有日——默认当月，已过则顺延到下个月
    day = int(groups["d"])
    if not 1 <= day <= 31:
        return None
    candidate = _clamp_date(today.year, today.month, day)
    if candidate is None or candidate < today:
        year, month = _next_month(today.year, today.month)
        candidate = _clamp_date(year, month, day)
    return candidate


# ---------------------------------------------------------------- 对外入口


def parse_due_from_title(text: str, today: Optional[date] = None) -> ParsedTitle:
    """解析标题中的日期。

    Args:
        text: 用户输入的原始标题。
        today: 计算基准日，缺省取系统当天（便于测试与跨日重算）。

    Returns:
        ParsedTitle：原标题（保留日期片段）、识别到的截止日期、命中的原文片段。
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedTitle("", None, "")

    today = today or date.today()
    match = _best_match(raw)
    if match is None:
        return ParsedTitle(raw, None, "")

    due = _resolve(match, today)
    if due is None:  # 数字像日期但无法构成合法日期，按普通文本处理
        return ParsedTitle(raw, None, "")

    # 标题保留原文（含日期片段），仅把识别结果作为截止日期返回
    return ParsedTitle(raw, due, match.group(0))

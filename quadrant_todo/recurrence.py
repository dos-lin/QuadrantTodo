"""周期 / 提醒 / 时段 纯逻辑（PRD F15 / F16 / F17 / F26）。

无状态纯函数，不依赖 Qt 与数据库，可 100% 单测覆盖。
所有周期相关计算统一归一为 RRULE 子集（PRD F16.8）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

# ---------------------------------------------------------------- 时段范围（PRD F15.2）

PeriodKind = str  # "week" | "month" | "year"


def period_range(kind: PeriodKind, anchor: date) -> Tuple[date, date]:
    """返回包含 anchor 的时段 [start, end)（end 为开区间，用于 < 比较）。

    - week：周一 ~ 周日
    - month：当月 1 日 ~ 月末
    - year：当年 1 月 1 日 ~ 12 月 31 日
    """
    if kind == "week":
        start = anchor - timedelta(days=anchor.weekday())  # 周一
        end = start + timedelta(days=7)
    elif kind == "month":
        start = anchor.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    elif kind == "year":
        start = anchor.replace(month=1, day=1)
        end = start.replace(year=start.year + 1)
    else:
        raise ValueError(f"未知时段类型: {kind}")
    return start, end


def format_period_label(kind: PeriodKind, anchor: date) -> str:
    """周期视图顶部的范围标签（PRD F15.4）。"""
    if kind == "week":
        iso = anchor.isocalendar()
        return f"{anchor.year} 年第 {iso[1]} 周"
    if kind == "month":
        return f"{anchor.year} 年 {anchor.month} 月"
    if kind == "year":
        return f"{anchor.year} 年"
    return ""


# ---------------------------------------------------------------- 周期边界（PRD F16.8）

_WEEKDAY_MAP = {
    "MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6,
}


def _parse_rrule(rule: str) -> Tuple[str, int, Optional[str], Optional[int]]:
    """解析 RRULE 子集 → (FREQ, INTERVAL, BYDAY, BYMONTHDAY)。"""
    parts: dict[str, str] = {}
    for kv in rule.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.strip().upper()] = v.strip()
    freq = parts.get("FREQ", "DAILY").upper()
    interval = int(parts.get("INTERVAL", "1") or "1")
    byday = parts.get("BYDAY")
    bymonthday = int(parts["BYMONTHDAY"]) if parts.get("BYMONTHDAY") else None
    return freq, interval, byday, bymonthday


def next_boundary(last_reset: date, cycle: str, custom_rule: Optional[str] = None) -> date:
    """返回 last_reset 之后的下一个周期边界（PRD F16.3 判重生点）。

    - daily：last_reset + 1 天
    - weekly：下一个周一
    - monthly：下个月 1 日
    - custom：基于 RRULE 推算（见 _next_custom_boundary）
    """
    if cycle == "daily":
        return last_reset + timedelta(days=1)
    if cycle == "weekly":
        return _next_monday_after(last_reset)
    if cycle == "monthly":
        return _next_month_start(last_reset)
    if cycle == "custom" and custom_rule:
        return _next_custom_boundary(last_reset, custom_rule)
    # 兜底：当 cycle 非法或 custom 无规则时，按天处理
    return last_reset + timedelta(days=1)


def _next_monday_after(d: date) -> date:
    days_ahead = (7 - d.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return d + timedelta(days=days_ahead)


def _next_month_start(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _next_custom_boundary(last_reset: date, custom_rule: str) -> date:
    freq, interval, byday, bymonthday = _parse_rrule(custom_rule)
    if freq == "DAILY":
        return last_reset + timedelta(days=interval)
    if freq == "WEEKLY":
        if byday:
            weekdays = {
                _WEEKDAY_MAP[w.strip().upper()]
                for w in byday.split(",")
                if w.strip().upper() in _WEEKDAY_MAP
            }
            # 从 last_reset 之后（含锚点 + 7*(interval-1)）找下一个匹配星期
            anchor = last_reset + timedelta(days=7 * (interval - 1))
            candidate = anchor + timedelta(days=1)
            while candidate.weekday() not in weekdays:
                candidate += timedelta(days=1)
            return candidate
        # 无 BYDAY：按相同星期每 interval 周
        return last_reset + timedelta(days=7 * interval)
    if freq == "MONTHLY":
        target_day = bymonthday or 1
        months_ahead = interval
        return _month_day_after(last_reset, target_day, months_ahead)
    # 未知 FREQ 兜底
    return last_reset + timedelta(days=1)


def _month_day_after(d: date, day: int, months_ahead: int = 1) -> date:
    """从 d 所在月之后第 months_ahead 个月的 day 日（月末顺延取最后一日）。"""
    total = d.month - 1 + months_ahead
    year = d.year + total // 12
    month = total % 12 + 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    last_day = (date(next_year, next_month, 1) - timedelta(days=1)).day
    return date(year, month, min(day, last_day))


def defer_due_date(due_date: Optional[date], cycle: str, custom_rule: Optional[str] = None) -> Optional[date]:
    """周期重生时顺延 dueDate 一个周期（PRD F16.3）。

    daily +1 天 / weekly +7 天 / monthly +1 月（月末顺延）/ custom 按 RRULE。
    无 dueDate 时返回 None（周期重置不臆造截止日期）。
    """
    if due_date is None:
        return None
    if cycle == "daily":
        return due_date + timedelta(days=1)
    if cycle == "weekly":
        return due_date + timedelta(days=7)
    if cycle == "monthly":
        return _add_month(due_date)
    if cycle == "custom" and custom_rule:
        freq, interval, _byday, bymonthday = _parse_rrule(custom_rule)
        if freq == "DAILY":
            return due_date + timedelta(days=interval)
        if freq == "WEEKLY":
            return due_date + timedelta(days=7 * interval)
        if freq == "MONTHLY":
            return _month_day_after(due_date, bymonthday or due_date.day, interval)
    return due_date + timedelta(days=1)


def _add_month(d: date) -> date:
    """加一个月，月末顺延取目标月最后一日（PRD F16.8）。"""
    return _month_day_after(d, d.day, 1)


# ---------------------------------------------------------------- 自定义周期：自然语言 ↔ RRULE（PRD F16.6）

_NATURAL_PATTERNS = [
    (re.compile(r"每\s*(\d+)\s*天"), lambda m: f"FREQ=DAILY;INTERVAL={m.group(1)}"),
    (re.compile(r"每\s*(\d+)\s*周"), lambda m: f"FREQ=WEEKLY;INTERVAL={m.group(1)}"),
    (re.compile(r"每\s*工作日"), lambda m: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
    (re.compile(r"每\s*(\d+)\s*个月"), lambda m: f"FREQ=MONTHLY;INTERVAL={m.group(1)}"),
    (re.compile(r"每月\s*(\d+)\s*[日号]"), lambda m: f"FREQ=MONTHLY;BYMONTHDAY={m.group(1)}"),
    (re.compile(r"每周\s*([一二三四五六日天])"), lambda m: f"FREQ=WEEKLY;BYDAY={_CN_WEEKDAY[m.group(1)]}"),
]

_CN_WEEKDAY = {"一": "MO", "二": "TU", "三": "WE", "四": "TH", "五": "FR", "六": "SA", "日": "SU", "天": "SU"}


def natural_to_rrule(text: str) -> Optional[str]:
    """将自然语言周期表达转为 RRULE 子集；无法识别返回 None（PRD F16.6）。"""
    text = (text or "").strip()
    if not text:
        return None
    for pattern, builder in _NATURAL_PATTERNS:
        match = pattern.search(text)
        if match:
            return builder(match)
    return None


def rrule_label(rule: str) -> str:
    """将 RRULE 转回可读中文标签（用于界面展示，PRD F16.6）。"""
    freq, interval, byday, bymonthday = _parse_rrule(rule)
    if freq == "DAILY":
        return f"每 {interval} 天" if interval > 1 else "每天"
    if freq == "WEEKLY" and byday == "MO,TU,WE,TH,FR":
        return "每工作日"
    if freq == "WEEKLY":
        if byday:
            days = "/".join(_RRULE_WEEKDAY_LABEL.get(d, d) for d in byday.split(","))
            return f"每 {interval} 周（{days}）" if interval > 1 else f"每{_strip_weekday_label(days)}"
        return f"每 {interval} 周" if interval > 1 else "每周"
    if freq == "MONTHLY":
        if bymonthday:
            return f"每月 {bymonthday} 日"
        return f"每 {interval} 个月" if interval > 1 else "每月"
    return rule


_RRULE_WEEKDAY_LABEL = {"MO": "一", "TU": "二", "WE": "三", "TH": "四", "FR": "五", "SA": "六", "SU": "日"}


def _strip_weekday_label(days: str) -> str:
    return "周" + days


# ---------------------------------------------------------------- 提醒联动（PRD F26）

def compute_relative_reminder(due_date: Optional[date], offset_minutes: Optional[int]) -> Optional[datetime]:
    """相对截止日期推导提醒时间：dueDate - offset 分钟（PRD F26.2 / F26.6）。

    无 dueDate 或无 offset 时返回 None。
    """
    if due_date is None or offset_minutes is None:
        return None
    due_dt = datetime(due_date.year, due_date.month, due_date.day, 0, 0)
    return due_dt - timedelta(minutes=offset_minutes)


def resolve_reminder(
    rule: str,
    due_date: Optional[date],
    manual_reminder: Optional[datetime],
    offset_minutes: Optional[int],
) -> Optional[datetime]:
    """根据提醒规则解析最终 reminder 值（PRD F26 全量逻辑）。

    - none：无提醒
    - manual：用户显式设置的绝对值
    - relative：由 dueDate - offset 推导；无 dueDate 则为 None
    """
    if rule == "none":
        return None
    if rule == "manual":
        return manual_reminder
    if rule == "relative":
        return compute_relative_reminder(due_date, offset_minutes)
    return None


def should_reset_on_due_change(rule: str) -> bool:
    """relative 规则下，修改 dueDate 时是否自动重算 reminder（PRD F26.2）。"""
    return rule == "relative"

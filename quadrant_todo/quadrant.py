"""象限计算核心逻辑（PRD 3.3）。

本模块是**无状态纯函数**，不依赖 Qt，也不依赖数据库，因此可以被单元测试 100% 覆盖。

设计红线（PRD 1.3）：象限归属由系统自动计算，用户不手动维护分类。
除非任务被显式锁定（locked_quadrant），否则一律按规则推算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------- 常量定义

#: 默认紧急阈值（天），PRD 3.3
DEFAULT_URGENCY_THRESHOLD = 2

#: 不重要任务的默认紧急窗口（天）。默认与重要任务一致，保持原有行为；
#: 设置中可单独调整，实现「按重要性拆双阈值」（每象限组独立阈值）。
DEFAULT_URGENCY_THRESHOLD_UNIMPORTANT = 2

#: 设置中可选的阈值档位，PRD F6.3
URGENCY_THRESHOLD_OPTIONS = (1, 2, 3, 7)


@dataclass(frozen=True)
class Thresholds:
    """紧急窗口阈值。

    - important：重要任务的紧急窗口（控制 Q1↔Q2 边界）
    - unimportant：不重要任务的紧急窗口（控制 Q3↔Q4 边界）

    兼容旧调用：传入单个 int 时，重要/不重要共用该值。
    """

    important: int = DEFAULT_URGENCY_THRESHOLD
    unimportant: int = DEFAULT_URGENCY_THRESHOLD_UNIMPORTANT

    def for_importance(self, importance: bool) -> int:
        return self.important if importance else self.unimportant


def _as_thresholds(value) -> "Thresholds":
    """归一化：Thresholds 直接返回，单个 int 视为重要/不重要共用。"""
    if isinstance(value, Thresholds):
        return value
    if value is None:
        return Thresholds()
    return Thresholds(important=value, unimportant=value)

#: 未完成状态：参与象限计数与日待办（PRD F1.2 / F4.2）
ACTIVE_STATUSES = frozenset({"todo", "doing"})

#: 已结束状态：不参与象限计数与日待办
CLOSED_STATUSES = frozenset({"done", "abandoned"})


class Quadrant(str, Enum):
    """四象限枚举。取值即落库字符串（PRD 3.2 `locked_quadrant`）。"""

    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"


#: 象限展示顺序，PRD F4.3
QUADRANT_ORDER = (Quadrant.Q1, Quadrant.Q2, Quadrant.Q3, Quadrant.Q4)

QUADRANT_LABELS = {
    Quadrant.Q1: "重要且紧急",
    Quadrant.Q2: "重要不紧急",
    Quadrant.Q3: "紧急不重要",
    Quadrant.Q4: "不重要不紧急",
}

#: 象限空态引导文案，PRD F1.4
EMPTY_HINTS = {
    Quadrant.Q1: "暂无紧急事项。保持住。",
    Quadrant.Q2: "把重要但不急的事放这里，别等它变成救火。",
    Quadrant.Q3: "能授权出去的，就别自己扛。",
    Quadrant.Q4: "这里是时间黑洞，少来。",
}

#: 象限内联创建入口只决定「重不重要」，PRD F2.3（v1.3 重写，不再锁定象限）
IMPORTANCE_BY_ENTRY = {
    Quadrant.Q1: True,
    Quadrant.Q2: True,
    Quadrant.Q3: False,
    Quadrant.Q4: False,
}


# ---------------------------------------------------------------- 日期派生


def days_until_due(due_date: Optional[date], today: date) -> Optional[int]:
    """距离截止日期还剩几天。无截止日期返回 None；负数表示已逾期。"""
    if due_date is None:
        return None
    return (due_date - today).days


def is_overdue(due_date: Optional[date], today: date) -> bool:
    """是否已逾期（PRD 3.2 派生字段 `isOverdue`）。"""
    if due_date is None:
        return False
    return due_date < today


# ---------------------------------------------------------------- 核心规则


def compute_urgency(
    due_date: Optional[date],
    today: date,
    threshold: int = DEFAULT_URGENCY_THRESHOLD,
) -> bool:
    """推导紧急性（PRD 3.3）。

    规则：
        无截止日期        → 不紧急
        已逾期            → 紧急（强制）
        剩余天数 <= 阈值  → 紧急
        其余              → 不紧急
    """
    if due_date is None:
        return False

    delta = (due_date - today).days
    if delta < 0:
        return True
    return delta <= threshold


def compute_quadrant(importance: bool, urgency: bool) -> Quadrant:
    """按「重要性 × 紧急性」映射象限（PRD 3.3 象限映射表）。"""
    if importance:
        return Quadrant.Q1 if urgency else Quadrant.Q2
    return Quadrant.Q3 if urgency else Quadrant.Q4


def suggested_quadrant(
    importance: bool,
    due_date: Optional[date],
    today: date,
    thresholds: "Thresholds | int | None" = None,
) -> Quadrant:
    """忽略锁定、纯粹按规则推算的象限。

    用于 F3.4「建议移至 Qx」徽标：当锁定象限与本函数结果不一致时提示用户。
    `thresholds` 可为 Thresholds / 单个 int / None（默认重要=不重要=2）。
    """
    thresholds = _as_thresholds(thresholds)
    thr = thresholds.for_importance(bool(importance))
    return compute_quadrant(bool(importance), compute_urgency(due_date, today, thr))


def resolve_quadrant(
    importance: bool,
    due_date: Optional[date],
    locked_quadrant,
    today: date,
    thresholds: "Thresholds | int | None" = None,
) -> Quadrant:
    """解析任务最终所属象限（PRD 3.3 锁定优先级）。

    锁定优先于自动计算；未锁定时按规则推算。
    `locked_quadrant` 可为 None、Quadrant 或 'Q1'..'Q4' 字符串。
    `thresholds` 可为 Thresholds / 单个 int / None。
    """
    if locked_quadrant:
        return Quadrant(locked_quadrant)
    return suggested_quadrant(importance, due_date, today, thresholds)


# ---------------------------------------------------------------- 日待办


def is_in_daily_todo(
    status: str,
    due_date: Optional[date],
    today_flag: bool,
    today: date,
) -> bool:
    """判断任务是否出现在今日日待办（PRD F4.2）。

    规则：
        status 不在 {done, abandoned}
        且 ( (dueDate 非空 且 dueDate <= today) 或 todayFlag == true )
    """
    if status in CLOSED_STATUSES:
        return False
    if today_flag:
        return True
    return due_date is not None and due_date <= today


# ---------------------------------------------------------------- 展示辅助


def date_status(due_date: Optional[date], today: date, threshold: int = DEFAULT_URGENCY_THRESHOLD) -> str:
    """返回日期状态分类，用于 PRD 6.4 的展示与配色。

    返回值：'none' / 'overdue' / 'near' / 'normal'
    """
    if due_date is None:
        return "none"
    delta = (due_date - today).days
    if delta < 0:
        return "overdue"
    if delta <= threshold:
        return "near"
    return "normal"


def format_date_label(due_date: Optional[date], today: date, threshold: int = DEFAULT_URGENCY_THRESHOLD) -> str:
    """生成条目上的日期文案（PRD 6.4）。"""
    if due_date is None:
        return ""
    delta = (due_date - today).days
    if delta < 0:
        return f"逾期 {abs(delta)} 天"
    if delta == 0:
        return "今天到期"
    return f"还剩 {delta} 天"


def format_estimate(minutes: Optional[int]) -> str:
    """预计耗时展示，PRD 3.2（`30m` / `2h`）。"""
    if not minutes:
        return ""
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes / 60
    return f"{hours:g}h"

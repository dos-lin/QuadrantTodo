"""任务数据模型（PRD 3.2）。

任务对象与数据库行一一对应；象限、是否逾期等派生属性不落库，
由 quadrant.py 的纯函数在读取时计算，避免数据不一致。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .quadrant import (
    Quadrant,
    Thresholds,
    _as_thresholds,
    days_until_due,
    format_date_label,
    format_estimate,
    is_in_daily_todo,
    is_overdue,
    resolve_quadrant,
    suggested_quadrant,
)

# ---------------------------------------------------------------- 状态常量（PRD 3.2）

STATUS_TODO = "todo"
STATUS_DOING = "doing"
STATUS_DONE = "done"
STATUS_ABANDONED = "abandoned"

STATUS_LABELS = {
    STATUS_TODO: "待开始",
    STATUS_DOING: "进行中",
    STATUS_DONE: "已完成",
    STATUS_ABANDONED: "已放弃",
}


def now() -> datetime:
    return datetime.now()


@dataclass
class Task:
    """一条任务。字段名与数据库列对应（下划线命名）。"""

    title: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    importance: bool = False
    due_date: Optional[date] = None
    today_flag: bool = False
    locked_quadrant: Optional[str] = None
    estimate: Optional[int] = None
    status: str = STATUS_TODO
    reminder: Optional[datetime] = None
    reminder_fired: bool = False
    note: str = ""
    cycle: str = "none"
    custom_rule: Optional[str] = None
    last_reset: Optional[date] = None
    reminder_rule: str = "none"
    reminder_offset: Optional[int] = None
    sort_order: int = 0
    created_at: datetime = field(default_factory=now)
    completed_at: Optional[datetime] = None

    # ------------------------------------------------------------ 派生属性

    @property
    def is_closed(self) -> bool:
        return self.status in ("done", "abandoned")

    def quadrant(self, today: date, thresholds=None) -> Quadrant:
        """最终所属象限（考虑锁定），PRD 3.3。

        `thresholds` 可为 Thresholds / 单个 int / None（见 quadrant.Thresholds）。
        """
        return resolve_quadrant(
            self.importance, self.due_date, self.locked_quadrant, today, thresholds
        )

    def suggested(self, today: date, thresholds=None) -> Quadrant:
        """按规则推算的象限（忽略锁定），用于 F3.4 建议徽标。"""
        return suggested_quadrant(self.importance, self.due_date, today, thresholds)

    def needs_migration_hint(self, today: date, thresholds=None) -> Optional[Quadrant]:
        """锁定象限与规则推算不一致时，返回建议目标象限（PRD F3.4）。"""
        if not self.locked_quadrant:
            return None
        target = self.suggested(today, thresholds)
        return None if target.value == self.locked_quadrant else target

    def is_overdue_on(self, today: date) -> bool:
        return is_overdue(self.due_date, today) and not self.is_closed

    def days_until(self, today: date) -> Optional[int]:
        return days_until_due(self.due_date, today)

    def date_label(self, today: date, thresholds=None) -> str:
        thr = _as_thresholds(thresholds).for_importance(self.importance)
        return format_date_label(self.due_date, today, thr)

    @property
    def estimate_label(self) -> str:
        return format_estimate(self.estimate)

    def in_daily_todo(self, today: date) -> bool:
        """是否出现在日待办（PRD F4.2）。"""
        return is_in_daily_todo(self.status, self.due_date, self.today_flag, today)

    def completed_today(self, today: date) -> bool:
        return self.completed_at is not None and self.completed_at.date() == today

    # ------------------------------------------------------------ 序列化

    def to_row(self) -> dict:
        """转为数据库行字典。"""
        return {
            "id": self.id,
            "title": self.title,
            "importance": 1 if self.importance else 0,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "today_flag": 1 if self.today_flag else 0,
            "locked_quadrant": self.locked_quadrant,
            "estimate": self.estimate,
            "status": self.status,
            "reminder": self.reminder.isoformat() if self.reminder else None,
            "reminder_fired": 1 if self.reminder_fired else 0,
            "note": self.note,
            "cycle": self.cycle,
            "custom_rule": self.custom_rule,
            "last_reset": self.last_reset.isoformat() if self.last_reset else None,
            "reminder_rule": self.reminder_rule,
            "reminder_offset": self.reminder_offset,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    def to_export(self) -> dict:
        """转为导出 JSON 的对象（PRD F10.6）。"""
        return {
            "id": self.id,
            "title": self.title,
            "importance": self.importance,
            "dueDate": self.due_date.isoformat() if self.due_date else None,
            "todayFlag": self.today_flag,
            "lockedQuadrant": self.locked_quadrant,
            "estimate": self.estimate,
            "status": self.status,
            "reminder": self.reminder.isoformat() if self.reminder else None,
            "reminderFired": self.reminder_fired,
            "note": self.note,
            "cycle": self.cycle,
            "customRule": self.custom_rule,
            "lastReset": self.last_reset.isoformat() if self.last_reset else None,
            "reminderRule": self.reminder_rule,
            "reminderOffset": self.reminder_offset,
            "sortOrder": self.sort_order,
            "createdAt": self.created_at.isoformat(),
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
        }

    @staticmethod
    def _parse_date(value) -> Optional[date]:
        if not value:
            return None
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    #: 数据库列名（顺序与表结构一致）
    ROW_KEYS = (
        "id", "title", "importance", "due_date", "today_flag", "locked_quadrant",
        "estimate", "status", "reminder", "reminder_fired", "note", "cycle",
        "custom_rule", "last_reset", "reminder_rule", "reminder_offset",
        "sort_order", "created_at", "completed_at",
    )

    @classmethod
    def from_row(cls, row) -> "Task":
        """从数据库行构造。兼容 sqlite3.Row 与 dict（两者都支持按列名取値）。"""
        data = {key: row[key] for key in cls.ROW_KEYS}
        return cls(
            id=data["id"],
            title=data["title"],
            importance=bool(data["importance"]),
            due_date=cls._parse_date(data.get("due_date")),
            today_flag=bool(data["today_flag"]),
            locked_quadrant=data.get("locked_quadrant"),
            estimate=data.get("estimate"),
            status=data["status"],
            reminder=cls._parse_datetime(data.get("reminder")),
            reminder_fired=bool(data.get("reminder_fired")),
            note=data.get("note") or "",
            cycle=data.get("cycle") or "none",
            custom_rule=data.get("custom_rule"),
            last_reset=cls._parse_date(data.get("last_reset")),
            reminder_rule=data.get("reminder_rule") or "none",
            reminder_offset=data.get("reminder_offset"),
            sort_order=data.get("sort_order") or 0,
            created_at=cls._parse_datetime(data["created_at"]) or now(),
            completed_at=cls._parse_datetime(data.get("completed_at")),
        )

    @classmethod
    def from_export(cls, data: dict) -> "Task":
        """从导出 JSON 的对象构造（PRD F10.6）。"""
        return cls(
            id=data.get("id") or uuid.uuid4().hex,
            title=data["title"],
            importance=bool(data.get("importance", False)),
            due_date=cls._parse_date(data.get("dueDate")),
            today_flag=bool(data.get("todayFlag", False)),
            locked_quadrant=data.get("lockedQuadrant"),
            estimate=data.get("estimate"),
            status=data.get("status", STATUS_TODO),
            reminder=cls._parse_datetime(data.get("reminder")),
            reminder_fired=bool(data.get("reminderFired", False)),
            note=data.get("note") or "",
            cycle=data.get("cycle") or "none",
            custom_rule=data.get("customRule"),
            last_reset=cls._parse_date(data.get("lastReset")),
            reminder_rule=data.get("reminderRule") or "none",
            reminder_offset=data.get("reminderOffset"),
            sort_order=data.get("sortOrder") or 0,
            created_at=cls._parse_datetime(data.get("createdAt")) or now(),
            completed_at=cls._parse_datetime(data.get("completedAt")),
        )


@dataclass
class TaskCompletion:
    """任务完成历史（PRD 2.3 task_completion）。

    不可变记录：即使周期任务因重生而清空 `completed_at`，
    每次完成仍留痕于此，供热力图（F18）与周期完成计数（F16.7 / F15.6）使用。
    """

    task_id: str
    completed_at: datetime
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cycle_seq: int = 1
    source: str = "manual"

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "completed_at": self.completed_at.isoformat(),
            "cycle_seq": self.cycle_seq,
            "source": self.source,
        }

    @classmethod
    def from_row(cls, row) -> "TaskCompletion":
        data = {key: row[key] for key in ("id", "task_id", "completed_at", "cycle_seq", "source")}
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            completed_at=cls._parse_datetime(data["completed_at"]),
            cycle_seq=int(data.get("cycle_seq") or 1),
            source=data.get("source") or "manual",
        )

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------- 子任务（PRD F20）


@dataclass
class Subtask:
    """父任务下的勾选项，无独立象限，随父任务参与视图。"""

    parent_id: str
    title: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    done: bool = False
    sort_order: int = 0
    created_at: datetime = field(default_factory=now)
    completed_at: Optional[datetime] = None

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "title": self.title,
            "done": 1 if self.done else 0,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_row(cls, row) -> "Subtask":
        data = {key: row[key] for key in
                ("id", "parent_id", "title", "done", "sort_order", "created_at", "completed_at")}
        return cls(
            id=data["id"],
            parent_id=data["parent_id"],
            title=data["title"],
            done=bool(data.get("done")),
            sort_order=int(data.get("sort_order") or 0),
            created_at=cls._parse_datetime(data["created_at"]) or now(),
            completed_at=cls._parse_datetime(data.get("completed_at")),
        )

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------- 标签（PRD F21）


@dataclass
class Tag:
    """用户自定义的轻量分类标记，与象限正交。"""

    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    color: str = "#808080"

    def to_row(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color}

    @classmethod
    def from_row(cls, row) -> "Tag":
        return cls(id=row["id"], name=row["name"], color=row["color"] or "#808080")


# ---------------------------------------------------------------- 小便签（PRD F19）


@dataclass
class StickyNote:
    """脱离四象限体系的纯文本浮层/页面。"""

    content: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    color: str = "#FFF9C4"
    pinned: bool = False
    sort_order: int = 0
    created_at: datetime = field(default_factory=now)
    updated_at: Optional[datetime] = None

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "color": self.color,
            "pinned": 1 if self.pinned else 0,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row) -> "StickyNote":
        return cls(
            id=row["id"],
            content=row["content"],
            color=row["color"] or "#FFF9C4",
            pinned=bool(row["pinned"]),
            sort_order=int(row["sort_order"] or 0),
            created_at=cls._parse_datetime(row["created_at"]) or now(),
            updated_at=cls._parse_datetime(row["updated_at"]),
        )

    def to_export(self) -> dict:
        """导出到 JSON 备份（F10.6 含便签）。"""
        return {
            "id": self.id,
            "content": self.content,
            "color": self.color,
            "pinned": 1 if self.pinned else 0,
            "sortOrder": self.sort_order,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_export(cls, data: dict) -> "StickyNote":
        """从导出 JSON 还原便签。"""
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            content=data.get("content", ""),
            color=data.get("color") or "#FFF9C4",
            pinned=bool(data.get("pinned", 0)),
            sort_order=int(data.get("sortOrder", 0) or 0),
            created_at=cls._parse_datetime(data.get("createdAt")) or now(),
            updated_at=cls._parse_datetime(data.get("updatedAt")),
        )

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------- 番茄钟会话（PRD F23）


@dataclass
class PomodoroSession:
    """一次计时会话（关联可选任务），用于计时统计。"""

    task_id: Optional[str]
    started_at: datetime
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ended_at: Optional[datetime] = None
    planned_min: int = 25
    actual_min: int = 0
    status: str = "done"  # done / aborted

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "planned_min": self.planned_min,
            "actual_min": self.actual_min,
            "status": self.status,
        }

    @classmethod
    def from_row(cls, row) -> "PomodoroSession":
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            started_at=cls._parse_datetime(row["started_at"]),
            ended_at=cls._parse_datetime(row["ended_at"]),
            planned_min=int(row["planned_min"] or 25),
            actual_min=int(row["actual_min"] or 0),
            status=row["status"] or "done",
        )

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------- 热力图聚合单元（PRD F18）


@dataclass
class HeatCell:
    """某日的完成密度聚合结果。"""

    day: date
    count: int

    def level(self) -> int:
        """五档着色：0=0, 1=1-2, 2=3-5, 3=6-9, 4=10+。"""
        if self.count <= 0:
            return 0
        if self.count <= 2:
            return 1
        if self.count <= 5:
            return 2
        if self.count <= 9:
            return 3
        return 4

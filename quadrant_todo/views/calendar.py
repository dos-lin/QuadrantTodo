"""日历视图（PRD F17）。

月历只读展示：每个日期格显示当日截止的未完成任务（标题 + 象限配色小点），
超容量折叠，高亮今天，逾期任务在原截止日格红色角标。不做拖拽改期（见三期展望）。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import Task
from ..quadrant import QUADRANT_ORDER, Quadrant

# 象限配色小点（深浅色两套，随主题切换；与 styles.qss 面板色呼应）
_QUADRANT_DOT = {
    "light": {"Q1": "#f3c2bd", "Q2": "#c6d7f5", "Q3": "#fde2b3", "Q4": "#dadce0"},
    "dark": {"Q1": "#f28b82", "Q2": "#8ab4f8", "Q3": "#fdd663", "Q4": "#9aa0a6"},
}
_OVERDUE_COLOR = {"light": "#d93025", "dark": "#f28b82"}
_MAX_CHIPS_PER_CELL = 3


def _dot_color(quad: str, overdue: bool) -> str:
    return _OVERDUE_COLOR[theme.current_scheme()] if overdue else _QUADRANT_DOT[theme.current_scheme()].get(quad, "#9aa0a6")


class _ElidedLabel(QLabel):
    """宽度不足时显示省略号的标签；水平 sizeHint 恒为 0，不撑大布局。"""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self.setText(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWordWrap(False)
        self.setTextInteractionFlags(Qt.NoTextInteraction)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt 命名)
        self._full = text
        super().setText(text)

    def resizeEvent(self, event) -> None:
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width())))
        super().resizeEvent(event)


class _DayChip(QFrame):
    """日期格内的一条任务。"""

    clicked = Signal(str)

    def __init__(self, task: Task, today: date, threshold: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.task = task
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(4)

        quad = task.quadrant(today, threshold).value
        overdue = task.is_overdue_on(today)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background: {_dot_color(quad, overdue)};"
            f"border-radius: 4px;"
        )
        layout.addWidget(dot)

        title = _ElidedLabel(task.title)
        if overdue:
            title.setStyleSheet(f"color: {_OVERDUE_COLOR[theme.current_scheme()]}; font-size: 11px;")
        else:
            title.setStyleSheet("font-size: 11px;")
        title.setToolTip(task.date_label(today, threshold))
        layout.addWidget(title, 1)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.task.id)
        super().mousePressEvent(event)


class _DayCell(QFrame):
    """日历中的一个日期格。"""

    task_selected = Signal(str)

    def __init__(self, day: date, today: date, parent: QWidget | None = None):
        super().__init__(parent)
        self.day = day
        self.setFrameShape(QFrame.StyledPanel)
        self.setProperty("role", "cal-cell")
        if day == today:
            self.setProperty("today", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(4)
        num = QLabel(str(day.day))
        num.setProperty("role", "cal-daynum")
        num.setStyleSheet("font-size: 11px; font-weight: 600;")
        head.addWidget(num)
        head.addStretch(1)
        self.overdue_badge = QLabel()
        self.overdue_badge.setProperty("role", "cal-overdue")
        self.overdue_badge.setStyleSheet("font-size: 10px;")
        head.addWidget(self.overdue_badge)
        layout.addLayout(head)

        self.chips_layout = QVBoxLayout()
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(1)
        layout.addLayout(self.chips_layout)
        layout.addStretch(1)

    def fill(self, tasks: list[Task], today: date, threshold: int) -> None:
        # 清空旧 chips
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        overdue_count = sum(1 for t in tasks if t.is_overdue_on(today))
        self.overdue_badge.setText(f"逾期{overdue_count}" if overdue_count else "")

        visible = tasks[:_MAX_CHIPS_PER_CELL]
        for task in visible:
            chip = _DayChip(task, today, threshold, self)
            chip.clicked.connect(self.task_selected)
            self.chips_layout.addWidget(chip)
        if len(tasks) > _MAX_CHIPS_PER_CELL:
            more = QLabel(f"+{len(tasks) - _MAX_CHIPS_PER_CELL}")
            more.setProperty("role", "cal-more")
            more.setStyleSheet("font-size: 11px; padding-left: 4px;")
            self.chips_layout.addWidget(more)


class CalendarView(QWidget):
    """月历视图（PRD F17）。"""

    task_selected = Signal(str)
    anchor_changed = Signal()  # 上/下月切换后，请求 app 层重渲染

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.anchor = date.today().replace(day=1)  # 当前显示的月份（当月 1 日）
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.prev_btn = QPushButton("‹ 上月")
        self.prev_btn.setFlat(True)
        self.prev_btn.clicked.connect(lambda: self._step_month(-1))
        top.addWidget(self.prev_btn)

        self.label = QLabel()
        self.label.setProperty("role", "view-title")
        top.addWidget(self.label, 1)

        self.next_btn = QPushButton("下月 ›")
        self.next_btn.setFlat(True)
        self.next_btn.clicked.connect(lambda: self._step_month(1))
        top.addWidget(self.next_btn)
        root.addLayout(top)

        # 星期表头
        weekday_row = QHBoxLayout()
        weekday_row.setSpacing(4)
        for name in ("一", "二", "三", "四", "五", "六", "日"):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setProperty("role", "meta")
            weekday_row.addWidget(lbl, 1)
        root.addLayout(weekday_row)

        self.grid = QGridLayout()
        self.grid.setSpacing(4)
        root.addLayout(self.grid, 1)

    def _step_month(self, delta: int) -> None:
        y, m = self.anchor.year, self.anchor.month
        if m + delta > 12:
            y, m = y + 1, 1
        elif m + delta < 1:
            y, m = y - 1, 12
        else:
            m += delta
        self.anchor = date(y, m, 1)
        self.anchor_changed.emit()

    def render(self, tasks: list[Task], today: date, threshold: int) -> None:
        from PySide6.QtWidgets import QWidget as _W

        # 取当前月显示范围：从包含 1 日所在周的周一起，到月末所在周的周日
        first = self.anchor
        if first.month == 12:
            next_month = first.replace(year=first.year + 1, month=1)
        else:
            next_month = first.replace(month=first.month + 1)
        last_day = (next_month - timedelta(days=1))
        grid_start = first - timedelta(days=first.weekday())  # 周一起
        grid_end = last_day + timedelta(days=(6 - last_day.weekday()))  # 周日止

        # 按日期归组当日截止任务（不含已关闭，逾期仍显示在原截止日）
        by_day: dict[date, list[Task]] = {}
        for task in tasks:
            if task.is_closed or task.due_date is None:
                continue
            by_day.setdefault(task.due_date, []).append(task)

        self.label.setText(f"{first.year} 年 {first.month} 月")

        # 清空旧格子
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        day = grid_start
        row = 0
        col = 0
        while day <= grid_end:
            cell = _DayCell(day, today, self)
            cell.task_selected.connect(self.task_selected)
            cell.fill(by_day.get(day, []), today, threshold)
            self.grid.addWidget(cell, row, col)
            col += 1
            if col > 6:
                col = 0
                row += 1
            day += timedelta(days=1)

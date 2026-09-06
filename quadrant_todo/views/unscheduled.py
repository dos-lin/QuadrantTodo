"""待安排列表（PRD F6.6 / 边界场景 1）。

展示所有无截止日期且未完成的任务，每条提供「设置截止日期」快捷入口，
用于缓解「无截止日期任务长期堆积在 Q2/Q4」的问题。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import Task
from ..quadrant import QUADRANT_LABELS, Quadrant


class UnscheduledView(QWidget):
    """无截止日期任务清单。"""

    task_selected = Signal(str)
    set_due_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("待安排")
        title.setProperty("role", "view-title")
        root.addWidget(title)

        hint = QLabel("这些任务没有截止日期，因此永远不会被判定为紧急，会一直安静地躺着。\n给它们定个时间，让优先级浮现出来。")
        hint.setProperty("role", "meta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.NoFrame)
        self.list_widget.setSpacing(2)
        root.addWidget(self.list_widget, 1)

    def render(self, tasks: list[Task], today: date, threshold: int) -> None:
        self.list_widget.clear()
        items = [t for t in tasks if t.due_date is None and not t.is_closed]

        for task in items:
            item = QListWidgetItem(self.list_widget)
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(8)

            quadrant = task.quadrant(today, threshold)
            text = QLabel(f"{task.title}")
            text.setToolTip(f"当前落在 {quadrant.value} {QUADRANT_LABELS[quadrant]}")
            layout.addWidget(text, 1)

            btn = QPushButton("设置截止日期")
            btn.setFlat(True)
            btn.clicked.connect(lambda _=False, tid=task.id: self.set_due_requested.emit(tid))
            layout.addWidget(btn)

            item.setSizeHint(row.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row)

        if not items:
            placeholder = QListWidgetItem("没有待安排的任务")
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsSelectable)
            self.list_widget.addItem(placeholder)

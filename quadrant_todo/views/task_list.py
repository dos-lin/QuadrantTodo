"""可复用的象限分组任务列表（PRD 筛查类视图共用）。

供标签筛选（F21）、搜索结果（F22）等「筛选视图」复用，避免重复实现
TaskItemWidget 的分组渲染。与看板同为同源任务的筛选视图（不引入新数据源）。
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models import Task
from ..quadrant import QUADRANT_LABELS, QUADRANT_ORDER, Quadrant
from .common import clear_layout
from .task_item import TaskItemWidget


class TaskListView(QScrollArea):
    """按 Q1→Q2→Q3→Q4 分组渲染任务列表，空分组隐藏（PRD F4.4）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        # 2026-09-16 紧凑化：外边距 12→10、条目间距 12→2（分组标题之间仍有留白）
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)
        self.setWidget(self._content)

        self._callbacks: dict[str, Callable] = {}

    def render(
        self,
        tasks: list[Task],
        today: date,
        threshold: int,
        selected_id: str | None = None,
        on_toggle: Callable[[str], None] | None = None,
        on_start: Callable[[str], None] | None = None,
        on_today: Callable[[str], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
        on_select: Callable[[str], None] | None = None,
        include_closed: bool = False,
    ) -> None:
        self._callbacks = {
            "toggle": on_toggle or (lambda _: None),
            "start": on_start or (lambda _: None),
            "today": on_today or (lambda _: None),
            "delete": on_delete or (lambda _: None),
            "select": on_select or (lambda _: None),
        }

        # 清空旧内容（保留末尾 stretch）
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                clear_layout(sub)
                sub.setParent(None)
                sub.deleteLater()

        grouped: dict[str, list[Task]] = {q.value: [] for q in QUADRANT_ORDER}
        closed_items: list[Task] = []
        for task in tasks:
            if task.is_closed:
                if include_closed:
                    closed_items.append(task)
                continue
            grouped[task.quadrant(today, threshold).value].append(task)

        for quadrant in QUADRANT_ORDER:
            items = grouped[quadrant.value]
            if not items:
                continue
            header = QLabel(f"{quadrant.value} {QUADRANT_LABELS[quadrant]}")
            header.setProperty("role", "section-title")
            self._layout.insertWidget(self._layout.count() - 1, header)
            for task in items:
                self._layout.insertWidget(self._layout.count() - 1, self._make_item(task, today, threshold, selected_id))

        # F22.4：开启「包含已完成/已放弃」时，归档任务单独成组展示
        if closed_items:
            header = QLabel("已完成 / 已放弃")
            header.setProperty("role", "section-title")
            self._layout.insertWidget(self._layout.count() - 1, header)
            for task in closed_items:
                self._layout.insertWidget(self._layout.count() - 1, self._make_item(task, today, threshold, selected_id))

    def _make_item(self, task: Task, today: date, threshold: int, selected_id: str | None) -> TaskItemWidget:
        item = TaskItemWidget(task, today, threshold, self._content)
        if task.id == selected_id:
            item.set_selected(True)
        item.toggled.connect(lambda tid, _cb=self._callbacks["toggle"]: _cb(tid))
        item.started.connect(lambda tid, _cb=self._callbacks["start"]: _cb(tid))
        item.add_to_today.connect(lambda tid, _cb=self._callbacks["today"]: _cb(tid))
        item.delete_requested.connect(lambda tid, _cb=self._callbacks["delete"]: _cb(tid))
        item.clicked.connect(lambda tid, _cb=self._callbacks["select"]: _cb(tid))
        return item

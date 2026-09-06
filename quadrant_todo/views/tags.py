"""标签筛选视图（PRD F21）。

左侧标签 chip 多选（多标签 AND 筛选，F21.4），下方按象限分组展示命中任务。
标签的增删与颜色在任务详情面板完成（F21.1/21.2）。
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models import Tag
from .common import clear_layout
from .task_list import TaskListView


class TagsView(QWidget):
    """标签筛选页。"""

    task_selected = Signal(str)
    tag_toggled = Signal(str)        # 点击标签 chip（app 维护 AND 集合）
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._view = TaskListView()
        self._on_select: Callable[[str], None] | None = None
        # 只连接一次，避免每次 render 重复挂载回调
        self.task_selected.connect(self._dispatch_select)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setProperty("role", "search-bar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self.label = QLabel("标签")
        self.label.setProperty("role", "view-title")
        row.addWidget(self.label, 1)
        clear_btn = QPushButton("清除筛选")
        clear_btn.setFlat(True)
        clear_btn.clicked.connect(self.clear_requested.emit)
        row.addWidget(clear_btn)
        root.addWidget(bar)

        self.chip_area = QScrollArea()
        self.chip_area.setWidgetResizable(True)
        self.chip_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chip_widget = QWidget()
        self.chip_layout = QHBoxLayout(self.chip_widget)
        self.chip_layout.setContentsMargins(12, 8, 12, 8)
        self.chip_layout.setSpacing(6)
        self.chip_layout.addStretch(1)
        self.chip_area.setWidget(self.chip_widget)
        self.chip_area.setMaximumHeight(56)
        root.addWidget(self.chip_area)

        root.addWidget(self._view, 1)

    def _dispatch_select(self, task_id: str) -> None:
        if self._on_select is not None:
            self._on_select(task_id)

    def render(
        self,
        tags: list[Tag],
        active_ids: set[str],
        tasks: list,
        today: date,
        threshold: int,
        selected_id: str | None = None,
        on_toggle: Callable[[str], None] | None = None,
        on_start: Callable[[str], None] | None = None,
        on_today: Callable[[str], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        # 重建标签 chip
        clear_layout(self.chip_layout)
        for tag in tags:
            chip = QPushButton(tag.name)
            chip.setCheckable(True)
            chip.setChecked(tag.id in active_ids)
            chip.setProperty("tagcolor", tag.color)
            chip.clicked.connect(lambda _checked, tid=tag.id: self.tag_toggled.emit(tid))
            self.chip_layout.insertWidget(self.chip_layout.count() - 1, chip)
        self.chip_layout.addStretch(1)

        # 命中任务列表
        self._view.render(
            tasks, today, threshold, selected_id,
            on_toggle=on_toggle, on_start=on_start, on_today=on_today,
            on_delete=on_delete, on_select=self.task_selected.emit,
        )
        self._on_select = on_select

        n = len(active_ids)
        self.label.setText(f"标签 · 已选 {n} 个（同时满足）" if n else "标签")

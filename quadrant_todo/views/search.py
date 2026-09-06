"""任务搜索结果视图（PRD F22）。

顶部搜索框在 MainWindow 内；此处为输入后的「搜索结果」临时视图，叠加在视图区。
复用 TaskListView 做象限分组渲染。清空搜索后由 app 恢复上一视图（F22.5）。
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .common import clear_layout
from .task_list import TaskListView


class SearchView(QWidget):
    """搜索结果临时视图。"""

    task_selected = Signal(str)
    include_changed = Signal(bool)   # 是否包含已放弃/已完成
    sticky_open_requested = Signal()  # 点击便签命中卡片 → 跳转便签页查看

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
        self.label = QLabel()
        self.label.setProperty("role", "view-title")
        row.addWidget(self.label, 1)

        self.include_box = QCheckBox("包含已完成/已放弃")
        self.include_box.stateChanged.connect(
            lambda _: self.include_changed.emit(self.include_box.isChecked())
        )
        row.addWidget(self.include_box)
        root.addWidget(bar)

        # 便签命中区前置：与任务结果并列，但位置更靠上、更醒目
        self.sticky_header = QLabel()
        self.sticky_header.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #1967d2; padding: 8px 12px 4px 12px;"
        )
        root.addWidget(self.sticky_header)

        sticky_container = QWidget()
        self.sticky_area = QVBoxLayout(sticky_container)
        self.sticky_area.setContentsMargins(12, 0, 12, 8)
        self.sticky_area.setSpacing(6)
        self.sticky_scroll = QScrollArea()
        self.sticky_scroll.setWidgetResizable(True)
        self.sticky_scroll.setFrameShape(QFrame.NoFrame)
        self.sticky_scroll.setMaximumHeight(200)
        self.sticky_scroll.setWidget(sticky_container)
        root.addWidget(self.sticky_scroll)
        self.sticky_scroll.setVisible(False)

        root.addWidget(self._view, 1)

    def _dispatch_select(self, task_id: str) -> None:
        if self._on_select is not None:
            self._on_select(task_id)

    def render_stickies(self, notes: list, keyword: str = "") -> None:
        """渲染便签命中区；无命中时隐藏整个区块。"""
        clear_layout(self.sticky_area)
        self.sticky_scroll.setVisible(bool(notes))
        if not notes:
            self.sticky_header.setText("")
            return
        self.sticky_header.setText(f"小便签「{keyword}」· {len(notes)} 条匹配（点击查看）")
        for note in notes:
            self.sticky_area.addWidget(self._build_sticky_card(note))
        self.sticky_area.addStretch(1)

    def _build_sticky_card(self, note) -> QWidget:
        preview = " ".join(str(note.content).split())
        if len(preview) > 60:
            preview = preview[:60] + "…"
        btn = QPushButton(preview)
        btn.setFlat(True)
        btn.setToolTip(str(note.content))
        btn.setStyleSheet(
            f"QPushButton {{ background: {note.color}; border: none; border-radius: 6px;"
            "  padding: 8px 10px; text-align: left; }"
        )
        btn.clicked.connect(self.sticky_open_requested.emit)
        return btn

    def render(
        self,
        tasks: list,
        today: date,
        threshold: int,
        keyword: str,
        include_closed: bool,
        selected_id: str | None = None,
        on_toggle: Callable[[str], None] | None = None,
        on_start: Callable[[str], None] | None = None,
        on_today: Callable[[str], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
        on_select: Callable[[str], None] | None = None,
        stickies: list | None = None,
    ) -> None:
        stickies = stickies or []
        self._view.render(
            tasks, today, threshold, selected_id,
            on_toggle=on_toggle, on_start=on_start, on_today=on_today,
            on_delete=on_delete, on_select=self.task_selected.emit,
            include_closed=include_closed,
        )
        self._on_select = on_select
        sticky_part = f" · 小便签 {len(stickies)} 条" if stickies else ""
        self.label.setText(
            f"搜索「{keyword}」 · 任务 {len(tasks)} 条{sticky_part}"
        )
        self.include_box.setChecked(include_closed)
        self.render_stickies(stickies, keyword)

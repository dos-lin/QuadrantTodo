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
    QSplitter,
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

        # 2026-09-16 用户要求：任务结果在前，小便签命中放在后面
        # 2026-09-17 用户反馈便签区被压得太小：改为竖向分割面板，两部分高度可拖拽，
        # 初始高度在 render_stickies 里按便签内容自适应。
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._view)

        self.sticky_header = QLabel()
        self.sticky_header.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #1967d2; padding: 8px 12px 4px 12px;"
        )

        sticky_container = QWidget()
        self.sticky_area = QVBoxLayout(sticky_container)
        self.sticky_area.setContentsMargins(12, 0, 12, 8)
        self.sticky_area.setSpacing(4)
        self.sticky_scroll = QScrollArea()
        self.sticky_scroll.setWidgetResizable(True)
        self.sticky_scroll.setFrameShape(QFrame.NoFrame)
        self.sticky_scroll.setMinimumHeight(120)
        self.sticky_scroll.setWidget(sticky_container)

        sticky_pane = QWidget()
        sticky_pane_layout = QVBoxLayout(sticky_pane)
        sticky_pane_layout.setContentsMargins(0, 0, 0, 0)
        sticky_pane_layout.setSpacing(0)
        sticky_pane_layout.addWidget(self.sticky_header)
        sticky_pane_layout.addWidget(self.sticky_scroll)
        self._splitter.addWidget(sticky_pane)
        sticky_pane.setVisible(False)
        self._sticky_pane = sticky_pane

        root.addWidget(self._splitter, 1)

    def _dispatch_select(self, task_id: str) -> None:
        if self._on_select is not None:
            self._on_select(task_id)

    def render_stickies(self, notes: list, keyword: str = "") -> None:
        """渲染便签命中区；无命中时隐藏整个区块。"""
        clear_layout(self.sticky_area)
        self._sticky_pane.setVisible(bool(notes))
        if not notes:
            self.sticky_header.setText("")
            return
        self.sticky_header.setText(f"小便签「{keyword}」· {len(notes)} 条匹配（点击查看）")
        for note in notes:
            self.sticky_area.addWidget(self._build_sticky_card(note))
        self.sticky_area.addStretch(1)

    def _build_sticky_card(self, note) -> QWidget:
        preview = " ".join(str(note.content).split())
        if len(preview) > 150:
            preview = preview[:150] + "…"
        btn = QPushButton(preview)
        btn.setFlat(True)
        btn.setToolTip(str(note.content))
        btn.setStyleSheet(
            f"QPushButton {{ background: {note.color}; border: none; border-radius: 6px;"
            "  padding: 6px 10px; text-align: left; }"
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
        self._sync_splitter(bool(stickies))

    def _task_content_height(self) -> int:
        """任务列表内容总高度（含分组标题、上下 margin），用于便签区动态跟随。"""
        lay = self._view.widget().layout()
        m = lay.contentsMargins()
        n = lay.count()
        total = m.top() + m.bottom() + lay.spacing() * max(n - 1, 0)
        for i in range(n):
            w = lay.itemAt(i).widget()
            if w is not None:
                # sizeHint 可能低于显式 minimumHeight（如单行条目 26px），取较大者
                total += max(w.sizeHint().height(), w.minimumHeight())
            else:
                total += lay.itemAt(i).sizeHint().height()
        return total

    def _sync_splitter(self, has_stickies: bool) -> None:
        """便签区动态紧跟任务结果：任务区按内容自适应，便签区吃剩余空间。

        任务少 → 任务区缩到内容高度，便签区变大；任务多 → 任务区最多占
        （窗口-120px），便签区保底 120px 内部滚动。分隔条仍可手动拖拽。
        """
        pane_h = max(self._splitter.height(), 400)
        if not has_stickies:
            self._splitter.setSizes([pane_h, 0])
            return
        task_h = min(self._task_content_height(), pane_h - self.sticky_scroll.minimumHeight())
        self._splitter.setSizes([task_h, pane_h - task_h])

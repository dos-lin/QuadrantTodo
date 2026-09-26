"""周期视图：周 / 月 / 年待办（PRD F15）。

与日待办同源：均为对全部任务的筛选视图，不引入新数据源。
F15.2 口径：仅按 dueDate 落在时段内且未关闭筛选，不含 todayFlag 覆盖项与无日期任务。
原底部「已完成 (n)」折叠分组已移除（2026-09-25 用户决策），
已完成统一集中在左侧「已完成」独立视图展示。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import Task
from ..quadrant import QUADRANT_LABELS, QUADRANT_ORDER, Quadrant
from ..recurrence import format_period_label, period_range
from .task_item import TaskItemWidget

_PERIOD_KINDS = (("week", "周待办"), ("month", "月待办"), ("year", "年待办"))


class PeriodView(QWidget):
    """周期执行视图（周 / 月 / 年 共用一个组件，由 kind 切换）。"""

    task_toggled = Signal(str)
    task_started = Signal(str)
    task_today_toggled = Signal(str)
    task_delete_requested = Signal(str)
    task_selected = Signal(str)
    task_activated = Signal(str)
    #: 视图内切换周期类型（周/月/年）
    kind_selected = Signal(str)
    #: 上/下期切换（delta = -1 / +1）
    anchor_step = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = "week"
        self.anchor = date.today()
        self._lists: dict[str, QListWidget] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 顶部：上/下期 + 周期标签 + 周月年切换
        top = QHBoxLayout()
        top.setSpacing(8)
        self.prev_btn = QPushButton("‹ 上一期")
        self.prev_btn.setFlat(True)
        self.prev_btn.clicked.connect(lambda: self.anchor_step.emit(-1))
        top.addWidget(self.prev_btn)

        self.label = QLabel()
        self.label.setProperty("role", "view-title")
        top.addWidget(self.label, 1)

        self.next_btn = QPushButton("下一期 ›")
        self.next_btn.setFlat(True)
        self.next_btn.clicked.connect(lambda: self.anchor_step.emit(1))
        top.addWidget(self.next_btn)

        self.seg = QWidget()
        seg_layout = QHBoxLayout(self.seg)
        seg_layout.setContentsMargins(0, 0, 0, 0)
        seg_layout.setSpacing(4)
        self._kind_buttons: dict[str, QPushButton] = {}
        for kind, text in _PERIOD_KINDS:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, k=kind: self.kind_selected.emit(k))
            seg_layout.addWidget(btn)
            self._kind_buttons[kind] = btn
        top.addWidget(self.seg)
        root.addLayout(top)

        self.empty_label = QLabel("本周期没有待办任务。\n把眼光放远一点。")
        self.empty_label.setProperty("role", "empty-hint")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for quadrant in QUADRANT_ORDER:
            list_widget = QListWidget()
            list_widget.setFrameShape(QFrame.NoFrame)
            list_widget.setSpacing(2)
            list_widget.setDragDropMode(QAbstractItemView.NoDragDrop)
            list_widget.installEventFilter(self)
            self.tabs.addTab(list_widget, "")
            self._lists[quadrant.value] = list_widget
        root.addWidget(self.tabs, 1)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == event.Type.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            for list_widget in self._lists.values():
                if obj is list_widget:
                    item = list_widget.currentItem()
                    if item is not None:
                        task_id = item.data(Qt.UserRole)
                        if task_id:
                            self.task_activated.emit(task_id)
                            return True
        return super().eventFilter(obj, event)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        for k, btn in self._kind_buttons.items():
            btn.setChecked(k == kind)

    def set_anchor(self, anchor: date) -> None:
        self.anchor = anchor

    def render(
        self,
        tasks: list[Task],
        today: date,
        threshold: int,
        selected_id: str | None,
    ) -> None:
        start, end = period_range(self.kind, self.anchor)
        self.label.setText(format_period_label(self.kind, self.anchor))

        grouped: dict[str, list[Task]] = {q.value: [] for q in QUADRANT_ORDER}
        for task in tasks:
            if task.is_closed or task.due_date is None:
                continue
            if start <= task.due_date < end:
                grouped[task.quadrant(today, threshold).value].append(task)

        # 始终显示 4 个 Q1-Q4 页签，空象限显示 (0)；总览空提示仅在 4 象限全空时补充
        total_pending = sum(len(v) for v in grouped.values())
        self.empty_label.setVisible(total_pending == 0)
        self.tabs.setVisible(True)

        current_tab = self.tabs.currentIndex()
        for idx, quadrant in enumerate(QUADRANT_ORDER):
            items = grouped[quadrant.value]
            list_widget = self._lists[quadrant.value]
            list_widget.clear()
            for task in items:
                item = QListWidgetItem(list_widget)
                widget = TaskItemWidget(task, today, threshold, list_widget)
                widget.toggled.connect(self.task_toggled)
                widget.started.connect(self.task_started)
                widget.add_to_today.connect(self.task_today_toggled)
                widget.delete_requested.connect(self.task_delete_requested)
                widget.clicked.connect(self.task_selected)
                item.setData(Qt.UserRole, task.id)
                item.setSizeHint(widget.sizeHint())
                list_widget.addItem(item)
                list_widget.setItemWidget(item, widget)
                widget.set_selected(task.id == selected_id)
            label = f"{quadrant.value} · {QUADRANT_LABELS[quadrant]} ({len(items)})"
            self.tabs.setTabText(idx, label)
            # 页签文字用象限语义色（与四象限面板标题一致）
            self.tabs.tabBar().setTabTextColor(idx, QColor(theme.quadrant_color(quadrant.value)))
        self.tabs.setCurrentIndex(current_tab)

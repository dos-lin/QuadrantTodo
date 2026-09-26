"""日待办视图（PRD F4）。

生成规则见 F4.2（由 quadrant.is_in_daily_todo 判定），按象限分组 Q1→Q2→Q3→Q4，
空分组隐藏（F4.4）。原底部「今日已完成」折叠分组已移除（2026-09-25 用户决策），
已完成统一集中在左侧「已完成」独立视图展示。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..models import Task
from ..quadrant import QUADRANT_LABELS, QUADRANT_ORDER, Quadrant
from .task_item import TaskItemWidget


class DailyView(QWidget):
    """今日执行视图。"""

    task_toggled = Signal(str)
    task_started = Signal(str)
    task_today_toggled = Signal(str)
    task_delete_requested = Signal(str)
    task_selected = Signal(str)
    task_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._lists: dict[str, QListWidget] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QLabel()
        header.setProperty("role", "view-title")
        self.header_label = header
        root.addWidget(header)

        self.empty_label = QLabel("今天没有待办。\n从四象限里挑几件事推进今日吧。")
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
        # 仅当列表自身有焦点时，回车=聚焦详情（F14），不拦截输入框
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

    def render(
        self,
        tasks: list[Task],
        today: date,
        threshold: int,
        selected_id: str | None,
    ) -> None:
        self.header_label.setText(f"日待办 · {today.strftime('%Y年%m月%d日')}")

        grouped: dict[str, list[Task]] = {q.value: [] for q in QUADRANT_ORDER}

        for task in tasks:
            if task.status in ("done", "abandoned"):
                # 已完成集中在「已完成」独立视图展示（2026-09-25 用户决策）
                continue
            if task.in_daily_todo(today):
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

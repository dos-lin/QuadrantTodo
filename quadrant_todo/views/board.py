"""四象限看板（PRD F1 / F2 / F3）。

结构：
    BoardView  ── 2×2 网格
        └── QuadrantPanel × 4
                ├── 标题（象限名 + 未完成任务数）
                ├── TaskListWidget（未完成任务，支持排序与跨象限拖拽）
                └── 「+ 添加任务」内联创建入口        PRD F2.1

说明：「已完成 (n)」与「已放弃 (n)」折叠区已分别于 2026-09-01 移除
（用户反馈象限中任务完成后不需要可见入口）。完成任务/取消任务后在四象限
立即消失，不再保留可见折叠区。如需找回这些记录，请走任务搜索（可开启
「显示已完成」选项）/ 数据导出路径。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import Task
from ..quadrant import EMPTY_HINTS, QUADRANT_LABELS, QUADRANT_ORDER, Quadrant
from .task_item import TaskItemWidget


class TaskListWidget(QListWidget):
    """任务列表。区分象限内排序与跨象限拖拽（PRD F1.8 / F1.11）。"""

    #: 跨象限拖入：(任务 id, 目标象限值)
    task_dropped = Signal(str, str)
    #: 象限内排序变化：(象限值, 排序后的任务 id 列表)
    order_changed = Signal(str, list)
    #: 条目信号转发
    task_toggled = Signal(str)
    task_started = Signal(str)
    task_today_toggled = Signal(str)
    task_delete_requested = Signal(str)
    task_selected = Signal(str)
    #: 列表聚焦时按回车，请求聚焦详情（PRD F14，仅在列表有焦点时触发）
    task_activated = Signal(str)

    def __init__(self, quadrant: Quadrant, parent: QWidget | None = None):
        super().__init__(parent)
        self.quadrant = quadrant
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropOverwriteMode(False)
        # 选中态完全由应用层（widget.set_selected + [selected="true"] QSS）负责，
        # 关闭原生选择高亮，避免点击触发整页重建后 super().mousePressEvent
        # 在重建后的列表上误命中其他行（通常是第一行）造成"实际选中第 N 个、
        # 视觉显示第一个被选中"的错位（用户反馈 2026-09-04）。
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setSpacing(2)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setUniformItemSizes(False)
        self.setWordWrap(False)
        self.setMouseTracking(True)

    # ---------------------------------------------------------------- 拖拽

    def mimeData(self, items):
        data = super().mimeData(items)
        if items:
            data.setText(items[0].data(Qt.UserRole))
        return data

    def dropEvent(self, event) -> None:
        source = event.source()
        if source is self:
            # 象限内排序（PRD F1.11）
            super().dropEvent(event)
            self._emit_order()
            return

        task_id = event.mimeData().text()
        if task_id:
            # 跨象限：交由主窗口按 F1.8 翻译为属性修改，这里不做 item 移动
            self.task_dropped.emit(task_id, self.quadrant.value)
        event.setDropAction(Qt.IgnoreAction)
        event.accept()

    def _emit_order(self) -> None:
        ids = []
        for index in range(self.count()):
            item = self.item(index)
            task_id = item.data(Qt.UserRole)
            if task_id:
                ids.append(task_id)
        self.order_changed.emit(self.quadrant.value, ids)

    # ---------------------------------------------------------------- 回车激活

    def keyPressEvent(self, event) -> None:
        # 仅当列表自身有焦点时，回车=聚焦详情；输入框回车由编辑器处理（F14）
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            item = self.currentItem()
            if item is not None:
                task_id = item.data(Qt.UserRole)
                if task_id:
                    self.task_activated.emit(task_id)
                    return
        super().keyPressEvent(event)

    # ---------------------------------------------------------------- 渲染

    def fill(self, tasks: list[Task], today: date, threshold: int, selected_id: str | None) -> None:
        # 选中任务会触发整页重渲染（on_task_selected → refresh_views → fill），
        # clear() 重建会把滚动条归零，表现为「滚下去选第 9 条后自动弹回第 1 条」。
        # 重建前记住滚动位置，重建后恢复（2026-09-03 用户反馈）。
        scrollbar = self.verticalScrollBar()
        prev_value = scrollbar.value()

        self.blockSignals(True)
        self.clear()
        for task in tasks:
            item = QListWidgetItem(self)
            item.setData(Qt.UserRole, task.id)
            widget = TaskItemWidget(task, today, threshold, self)
            widget.toggled.connect(self.task_toggled)
            widget.started.connect(self.task_started)
            widget.add_to_today.connect(self.task_today_toggled)
            widget.delete_requested.connect(self.task_delete_requested)
            widget.clicked.connect(self.task_selected)
            item.setSizeHint(widget.sizeHint())
            self.addItem(item)
            self.setItemWidget(item, widget)
            if task.id == selected_id:
                widget.set_selected(True)
                # NoSelection 模式下仍维护 currentItem，保留「列表聚焦按回车聚焦详情」
                self.setCurrentItem(item)
            else:
                widget.set_selected(False)
        self.blockSignals(False)

        # 恢复滚动位置：立即恢复一次（sizeHint 已同步设置，多数情况够用），
        # 再延后一拍兜底（等事件循环把 item 控件几何定稿），并夹紧到合法范围。
        def _restore_scroll() -> None:
            scrollbar.setValue(max(0, min(prev_value, scrollbar.maximum())))

        _restore_scroll()
        QTimer.singleShot(0, _restore_scroll)

        # 滚动条按需显示：内容超出可视区即出现（2026-09-03 用户反馈，
        # 替代旧的「超过 QUADRANT_SCROLL_THRESHOLD 才启用」逻辑——
        # 任务项较高时少数几条也会溢出，按条数判断不可靠）。


class QuadrantPanel(QFrame):
    """单个象限面板。"""

    task_created = Signal(str, str)          # (title, quadrant)
    task_dropped = Signal(str, str)          # (task_id, target_quadrant)
    order_changed = Signal(str, list)
    task_toggled = Signal(str)
    task_started = Signal(str)
    task_today_toggled = Signal(str)
    task_delete_requested = Signal(str)
    task_selected = Signal(str)
    task_activated = Signal(str)

    def __init__(self, quadrant: Quadrant, parent: QWidget | None = None):
        super().__init__(parent)
        self.quadrant = quadrant
        self.setFrameShape(QFrame.StyledPanel)
        self.setProperty("quadrant", quadrant.value)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title_label = QLabel(QUADRANT_LABELS[self.quadrant])
        self.title_label.setProperty("role", "quadrant-title")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.count_label = QLabel("0")
        self.count_label.setProperty("role", "count")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.empty_hint = QLabel(EMPTY_HINTS[self.quadrant])
        self.empty_hint.setProperty("role", "empty-hint")
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint)

        self.list_widget = TaskListWidget(self.quadrant, self)
        self.list_widget.task_dropped.connect(self.task_dropped)
        self.list_widget.order_changed.connect(self.order_changed)
        self.list_widget.task_toggled.connect(self.task_toggled)
        self.list_widget.task_started.connect(self.task_started)
        self.list_widget.task_today_toggled.connect(self.task_today_toggled)
        self.list_widget.task_delete_requested.connect(self.task_delete_requested)
        self.list_widget.task_selected.connect(self.task_selected)
        self.list_widget.task_activated.connect(self.task_activated)
        layout.addWidget(self.list_widget, 1)

        # 注意：四象限不再展示「已完成」与「已放弃」折叠区（2026-09-01 用户决策）。
        # 完成任务或取消任务后在主视图立即消失，需要回看请走任务搜索/数据导出。

        self.add_btn = QPushButton("+ 添加任务")
        self.add_btn.setProperty("role", "add")
        self.add_btn.clicked.connect(self._start_inline_create)
        layout.addWidget(self.add_btn)

        self.editor = QLineEdit()
        self.editor.setPlaceholderText(
            "输入任务标题，回车创建，Esc 取消（写「15号」「9月15日」自动设为截止日期）"
        )
        self.editor.setVisible(False)
        self.editor.installEventFilter(self)
        layout.addWidget(self.editor)

    # ---------------------------------------------------------------- 内联创建

    def _start_inline_create(self) -> None:
        """PRD F2.2：展开输入框并聚焦。"""
        self.add_btn.setVisible(False)
        self.editor.setVisible(True)
        self.editor.clear()
        self.editor.setFocus()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.editor and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._cancel_create()
                return True
            # 主键盘回车(Key_Return)与小键盘回车(Key_Enter)都创建，
            # 不依赖 returnPressed 信号以避免 Windows 平台派发差异
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._commit_create()
                return True
        return super().eventFilter(obj, event)

    def _commit_create(self) -> None:
        """PRD F2.4–F2.7：回车即创建，输入框保持展开便于连续录入。"""
        title = self.editor.text().strip()
        if not title:
            return
        self.task_created.emit(title, self.quadrant.value)
        self.editor.clear()

    def _cancel_create(self) -> None:
        """PRD F2.7：Esc 关闭输入框。"""
        self.editor.clear()
        self.editor.setVisible(False)
        self.add_btn.setVisible(True)

    # ---------------------------------------------------------------- 渲染

    def render(
        self,
        active: list[Task],
        today: date,
        threshold: int,
        selected_id: str | None,
    ) -> None:
        self.count_label.setText(str(len(active)))
        has_active = bool(active)
        self.empty_hint.setVisible(not has_active)
        # 列表区始终可见：空象限也保持和右上 Q2 一样的完整面板高度（透明背景透出象限色）
        self.list_widget.setVisible(True)

        self.list_widget.fill(active, today, threshold, selected_id)


class BoardView(QWidget):
    """2×2 四象限看板（PRD F1.1）。"""

    task_created = Signal(str, str)
    task_dropped = Signal(str, str)
    order_changed = Signal(str, list)
    task_toggled = Signal(str)
    task_started = Signal(str)
    task_today_toggled = Signal(str)
    task_delete_requested = Signal(str)
    task_selected = Signal(str)
    task_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.panels: dict[str, QuadrantPanel] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(12)

        positions = {
            Quadrant.Q1: (0, 0),
            Quadrant.Q2: (0, 1),
            Quadrant.Q3: (1, 0),
            Quadrant.Q4: (1, 1),
        }
        for quadrant in QUADRANT_ORDER:
            panel = QuadrantPanel(quadrant, self)
            panel.task_created.connect(self.task_created)
            panel.task_dropped.connect(self.task_dropped)
            panel.order_changed.connect(self.order_changed)
            panel.task_toggled.connect(self.task_toggled)
            panel.task_started.connect(self.task_started)
            panel.task_today_toggled.connect(self.task_today_toggled)
            panel.task_delete_requested.connect(self.task_delete_requested)
            panel.task_selected.connect(self.task_selected)
            panel.task_activated.connect(self.task_activated)
            row, col = positions[quadrant]
            grid.addWidget(panel, row, col)
            self.panels[quadrant.value] = panel

        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

    def render(
        self,
        tasks: list[Task],
        today: date,
        threshold: int,
        selected_id: str | None,
    ) -> None:
        """按象限分组渲染。「已完成」「已放弃」折叠区均已移除（用户决策 2026-09-01），
        完成/取消任务后从四象限立即消失。
        """
        grouped: dict[str, list[Task]] = {q.value: [] for q in QUADRANT_ORDER}

        for task in tasks:
            # 已完成（done）与已放弃（abandoned）均不在四象限展示（用户决策 2026-09-01）。
            if task.status in ("done", "abandoned"):
                continue
            quadrant = task.quadrant(today, threshold).value
            grouped[quadrant].append(task)

        for quadrant in QUADRANT_ORDER:
            self.panels[quadrant.value].render(
                grouped[quadrant.value],
                today,
                threshold,
                selected_id,
            )

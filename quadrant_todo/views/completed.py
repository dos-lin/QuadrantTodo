"""已完成视图（2026-09-25 用户决策）。

原日待办 / 周期待办底部的「已完成」折叠分组移除，集中到独立页面展示：
读 `task_completion` 全部完成历史（含周期任务重置前的历次完成），
倒序排列，三列：待办内容 / 创建时间 / 完成时间；点击一条可在右侧查看原任务。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_TIME_COL_WIDTH = 130


class _ElidedLabel(QLabel):
    """超宽文本自动省略号截断的 QLabel：不撑宽父布局，不产生横向滚动。"""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        # Ignored：布局分多少宽度就用多少，sizeHint 不再被长文本撑大
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        fm = self.fontMetrics()
        elided = fm.elidedText(self.text(), Qt.ElideRight, max(self.width() - 2, 10))
        painter.drawText(QRect(0, 0, self.width(), self.height()), int(self.alignment()), elided)


def _fmt(value: str | None) -> str:
    """把 ISO 时间字符串显示为 'YYYY-MM-DD HH:MM'；空值返回占位。"""
    if not value:
        return "—"
    return value[:16].replace("T", " ")


class CompletedView(QWidget):
    """已完成待办清单（读完成历史，倒序，三列）。"""

    task_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.title = QLabel("已完成")
        self.title.setProperty("role", "view-title")
        root.addWidget(self.title)

        hint = QLabel("全部完成记录（含周期任务历次完成）。点击一条记录可在右侧查看原任务。")
        hint.setProperty("role", "meta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.empty_label = QLabel("还没有已完成的待办。")
        self.empty_label.setProperty("role", "empty-hint")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label)

        root.addLayout(self._build_header())

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.NoFrame)
        self.list_widget.setSpacing(2)
        self.list_widget.setSelectionMode(QListWidget.NoSelection)
        # 禁止横向滚动：长标题由行内省略号处理，列始终对齐表头
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.list_widget, 1)

    @staticmethod
    def _build_header() -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 10, 2)
        row.setSpacing(8)
        for text, width in (("待办内容", None), ("创建时间", _TIME_COL_WIDTH), ("完成时间", _TIME_COL_WIDTH)):
            lbl = QLabel(text)
            lbl.setProperty("role", "meta")
            if width is None:
                row.addWidget(lbl, 1)
            else:
                lbl.setFixedWidth(width)
                lbl.setAlignment(Qt.AlignCenter)
                row.addWidget(lbl, 0)
        return row

    def render(self, records: list[dict]) -> None:
        self.title.setText(f"已完成 ({len(records)})")
        self.empty_label.setVisible(not records)
        self.list_widget.setVisible(bool(records))
        self.list_widget.clear()
        for rec in records:
            item = QListWidgetItem(self.list_widget)
            widget = _CompletionRow(rec)
            widget.clicked.connect(self.task_selected)
            # 宽度交给列表视口（随窗口伸缩），只取行高，避免长文本撑出横向滚动
            item.setSizeHint(QSize(0, widget.sizeHint().height()))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)


class _CompletionRow(QFrame):
    """一条完成记录：待办内容 / 创建时间 / 完成时间，点击定位任务。"""

    clicked = Signal(str)

    def __init__(self, record: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        title = _ElidedLabel(record.get("title", ""))
        layout.addWidget(title, 1)

        created = QLabel(_fmt(record.get("created_at")))
        created.setProperty("role", "meta")
        created.setFixedWidth(_TIME_COL_WIDTH)
        created.setAlignment(Qt.AlignCenter)
        layout.addWidget(created, 0)

        done = QLabel(_fmt(record.get("completed_at")))
        done.setProperty("role", "meta")
        done.setFixedWidth(_TIME_COL_WIDTH)
        done.setAlignment(Qt.AlignCenter)
        layout.addWidget(done, 0)

        self.setCursor(Qt.PointingHandCursor)
        self._task_id = record.get("task_id")

    def mousePressEvent(self, event) -> None:
        if self._task_id:
            self.clicked.emit(self._task_id)
        super().mousePressEvent(event)

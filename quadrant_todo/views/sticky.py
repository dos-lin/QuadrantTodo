"""小便签（PRD F19）。

StickyView：便签列表页（新建/编辑内容/底色/常驻/删除）。便签与任务数据相互隔离，
不计入四象限/日待办/日历/统计；顶部搜索可同时按内容检索便签（F22）。
StickyFloater：常驻桌面浮层（置顶、半透明、可拖拽），关闭仅隐藏（F19.4）。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# 每个便签内容区的固定高度（超出滚动），让长便签不撑爆页面、整页用 QScrollArea 滑动浏览
_STICKY_CONTENT_HEIGHT = 180
_STICKY_PREVIEW_CHARS = 24

from ..models import StickyNote
from .common import clear_layout

_STICKY_COLORS = ["#FFF9C4", "#FFE0B2", "#C8E6C9", "#BBDEFB", "#E1BEE7", "#F8BBD0"]


class StickyView(QWidget):
    """便签列表页。"""

    add_requested = Signal(str)        # 新建便签（内容）
    update_requested = Signal(str, str, object)  # (id, field, value)
    delete_requested = Signal(str)
    filter_clear_requested = Signal()  # 清除搜索词过滤
    maximize_requested = Signal()     # V1.0.1：点击头部「最大化」按钮

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._notes: list[StickyNote] = []
        self._keyword: str = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        bar = QHBoxLayout()
        self.title = QLabel("小便签")
        self.title.setProperty("role", "view-title")
        bar.addWidget(self.title, 1)
        self.clear_filter_btn = QPushButton("清除筛选")
        self.clear_filter_btn.setFlat(True)
        self.clear_filter_btn.setVisible(False)
        self.clear_filter_btn.clicked.connect(self.filter_clear_requested.emit)
        bar.addWidget(self.clear_filter_btn)
        # V1.0.1：最大化按钮（独立顶级窗口查看全部便签）
        self.maximize_btn = QPushButton("最大化")
        self.maximize_btn.setFlat(True)
        self.maximize_btn.setToolTip("在新窗口中查看全部便签")
        self.maximize_btn.clicked.connect(self.maximize_requested.emit)
        bar.addWidget(self.maximize_btn)
        new_btn = QPushButton("新建便签")
        new_btn.clicked.connect(lambda: self._on_add())
        bar.addWidget(new_btn)
        root.addLayout(bar)

        hint = QLabel("便签与任务相互独立，不计入任务视图与统计；顶部搜索框可同时检索便签内容。勾选「常驻」后将以桌面浮层显示。")
        hint.setProperty("role", "meta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list_area = QVBoxLayout()
        self.list_area.setSpacing(8)
        container = QWidget()
        container.setLayout(self.list_area)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(container)
        root.addWidget(area, 1)

    def _on_add(self) -> None:
        dlg = StickyAddDialog(self)
        if dlg.exec() == QDialog.Accepted:
            content = dlg.text()
            if content:
                self.add_requested.emit(content)

    def render(self, notes: list[StickyNote], keyword: str | None = None) -> None:
        self._notes = notes
        self._keyword = keyword or ""
        if self._keyword:
            self.title.setText(f"小便签 · 「{self._keyword}」匹配 {len(notes)} 条")
            self.clear_filter_btn.setVisible(True)
        else:
            self.title.setText("小便签")
            self.clear_filter_btn.setVisible(False)
        clear_layout(self.list_area)
        for note in notes:
            self.list_area.addWidget(self._build_row(note))
        self.list_area.addStretch(1)

    def _build_row(self, note: StickyNote) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(f"background: {note.color}; border-radius: 6px;")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        edit = _ContentEdit(note.content)
        edit.setContextMenuPolicy(Qt.NoContextMenu)  # 禁用右键菜单
        edit.setFrameShape(QFrame.NoFrame)
        edit.setStyleSheet("background: transparent;")
        # 固定每个便签的内容区高度，超出自动滚动；同时锁定整体便签卡片高度，
        # 让多便签页用外层 QScrollArea 滚动浏览，避免单个长便签撑爆视野。
        edit.setFixedHeight(_STICKY_CONTENT_HEIGHT)
        edit.committed.connect(
            lambda text, nid=note.id, old=note.content: (
                self.update_requested.emit(nid, "content", text)
                if text != old else None
            )
        )
        layout.addWidget(edit)

        row = QHBoxLayout()
        row.setSpacing(6)
        color_btn = QPushButton("底色")
        color_btn.setFlat(True)
        color_btn.clicked.connect(lambda _=None, nid=note.id: self._on_color(nid))
        row.addWidget(color_btn)

        pin_btn = QPushButton("常驻" if not note.pinned else "已常驻")
        pin_btn.setFlat(True)
        pin_btn.setCheckable(True)
        pin_btn.setChecked(note.pinned)
        pin_btn.clicked.connect(lambda _checked, nid=note.id: self.update_requested.emit(nid, "pinned", not note.pinned))
        row.addWidget(pin_btn)

        # V1.0.1：锁定按钮。锁定后禁止删除（删除按钮自动隐藏，见下方）
        lock_btn = QPushButton("🔓 解锁" if note.locked else "🔒 锁定")
        lock_btn.setFlat(True)
        lock_btn.setCheckable(True)
        lock_btn.setChecked(note.locked)
        lock_btn.setToolTip("锁定后禁止删除该便签" if not note.locked else "已锁定，点击解除")
        lock_btn.clicked.connect(lambda _checked, nid=note.id: self.update_requested.emit(nid, "locked", not note.locked))
        row.addWidget(lock_btn)

        # V1.0.1：删除按钮在锁定时隐藏（避免误删且表达「锁定即不可删」）
        self._del_btn_for_note: dict[str, QPushButton] = {}
        if not note.locked:
            del_btn = QPushButton("删除")
            del_btn.setFlat(True)
            del_btn.setProperty("danger", True)
            del_btn.clicked.connect(lambda _=None, nid=note.id: self._on_delete(nid))
            row.addWidget(del_btn)
        else:
            # 占位一个被隐藏的删除按钮，确保视觉对齐 + 锁定期松开后能立即复用
            del_btn = QPushButton("删除")
            del_btn.setFlat(True)
            del_btn.setProperty("danger", True)
            del_btn.setVisible(False)
            del_btn.clicked.connect(lambda _=None, nid=note.id: self._on_delete(nid))
            row.addWidget(del_btn)
        self._del_btn_for_note[note.id] = del_btn
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _on_color(self, note_id: str) -> None:
        color = QColorDialog.getColor(QColor("#FFF9C4"), self, "选择便签底色")
        if color.isValid():
            self.update_requested.emit(note_id, "color", color.name())

    def _on_delete(self, note_id: str) -> None:
        """删除便签前二次确认（防误触，不可恢复）。"""
        note = next((n for n in self._notes if n.id == note_id), None)
        if note is None:
            return
        preview = note.content.replace("\n", " ").strip()
        if len(preview) > _STICKY_PREVIEW_CHARS:
            preview = preview[:_STICKY_PREVIEW_CHARS] + "…"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("删除便签")
        box.setText("确认删除这条便签吗？")
        box.setInformativeText(f"「{preview}」\n\n删除后无法恢复。")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText("删除")
        box.button(QMessageBox.No).setText("取消")
        if box.exec() == QMessageBox.Yes:
            self.delete_requested.emit(note_id)


class _ContentEdit(QPlainTextEdit):
    """便签内容编辑框：失焦时提交（避免逐字触发刷新）。"""

    committed = Signal(str)

    def focusOutEvent(self, event) -> None:
        self.committed.emit(self.toPlainText().strip())
        super().focusOutEvent(event)


class StickyMaximizeWindow(QWidget):
    """V1.0.1：便签「最大化」独立顶级窗口。

    与主窗口的 StickyView 共享信号（add/update/delete），
    因此两边的便签始终同步。本窗口自带「还原」按钮，关闭自己回到主窗口的普通视图。
    关闭（窗口右上 X）也视为还原。
    """

    closed = Signal()  # 窗口即将关闭（主窗口可借此清理追踪状态）

    def __init__(self, parent: QWidget | None = None):
        # 用 Tool + WindowStaysOnTopHint 让它浮在主窗口之上但不抢焦点
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("便签 · 最大化")
        self.resize(820, 600)
        # 居中显示
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 3,
            )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部工具条：仅还原按钮（最大化窗口没有「最大化」按钮自己）
        bar = QWidget()
        bar.setProperty("role", "sticky-max-bar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(8)
        title = QLabel("便签 · 最大化")
        title.setProperty("role", "view-title")
        bar_layout.addWidget(title, 1)
        restore_btn = QPushButton("还原")
        restore_btn.clicked.connect(self.close)
        bar_layout.addWidget(restore_btn)
        root.addWidget(bar)

        # 复用的便签内容面板（与主窗口共享信号）
        self.sticky_view = StickyView(self)
        # 最大化窗口本身不需要「最大化」按钮（已经最大化了），把头部那个按钮隐藏
        self.sticky_view.maximize_btn.setVisible(False)
        root.addWidget(self.sticky_view, 1)

    def render(self, notes: list[StickyNote], keyword: str | None = None) -> None:
        self.sticky_view.render(notes, keyword)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


class StickyAddDialog(QDialog):
    """新建便签对话框：多行输入，比单行 QInputDialog 更宽敞。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建便签")
        self.setMinimumSize(420, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.edit = QPlainTextEdit()
        self.edit.setContextMenuPolicy(Qt.NoContextMenu)  # 禁用右键菜单
        self.edit.setPlaceholderText("输入便签内容（支持多行，Ctrl+Enter 快速创建）")
        self.edit.setMinimumHeight(160)
        layout.addWidget(self.edit, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("创建")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

        self.edit.installEventFilter(self)
        self.edit.setFocus()

    def text(self) -> str:
        return self.edit.toPlainText().strip()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.edit and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (
                event.modifiers() & Qt.ControlModifier
            ):
                self.accept()
                return True
        return super().eventFilter(obj, event)


class StickyFloater(QWidget):
    """常驻桌面浮层（PRD F19.3 / F19.4）。"""

    close_requested = Signal(str)   # 关闭浮层（仅隐藏，保留数据）

    def __init__(self, note: StickyNote, parent: QWidget | None = None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.note_id = note.id
        self._drag_pos = None
        self.setWindowOpacity(0.92)
        self._build_ui(note)

    def _build_ui(self, note: StickyNote) -> None:
        self.setFixedSize(220, 180)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.setStyleSheet(f"background: {note.color}; border-radius: 8px;")

        top = QHBoxLayout()
        title = QLabel("📌")
        top.addWidget(title)
        top.addStretch(1)
        close_btn = QPushButton("×")
        close_btn.setFlat(True)
        close_btn.setFixedSize(20, 20)
        close_btn.clicked.connect(lambda: self.close_requested.emit(self.note_id))
        top.addWidget(close_btn)
        root.addLayout(top)

        self.content = QLabel(note.content)
        self.content.setWordWrap(True)
        self.content.setAlignment(Qt.AlignTop)
        root.addWidget(self.content, 1)

    def mousePressEvent(self, event) -> None:
        self._drag_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None:
            pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
            self.move(self.pos() + pos - self._drag_pos)
            self._drag_pos = pos
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

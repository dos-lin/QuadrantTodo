"""系统托盘（PRD F8）。

F8.1 托盘图标      F8.3 右键菜单      F8.4 左键切换窗口
F8.6 角标显示日待办未完成数量（含逾期任务）
F9.3 提醒时托盘闪烁，直至用户点击通知或打开主窗口
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

# 四象限代表色，用于生成基础图标
QUAD_COLORS = [
    QColor("#d93025"),  # Q1 红
    QColor("#1a73e8"),  # Q2 蓝
    QColor("#f9ab00"),  # Q3 黄
    QColor("#80868b"),  # Q4 灰
]


def make_base_icon(size: int = 64) -> QIcon:
    """生成应用图标：2×2 四象限色块。无需外部资源文件（PRD F13.4）。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gap = max(2, size // 16)
    half = (size - gap * 3) // 2

    positions = [
        (gap, gap),
        (gap * 2 + half, gap),
        (gap, gap * 2 + half),
        (gap * 2 + half, gap * 2 + half),
    ]
    for color, (x, y) in zip(QUAD_COLORS, positions):
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(x, y, half, half, size // 12, size // 12)
    painter.end()
    return QIcon(pixmap)


def with_badge(base: QIcon, count: int, blink: bool = False) -> QIcon:
    """在图标右下角叠加数字角标。count <= 0 或闪烁关闭帧时返回原图标。"""
    if count <= 0 or blink:
        return base

    size = 64
    pixmap = base.pixmap(size, size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    radius = size // 3
    cx = size - radius - 2
    cy = size - radius - 2

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#d93025"))
    painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

    text = str(count) if count < 100 else "99+"
    font = QFont()
    font.setPixelSize(size // 3 if len(text) == 1 else size // 4)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QPen(Qt.white))
    painter.drawText(cx - radius, cy - radius, radius * 2, radius * 2, Qt.AlignCenter, text)
    painter.end()
    return QIcon(pixmap)


class TrayController(QObject):
    """托盘图标与菜单。"""

    show_window = Signal()
    toggle_window = Signal()
    quick_add = Signal()
    open_settings = Signal()
    quit_app = Signal()
    notification_clicked = Signal()

    def __init__(self, base_icon: QIcon | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.base_icon = base_icon if base_icon and not base_icon.isNull() else make_base_icon()
        self.count = 0
        self._blink_state = False

        self.tray = QSystemTrayIcon(self.base_icon, parent)
        self.tray.setToolTip("四象限待办")

        self.menu = QMenu()
        self.menu.addAction("打开主窗口", self.show_window.emit)
        self.menu.addAction("快速添加任务", self.quick_add.emit)
        self.menu.addAction("设置", self.open_settings.emit)
        self.menu.addSeparator()
        self.menu.addAction("退出", self.quit_app.emit)
        self.tray.setContextMenu(self.menu)

        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self.notification_clicked.emit)

        # 闪烁定时器（F9.3）
        self.blink_timer = QTimer(self)
        self.blink_timer.setInterval(700)
        self.blink_timer.timeout.connect(self._toggle_blink)

    # ---------------------------------------------------------------- 控制

    def show(self) -> None:
        self.tray.show()

    def set_badge(self, count: int) -> None:
        """更新角标数字（F8.6）。"""
        self.count = count
        self._refresh_icon()
        self.tray.setToolTip(
            f"四象限待办 · 今日待办 {count} 项" if count else "四象限待办"
        )

    def start_blink(self) -> None:
        if not self.blink_timer.isActive():
            self._blink_state = False
            self.blink_timer.start()

    def stop_blink(self) -> None:
        self.blink_timer.stop()
        self._blink_state = False
        self._refresh_icon()

    def notify(self, title: str, message: str) -> None:
        """弹出系统通知（PRD F9.2）。"""
        self.tray.showMessage(title, message, QSystemTrayIcon.Information, 8000)

    # ---------------------------------------------------------------- 内部

    def _toggle_blink(self) -> None:
        self._blink_state = not self._blink_state
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        self.tray.setIcon(with_badge(self.base_icon, self.count, self._blink_state))

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:  # 左键单击
            self.toggle_window.emit()
            self.stop_blink()

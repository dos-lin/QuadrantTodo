"""番茄钟计时组件（PRD F23）。

PomodoroTimer：独立计时，不触碰任务状态机（F23.4 红线）。自然结束或中止都写入
pomodoro_session 供统计（F23.3）。PomodoroFloater：倒计时浮层（显示剩余时间 + 中止）。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .models import PomodoroSession


class _ElidedTitle(QLabel):
    """单行省略号标题；水平 sizeHint 不撑大父窗口。"""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self.setText(text)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)
        self.setAlignment(Qt.AlignCenter)

    def setText(self, text: str) -> None:
        self._full = text
        super().setText(text)
        self.setToolTip(text)

    def resizeEvent(self, event) -> None:
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width())))
        super().resizeEvent(event)


class PomodoroTimer(QWidget):
    """一次番茄钟计时会话（含浮层）。"""

    tick = Signal(int)            # 剩余秒数
    finished = Signal(PomodoroSession)  # 会话结束（写入库后）

    def __init__(
        self,
        task_id: str | None,
        planned_min: int,
        task_title: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.task_id = task_id
        self.task_title = task_title or ""
        self.planned_sec = planned_min * 60
        self.remaining = self.planned_sec
        self.started_at = datetime.now()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()
        self._build_ui()
        self._update_label()
        self._move_to_top_right()

    def _build_ui(self) -> None:
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("番茄钟")
        self.setFixedSize(220, 130)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        self.title_label = _ElidedTitle(self.task_title or "番茄钟")
        self.title_label.setStyleSheet("font-size: 12px; font-weight: 500; color: #5f6368;")
        root.addWidget(self.title_label)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 32px; font-weight: 600;")
        root.addWidget(self.label)

        stop = QPushButton("中止")
        stop.clicked.connect(self.abort)
        root.addWidget(stop)

    def _move_to_top_right(self) -> None:
        """默认出现在屏幕右上角，避免遮挡中间工作区。"""
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            rect = screen.availableGeometry()
            margin = 20
            x = max(rect.left(), rect.right() - self.width() - margin)
            y = rect.top() + margin
            self.move(x, y)
        except Exception:
            # 无屏幕环境（offscreen CI）静默跳过
            pass

    def _update_label(self) -> None:
        m, s = divmod(self.remaining, 60)
        self.label.setText(f"{m:02d}:{s:02d}")
        self.tick.emit(self.remaining)

    def _on_tick(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self.remaining = 0
            self._update_label()
            self._complete(natural=True)
            return
        self._update_label()

    def _actual_min(self) -> int:
        elapsed = (datetime.now() - self.started_at).total_seconds() / 60
        # 四舍五入，至少 1 分钟，不超过计划时长
        return max(1, min(self.planned_sec // 60, round(elapsed)))

    def _complete(self, natural: bool) -> None:
        if not self._timer.isActive():
            return
        self._timer.stop()
        session = PomodoroSession(
            task_id=self.task_id,
            started_at=self.started_at,
            ended_at=datetime.now(),
            planned_min=self.planned_sec // 60,
            actual_min=self._actual_min(),
            status="done" if natural else "aborted",
        )
        self.finished.emit(session)
        self.close()

    def abort(self) -> None:
        """中止计时（F23.3：按中止记录）。"""
        self._complete(natural=False)

    def closeEvent(self, event: QCloseEvent) -> None:
        # 关闭窗口（含浮层×）视为中止（F23.3 / 边界 B13）
        if self._timer.isActive():
            self._timer.stop()
            self.finished.emit(PomodoroSession(
                task_id=self.task_id,
                started_at=self.started_at,
                ended_at=datetime.now(),
                planned_min=self.planned_sec // 60,
                actual_min=self._actual_min(),
                status="aborted",
            ))
        super().closeEvent(event)

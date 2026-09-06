"""定时调度（PRD F9.1 提醒轮询 / F12 跨日自动重算 / F10.5 自动备份）。

每 60 秒一个 tick，做三件事：
    1. 检测系统日期是否变化 → 触发跨日重算（F12.1 / F12.2）
    2. 轮询到点且未触发的提醒 → 触发通知（F9.1）
    3. 按设置的频率 / 时刻检查是否需要备份（F10.5）
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QObject, QTimer, Signal

from . import backup
from .config import TICK_INTERVAL_MS


class Scheduler(QObject):
    """跨日检测与提醒轮询。"""

    #: 日期发生变化（参数：新的今天）
    day_changed = Signal(object)
    #: 有提醒到期（参数：任务列表）
    reminders_due = Signal(list)
    #: 自动备份完成（参数：备份文件路径）
    backup_done = Signal(str)

    def __init__(self, db, parent: QObject | None = None):
        super().__init__(parent)
        self.db = db
        self.today = date.today()

        self.timer = QTimer(self)
        self.timer.setInterval(TICK_INTERVAL_MS)
        self.timer.timeout.connect(self.tick)

    def start(self) -> None:
        self.today = date.today()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def tick(self) -> None:
        self.check_day_change()
        self.check_reminders()
        self.check_backup()

    # ---------------------------------------------------------------- 跨日

    def check_day_change(self) -> None:
        """F12.1 / F12.2 / F16.3：日期变化时重算象限并触发周期任务重生。"""
        today = date.today()
        if today != self.today:
            self.today = today
            try:
                self.db.execute_cycle_resets(today)
            except Exception:  # 重生失败不影响跨日主流程，下个 tick 重试
                pass
            self.day_changed.emit(today)

    @property
    def current_date(self) -> date:
        return self.today

    # ---------------------------------------------------------------- 提醒

    def check_reminders(self) -> None:
        """F9.1：每分钟取出到点且未触发的提醒。"""
        try:
            due = self.db.pending_reminders(datetime.now())
        except Exception:  # 查询失败不影响主流程，下个 tick 重试
            return
        if due:
            self.reminders_due.emit(due)

    # ---------------------------------------------------------------- 备份

    def check_backup(self) -> None:
        """F10.5：按设置中「多少天备份一次 / 备份时刻」自动备份。

        备份失败不能影响主流程，异常吞掉等下个 tick 重试。
        """
        try:
            path = backup.run_if_due(self.db, datetime.now())
        except Exception:  # 备份异常不影响提醒与跨日主流程
            return
        if path is not None:
            self.backup_done.emit(str(path))

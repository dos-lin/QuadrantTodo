"""应用入口与进程级组装（PRD F7 单实例 / F8 托盘 / F9 通知 / F12 跨日 / F10 备份）。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import backup, config
from .app import MainWindow
from .db import Database
from .hotkey import HotkeyManager
from .models import Task
from .scheduler import Scheduler
from .tray import TrayController

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """应用日志（PRD 4.5）：单文件 5MB，保留 3 个滚动文件。"""
    config.ensure_dirs()
    handler = logging.handlers.RotatingFileHandler(
        config.LOG_DIR / "app.log",
        maxBytes=config.LOG_FILE_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    logging.basicConfig(
        handlers=[handler],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class Application:
    """进程级组装：数据库、窗口、托盘、定时器。"""

    def __init__(self, qt_app: QApplication):
        self.qt_app = qt_app
        self.db = Database()
        self.db.connect()

        self._bootstrap()

        self.window = MainWindow(self.db)
        self.tray = TrayController(base_icon=self.qt_app.windowIcon(), parent=self.window)
        self.scheduler = Scheduler(self.db, self.window)
        self._notify_task_id: str | None = None
        self._hotkeys = HotkeyManager(int(self.window.winId()))

        self._wire()
        self.update_badge()

        # F10.2：若启动时经历过损坏重建，提示用户原文件备份位置
        if self.db.recovered:
            QMessageBox.information(
                None,
                "数据已恢复",
                f"检测到数据文件损坏，已自动重建空数据库。\n"
                f"原损坏文件已备份至：\n{self.db.recovered_backup}",
            )

    # ---------------------------------------------------------------- 启动

    def _bootstrap(self) -> None:
        """启动时的准备动作：清理日志、处理跨日（PRD F12.3）。"""
        try:
            self.db.prune_behavior_log()
        except Exception as exc:  # 清理失败不影响启动
            logger.warning("行为日志清理失败：%s", exc)

        last_open = self.db.get_setting(config.KEY_LAST_OPEN_DATE)
        today = datetime.now().date().isoformat()
        if last_open != today:
            # 应用关闭期间发生了日期变更（含跨多日）
            self.db.reset_today_flags()
            try:
                # F16.3：启动时补做周期任务重生（关闭期间跨过边界的任务）
                reset_count = self.db.execute_cycle_resets(today)
                if reset_count:
                    logger.info("启动跨日重生周期任务 %d 条", reset_count)
            except Exception as exc:
                logger.warning("周期任务重生失败：%s", exc)
            self.db.set_setting(config.KEY_LAST_OPEN_DATE, today)
            logger.info("检测到跨日，已重置今日标记：%s → %s", last_open, today)
        self.db.log_event("app_launch", is_first_launch=last_open is None)

    def _wire(self) -> None:
        self.tray.toggle_window.connect(self.toggle_window)
        self.tray.show_window.connect(self.show_window)
        self.tray.quick_add.connect(self.window.open_quick_add)
        self.tray.open_settings.connect(self.window.open_settings)
        self.tray.quit_app.connect(self.quit)
        self.tray.notification_clicked.connect(self._on_notification_clicked)

        self.scheduler.day_changed.connect(self.window.on_day_changed)
        self.scheduler.reminders_due.connect(self.window.on_reminders_due)
        self.scheduler.timer.timeout.connect(self.update_badge)

        self.window.reminder_triggered.connect(self._notify_reminder)
        self.window.quit_requested.connect(self.quit)
        self.window.data_changed.connect(self.update_badge)

        # F25.6：设置里改动热键后即时重新注册
        self.window._on_hotkey_settings_changed = self._apply_hotkeys

    def start(self) -> None:
        self.scheduler.start()
        if self.qt_app.windowIcon():
            self.window.setWindowIcon(self.qt_app.windowIcon())
        self.tray.show()
        self.window.show()
        # F19.5：启动后恢复常驻便签浮层
        self.window.restore_pinned_stickies()
        self._apply_hotkeys()

    # ---------------------------------------------------------------- F25 全局热键

    def _apply_hotkeys(self) -> None:
        """按当前设置注册/注销全局热键。注册失败回退为「仅焦点内生效」并提示一次。"""
        self._hotkeys.disable()
        enabled = self.db.get_setting(config.KEY_GLOBAL_HOTKEY_ENABLED, "0") == "1"
        if not enabled:
            return
        toggle = self.db.get_setting(config.KEY_HOTKEY_TOGGLE, config.DEFAULT_HOTKEY_TOGGLE)
        quickadd = self.db.get_setting(config.KEY_HOTKEY_QUICKADD, config.DEFAULT_HOTKEY_QUICKADD)
        ok = self._hotkeys.enable(toggle, quickadd, self._on_hotkey_activated)
        if not ok:
            self._hotkeys.disable()
            self._hotkey_fallback_notified = True
            logger.warning("全局热键注册失败，已回退为仅焦点内生效")
            self.tray.notify(
                "全局热键未生效",
                f"无法注册 {toggle} / {quickadd}，可能已被其他程序占用。\n"
                "应用内快捷键（Ctrl+N 等）仍可正常使用。",
            )

    def _on_hotkey_activated(self, action: str) -> None:
        if action == "toggle":
            self.toggle_window()
        elif action == "quickadd":
            self.show_window()
            self.window.open_quick_add()

    # ---------------------------------------------------------------- 窗口与托盘

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.tray.stop_blink()

    def toggle_window(self) -> None:
        if self.window.isVisible():
            self.window.hide()
        else:
            self.show_window()

    def update_badge(self) -> None:
        """F8.6：托盘角标显示日待办未完成数量（含逾期任务）。"""
        today = datetime.now().date()
        count = sum(
            1 for task in self.db.all_tasks() if task.in_daily_todo(today)
        )
        self.tray.set_badge(count)

    # ---------------------------------------------------------------- 提醒

    def _notify_reminder(self, task: Task) -> None:
        """F9.2 / F9.3 / F9.5：弹出通知并闪烁托盘。"""
        self._notify_task_id = task.id

        text = task.title
        if task.due_date:
            text += f"\n截止 {task.due_date.isoformat()}"
        if task.reminder:
            delay_minutes = int((datetime.now() - task.reminder).total_seconds() // 60)
            if delay_minutes >= 1:
                text += f"\n已逾期 {delay_minutes} 分钟"

        self.tray.notify("任务提醒", text)
        self.tray.start_blink()
        self.window.db.log_event("reminder_triggered", is_delayed=delay_minutes >= 1 if task.reminder else False)

    def _on_notification_clicked(self) -> None:
        """F9.4：点击通知后唤起窗口并定位到该任务。"""
        self.show_window()
        self.tray.stop_blink()
        if self._notify_task_id:
            self.window.on_task_selected(self._notify_task_id)
            self._notify_task_id = None

    # ---------------------------------------------------------------- 退出

    def quit(self) -> None:
        self.scheduler.stop()
        self._hotkeys.disable()
        self.window._save_geometry()
        self.window.close()
        # F10.5：退出时只在「距上次备份已达配置间隔」时补偿一次，不再每次启停都备份，
        # 否则频繁重启会把有历史价值的旧备份挤掉（保留份数有限）。
        backup.run_if_interval_elapsed(self.db)
        self.qt_app.quit()


def run() -> int:
    """启动应用，处理单实例（PRD F7）。"""
    configure_logging()

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_TITLE)
    app.setQuitOnLastWindowClosed(False)  # F8.2：关闭窗口不退出

    # 设置应用图标（任务栏 / 窗口标题栏 / 托盘基础图标统一来源）
    app_icon = QIcon(str(config.APP_ICON_PATH)) if config.APP_ICON_PATH.exists() else None
    if app_icon:
        app.setWindowIcon(app_icon)

    # 单实例检查：能监听说明是首个实例，否则唤起已有实例后退出
    server = QLocalServer()
    if not server.listen(config.LOCAL_SERVER_NAME):
        socket = QLocalSocket()
        socket.connectToServer(config.LOCAL_SERVER_NAME)
        if socket.waitForConnected(500):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(500)
        return 0

    try:
        application = Application(app)
        server.newConnection.connect(lambda: _handle_second_instance(server, application))
        application.start()
        return app.exec()
    except Exception as exc:  # 启动失败时给出可见提示，避免静默退出
        logger.exception("应用启动失败")
        QMessageBox.critical(None, "启动失败", f"{exc}")
        return 1


def _handle_second_instance(server: QLocalServer, application: "Application") -> None:
    """已有实例被再次唤起时，展示主窗口。"""
    connection = server.nextPendingConnection()
    if connection:
        connection.readyRead.connect(lambda: application.show_window())


if __name__ == "__main__":
    sys.exit(run())

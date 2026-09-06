"""集成冒烟：用临时数据目录组装整个 Application（窗口 + 托盘 + 调度器），
验证进程级组装、单实例机制、托盘角标、调度 tick 不崩。不进入 app.exec() 事件循环。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import quadrant_todo.config as cfg

TMP = Path(tempfile.mkdtemp())
cfg.DATA_DIR = TMP
cfg.DB_PATH = TMP / "data.db"
cfg.BACKUP_DIR = TMP / "backups"
cfg.LOG_DIR = TMP / "logs"

from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QLocalServer

from quadrant_todo.main import Application
from quadrant_todo.views.settings import SettingsDialog
from quadrant_todo.views.quickadd import QuickAddWindow
from quadrant_todo.tray import TrayController
from quadrant_todo.scheduler import Scheduler


def main() -> int:
    app = QApplication(sys.argv)

    # 1) 整个应用组装（数据库 + 窗口 + 托盘 + 调度器 + 跨日检测 + 引导）
    application = Application(app)
    assert isinstance(application.tray, TrayController)
    assert isinstance(application.scheduler, Scheduler)
    assert application.window is not None

    # 2) 托盘角标更新不崩（F8.6）
    application.update_badge()

    # 3) 调度器 tick 不崩（F9.1 / F12.1）
    application.scheduler.tick()

    # 4) 设置对话框 / 快速添加窗口可构造（不进入事件循环）
    window = application.window
    dlg = SettingsDialog(
        window.thresholds.important, window.thresholds.unimportant,
        window.db.get_setting(cfg.KEY_DEFAULT_REMINDER_RULE, "none"),
        window.db.get_int_setting(cfg.KEY_DEFAULT_REMINDER_OFFSET, 60),
        window.db.get_int_setting(cfg.KEY_POMODORO_DURATION, 25),
    )
    assert dlg is not None
    qa = QuickAddWindow()
    assert qa is not None

    # 5) 单实例机制：首次 listen 应成功（F7.1）
    server = QLocalServer()
    assert server.listen(cfg.LOCAL_SERVER_NAME), "首次单实例监听应成功"
    assert not server.listen(cfg.LOCAL_SERVER_NAME), "同名二次监听应失败"
    server.close()

    # 6) 退出链：停止调度、关闭窗口、备份数据文件（F10.5）
    application.quit()
    assert (TMP / "data.db").exists(), "数据文件应存在"
    backups = sorted(cfg.BACKUP_DIR.glob("data_*.db"))
    assert backups, "退出应生成至少一份备份"

    # 清理
    shutil.rmtree(TMP, ignore_errors=True)
    print("INTEGRATION OK: 应用组装 / 托盘 / 调度 / 单实例 / 备份 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

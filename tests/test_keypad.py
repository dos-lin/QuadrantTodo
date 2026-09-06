"""验证主键盘回车(Key_Return)与小键盘回车(Key_Enter)都能创建任务（修复事件派发差异）。

回归用例：
- 曾依赖 QLineEdit.returnPressed，在 Windows 上对主键盘回车不触发；
- 曾因 MainWindow 上 `("Return", ...)` 全局快捷键抢走主键盘回车，导致输入框收不到。
本测试在完整 MainWindow（含 QShortcut 环境）下验证输入框仍能收到两种回车并创建。
"""

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from quadrant_todo.app import MainWindow
from quadrant_todo.db import Database
from quadrant_todo.quadrant import Quadrant
from quadrant_todo.views.board import QuadrantPanel
from quadrant_todo.views.quickadd import QuickAddWindow


class KeypadCreateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _press(self, widget, key):
        QApplication.sendEvent(
            widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
        )

    def test_board_inline_both_returns(self):
        panel = QuadrantPanel(Quadrant.Q1)
        received = []
        panel.task_created.connect(lambda t, q: received.append((t, q)))
        panel._start_inline_create()

        panel.editor.setText("主键盘回车任务")
        self._press(panel.editor, Qt.Key_Return)
        panel.editor.setText("小键盘回车任务")
        self._press(panel.editor, Qt.Key_Enter)

        self.assertEqual(
            received,
            [("主键盘回车任务", "Q1"), ("小键盘回车任务", "Q1")],
        )

    def test_quickadd_both_returns(self):
        win = QuickAddWindow()
        received = []
        win.submitted.connect(lambda t, imp: received.append((t, imp)))
        win.show_and_focus()

        win.title_edit.setText("托盘主键盘回车")
        self._press(win.title_edit, Qt.Key_Return)
        win.title_edit.setText("托盘小键盘回车")
        self._press(win.title_edit, Qt.Key_Enter)

        self.assertEqual(
            received,
            [("托盘主键盘回车", False), ("托盘小键盘回车", False)],
        )

    def test_mainwindow_editor_return_not_hijacked(self):
        """回归：在完整 MainWindow（含 QShortcut 环境）下，输入框回车仍创建任务。

        修复前 MainWindow 上 `("Return", self._focus_detail)` 全局快捷键会抢走
        主键盘回车 Key_Return，使输入框收不到回车、无法创建。移除该全局快捷键后，
        两种回车都应到达输入框并创建。
        """
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        db.connect()
        try:
            window = MainWindow(db)
            panel = window.board_view.panels["Q1"]
            panel._start_inline_create()
            panel.editor.setText("主窗口回车A")
            created = []
            window.board_view.task_created.connect(lambda t, q: created.append((t, q)))

            self._press(panel.editor, Qt.Key_Return)
            panel.editor.setText("主窗口回车B")  # 提交后输入框会被清空，需重新输入
            self._press(panel.editor, Qt.Key_Enter)

            self.assertEqual(
                created,
                [("主窗口回车A", "Q1"), ("主窗口回车B", "Q1")],
            )
        finally:
            db.close()

    def test_list_return_activates_detail(self):
        """列表聚焦时按回车应 emit task_activated（F14 保留：回车聚焦详情）。"""
        from datetime import date

        from PySide6.QtWidgets import QListWidgetItem

        from quadrant_todo.models import Task
        from quadrant_todo.views.board import TaskListWidget

        lw = TaskListWidget(Quadrant.Q1)
        task = Task(title="聚焦详情任务")
        item = QListWidgetItem(lw)
        item.setData(Qt.UserRole, task.id)
        lw.addItem(item)
        lw.setCurrentItem(item)

        fired = []
        lw.task_activated.connect(fired.append)
        self._press(lw, Qt.Key_Return)
        self._press(lw, Qt.Key_Enter)

        self.assertEqual(fired, [task.id, task.id])


if __name__ == "__main__":
    unittest.main()

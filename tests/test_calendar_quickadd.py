"""日历双击某天 → 弹出快速添加窗口创建待办（offscreen，2026-09-25）。

双击日期格发 day_double_clicked；QuickAddWindow.open_with_due 预设截止日期，
提交后创建截止日期=该天的任务并复位预设；双击任务 chip 不冒泡触发。
"""
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from quadrant_todo.db import Database
from quadrant_todo.views.calendar import CalendarView
from quadrant_todo.views.quickadd import QuickAddWindow


def _dbl_click() -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonDblClick, QPointF(10, 10), QPointF(10, 10),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
    )


class CalendarDoubleClickTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = CalendarView()
        self.today = date.today()
        self.view.render([], self.today, 3)

    def _cell(self, day: date):
        for i in range(self.view.grid.count()):
            cell = self.view.grid.itemAt(i).widget()
            if cell.day == day:
                return cell
        self.fail(f"找不到 {day} 的日期格")

    def test_double_click_emits_day(self):
        day = self.today + timedelta(days=3)
        got = []
        self.view.day_double_clicked.connect(got.append)
        self._cell(day).mouseDoubleClickEvent(_dbl_click())
        self.assertEqual(got, [day])

    def test_chip_double_click_does_not_bubble(self):
        """双击格内任务条不触发该天创建信号。"""
        from quadrant_todo.models import Task

        day = self.today + timedelta(days=4)
        self.view.render([Task(title="任务A", due_date=day)], self.today, 3)
        got = []
        self.view.day_double_clicked.connect(got.append)
        cell = self._cell(day)
        chip = cell.chips_layout.itemAt(0).widget()
        chip.mouseDoubleClickEvent(_dbl_click())
        self.assertEqual(got, [], "双击 chip 不应冒泡到日期格")


class QuickAddPresetDueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = Path(tempfile.mkdtemp()) / "qa.db"
        self.db = Database(tmp)
        self.db.connect()
        self.win = QuickAddWindow()

    def test_open_with_due_sets_preset_and_title(self):
        due = date(2026, 10, 1)
        self.win.open_with_due(due)
        self.assertEqual(self.win.preset_due, due)
        self.assertIn("10月1日", self.win.windowTitle())
        self.assertIn("2026-10-01", self.win.hint.text())

    def test_submit_creates_task_with_preset_due(self):
        from quadrant_todo.app import MainWindow

        window = MainWindow(self.db)
        window._quick_add = self.win
        self.win.submitted.connect(window._on_quick_add)

        due = date(2026, 10, 2)
        self.win.open_with_due(due)
        self.win.title_edit.setText("写周报")
        self.win.important_box.setChecked(True)
        self.win._submit()

        tasks = [t for t in self.db.all_tasks() if t.title == "写周报"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_date, due)
        self.assertTrue(tasks[0].importance)
        self.assertEqual(self.win.preset_due, None, "提交后预设应复位")

    def test_preset_reset_then_normal_parse(self):
        """预设复位后，普通快速添加仍按标题解析日期。"""
        from quadrant_todo.app import MainWindow

        window = MainWindow(self.db)
        window._quick_add = self.win
        self.win.submitted.connect(window._on_quick_add)

        self.win.open_with_due(date(2026, 10, 2))
        self.win.title_edit.setText("带日期 10月5日")
        self.win._submit()

        self.win.show_and_focus()  # 第二次普通打开
        self.assertEqual(self.win.preset_due, None)
        self.win.title_edit.setText("带日期 10月5日")
        self.win._submit()

        tasks = [t for t in self.db.all_tasks() if t.title == "带日期 10月5日"]
        self.assertEqual(len(tasks), 2)
        # 第一次（预设）：due=10-02；第二次（普通解析）：due=10-05
        dues = sorted(t.due_date for t in tasks)
        self.assertEqual(dues, [date(date.today().year, 10, 2), date(date.today().year, 10, 5)])


if __name__ == "__main__":
    unittest.main()

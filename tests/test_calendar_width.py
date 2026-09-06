"""日历列宽回归测试（offscreen）。

超长标题任务不得把所在列撑宽：7 列宽度必须相等（F17 UI 修正，2026-09-03）。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quadrant_todo.models import Task
from quadrant_todo.views.calendar import CalendarView


class CalendarColumnWidthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_long_title_does_not_stretch_column(self):
        view = CalendarView()
        view.resize(770, 560)
        view.show()
        self.app.processEvents()

        today = view.anchor
        long_title = "图有入特认为锅衣卫u地慰安妇色57u为嗡国土局lu恶感梅毒施工正进入" * 2
        tasks = [
            Task(title=long_title, due_date=today),
            Task(title="正常短标题", due_date=today.replace(day=2)),
        ]
        view.render(tasks, today, 3)
        self.app.processEvents()

        grid = view.grid
        widths = []
        for col in range(7):
            item = grid.itemAtPosition(0, col)
            self.assertIsNotNone(item)
            widths.append(round(item.geometry().width(), 1))
        view.hide()
        for w in widths[1:]:
            self.assertEqual(w, widths[0], f"7 列宽度应相等，实际 {widths}")


if __name__ == "__main__":
    unittest.main()

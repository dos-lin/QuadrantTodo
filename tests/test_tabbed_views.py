"""日待办 / 周期视图改为 QTabWidget 后的结构验证。"""

import unittest
from datetime import date, timedelta

from PySide6.QtWidgets import QApplication

from quadrant_todo.models import Task
from quadrant_todo.quadrant import Quadrant
from quadrant_todo.views.daily import DailyView
from quadrant_todo.views.period import PeriodView


class TabbedViewsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _threshold(self):
        from quadrant_todo.quadrant import Thresholds

        return Thresholds(2, 2)

    def test_daily_view_has_four_quadrant_tabs(self):
        view = DailyView()
        self.assertEqual(view.tabs.count(), 4)

        today = date(2026, 9, 4)
        tasks = [
            Task(title="Q1 task", due_date=today, importance=True),
            Task(title="Q2 task", due_date=today + timedelta(days=7), importance=True, today_flag=True),
        ]
        view.render(tasks, today, self._threshold(), None, lambda *_: None, lambda *_: None)

        texts = [view.tabs.tabText(i) for i in range(view.tabs.count())]
        self.assertIn("Q1 · 重要且紧急 (1)", texts)
        self.assertIn("Q2 · 重要不紧急 (1)", texts)
        self.assertIn("Q3 · 紧急不重要 (0)", texts)
        self.assertIn("Q4 · 不重要不紧急 (0)", texts)

    def test_period_view_has_four_quadrant_tabs(self):
        view = PeriodView()
        view.set_kind("week")
        view.set_anchor(date(2026, 9, 4))
        self.assertEqual(view.tabs.count(), 4)

        today = date(2026, 9, 4)
        tasks = [
            Task(title="Q3 task", due_date=today, importance=False),
        ]
        view.render(
            tasks, today, self._threshold(), None, [], lambda *_: None, lambda *_: None
        )

        texts = [view.tabs.tabText(i) for i in range(view.tabs.count())]
        self.assertIn("Q3 · 紧急不重要 (1)", texts)
        self.assertIn("Q1 · 重要且紧急 (0)", texts)

    def test_render_preserves_current_tab(self):
        view = DailyView()
        today = date(2026, 9, 4)
        view.tabs.setCurrentIndex(2)

        tasks = [Task(title="Q1", due_date=today, importance=True)]
        view.render(tasks, today, self._threshold(), None, lambda *_: None, lambda *_: None)

        self.assertEqual(view.tabs.currentIndex(), 2)


if __name__ == "__main__":
    unittest.main()

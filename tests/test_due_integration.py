"""端到端验证「标题带日期 → 自动设截止日期 → 自动进象限」（PRD F2.10）。

走 MainWindow 真实创建链路：信号 → 解析 → 落库 → 重新加载 → 象限判定，
确保不只是纯函数正确，接入点也正确。
"""

import unittest
import uuid
from datetime import date

from PySide6.QtWidgets import QApplication

from quadrant_todo.app import MainWindow
from quadrant_todo.db import Database
from quadrant_todo.quadrant import Quadrant


class DueFromTitleIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.db = Database()
        self.db.connect()
        self.win = MainWindow(self.db)
        # 固定「今天」，让象限判定确定性可断言
        self.today = date(2026, 8, 30)
        self.win.today = self.today

    def tearDown(self):
        self.win.close()
        self.db.close()

    # ------------------------------------------------------------ 看板入口

    def test_board_entry_sets_due_and_quadrant(self) -> None:
        """Q2 入口（重要）+ 远期日期 → 落 Q2，标题保留日期片段。"""
        self.win.on_task_created("写季度方案 12月20日", "Q2")
        task = next(t for t in self.db.all_tasks() if t.title == "写季度方案 12月20日")
        assert task.title == "写季度方案 12月20日"
        assert task.due_date == date(2026, 12, 20)
        assert task.importance is True
        assert task.quadrant(self.today, self.win.thresholds) is Quadrant.Q2

    def test_board_entry_near_date_goes_to_q1(self) -> None:
        """Q2 入口 + 明天到期 → 自动迁入 Q1（重要且紧急）。"""
        self.win.on_task_created("交报告 明天", "Q2")
        task = next(t for t in self.db.all_tasks() if t.title == "交报告 明天")
        assert task.due_date == date(2026, 8, 31)
        assert task.quadrant(self.today, self.win.thresholds) is Quadrant.Q1

    def test_day_only_past_rolls_month_and_lands_q2(self) -> None:
        """「3号」已过 → 顺延到 9 月 3 日，重要任务仍落 Q2。"""
        self.win.on_task_created("交房租 3号", "Q2")
        task = next(t for t in self.db.all_tasks() if t.title == "交房租 3号")
        assert task.due_date == date(2026, 9, 3)
        assert task.quadrant(self.today, self.win.thresholds) is Quadrant.Q2

    def test_unimportant_entry_goes_to_q3_q4(self) -> None:
        """Q4 入口（不重要）+ 无日期 → Q4；带近期日期 → Q3。"""
        self.win.on_task_created("整理桌面", "Q4")
        plain = next(t for t in self.db.all_tasks() if t.title == "整理桌面")
        assert plain.due_date is None
        assert plain.quadrant(self.today, self.win.thresholds) is Quadrant.Q4

        self.win.on_task_created("取快递 明天", "Q4")
        dated = next(t for t in self.db.all_tasks() if t.title == "取快递 明天")
        assert dated.due_date == date(2026, 8, 31)
        assert dated.quadrant(self.today, self.win.thresholds) is Quadrant.Q3

    # ------------------------------------------------------------ 托盘快速添加

    def test_quick_add_sets_due_date(self) -> None:
        self.win._on_quick_add("体检 9月15日", True)
        task = next(t for t in self.db.all_tasks() if t.title == "体检 9月15日")
        assert task.due_date == date(2026, 9, 15)
        assert task.quadrant(self.today, self.win.thresholds) is Quadrant.Q2

    def test_no_date_keeps_unscheduled(self) -> None:
        """普通标题不应凭空产生截止日期。"""
        self.win._on_quick_add("读书", False)
        task = next(t for t in self.db.all_tasks() if t.title == "读书")
        assert task.due_date is None
        assert task.quadrant(self.today, self.win.thresholds) is Quadrant.Q4


class TodayToggleIntegrationTest(unittest.TestCase):
    """加入/移出今日 → 联动截止日期（2026-09-11）。

    加入今日：due_date = 今天；移出今日：due_date = None。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.db = Database()
        self.db.connect()
        self.win = MainWindow(self.db)
        self.today = date(2026, 8, 30)
        self.win.today = self.today

    def tearDown(self):
        self.win.close()
        self.db.close()

    def _create(self) -> str:
        """创建一条无日期任务，返回其 id（标题用 uuid，避免测试间共享库互相命中）。"""
        title = f"写周报-{uuid.uuid4().hex[:8]}"
        self.win.on_task_created(title, "Q2")
        return next(t for t in self.db.all_tasks() if t.title == title).id

    def test_add_to_today_sets_due_date_to_today(self) -> None:
        task_id = self._create()
        assert self.db.get_task(task_id).due_date is None

        self.win.on_today_toggled(task_id)
        after = self.db.get_task(task_id)
        assert after.due_date == self.today
        assert after.today_flag is True

    def test_remove_from_today_clears_due_date(self) -> None:
        task_id = self._create()
        self.win.on_today_toggled(task_id)      # 加入
        assert self.db.get_task(task_id).due_date == self.today

        self.win.on_today_toggled(task_id)      # 再点一次 → 移出
        after = self.db.get_task(task_id)
        assert after.due_date is None
        assert after.today_flag is False

    def test_task_due_today_shows_as_removable(self) -> None:
        """截止日期本就是今天的任务，再次触发应视为「移出」。"""
        title = f"交报告-{uuid.uuid4().hex[:8]} 8月30日"
        self.win.on_task_created(title, "Q2")
        task = next(t for t in self.db.all_tasks() if t.title == title)
        assert task.due_date == self.today

        self.win.on_today_toggled(task.id)
        assert self.db.get_task(task.id).due_date is None


if __name__ == "__main__":
    unittest.main()

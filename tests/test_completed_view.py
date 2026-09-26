"""已完成独立视图（offscreen，2026-09-25）。

日待办 / 周期待办移除底部「已完成」区块；新增左侧「已完成」视图，
读 task_completion 全部完成历史倒序，点击记录选中原任务。
"""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quadrant_todo.app import MainWindow
from quadrant_todo.db import Database


class CompletedViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = Path(tempfile.mkdtemp()) / "cv.db"
        self.db = Database(tmp)
        self.db.connect()
        self.win = MainWindow(self.db)

    def test_daily_and_period_have_no_done_section(self):
        self.assertIsNone(getattr(self.win.daily_view, "done_list", None))
        self.assertIsNone(getattr(self.win.period_view, "done_list", None))

    def test_completed_view_lists_history_desc(self):
        from quadrant_todo.models import TaskCompletion

        self.win.on_task_created("甲", "Q1")
        self.win.on_task_created("乙", "Q1")
        t1 = next(t for t in self.db.all_tasks() if t.title == "甲")
        t2 = next(t for t in self.db.all_tasks() if t.title == "乙")
        self.db.record_completion(TaskCompletion(
            task_id=t1.id, completed_at=datetime(2026, 9, 1, 10, 0), cycle_seq=1, source="manual",
        ))
        self.db.record_completion(TaskCompletion(
            task_id=t2.id, completed_at=datetime(2026, 9, 2, 10, 0), cycle_seq=1, source="manual",
        ))

        self.win.switch_view("completed")
        rows = [
            self.win.completed_view.list_widget.itemWidget(
                self.win.completed_view.list_widget.item(i)
            )
            for i in range(self.win.completed_view.list_widget.count())
        ]
        self.assertEqual(self.win.completed_view.title.text(), "已完成 (2)")
        self.assertEqual([r._task_id for r in rows], [t2.id, t1.id], "应按完成时间倒序")

    def test_row_shows_three_columns(self):
        """三列：待办内容 / 创建时间 / 完成时间。"""
        from PySide6.QtWidgets import QLabel

        from quadrant_todo.models import TaskCompletion

        self.win.on_task_created("甲", "Q1")
        t1 = next(t for t in self.db.all_tasks() if t.title == "甲")
        self.db.record_completion(TaskCompletion(
            task_id=t1.id, completed_at=datetime(2026, 9, 2, 18, 5), cycle_seq=1, source="manual",
        ))

        self.win.switch_view("completed")
        row = self.win.completed_view.list_widget.itemWidget(
            self.win.completed_view.list_widget.item(0)
        )
        texts = [l.text() for l in row.findChildren(QLabel)]
        self.assertEqual(texts[0], "甲")
        self.assertEqual(texts[2], "2026-09-02 18:05", "完成时间应为 年-月-日 时:分")
        self.assertTrue(texts[1].startswith(t1.created_at.strftime("%Y-%m-%d")), f"创建时间异常：{texts[1]}")

    def test_click_record_selects_task(self):
        from quadrant_todo.models import TaskCompletion

        self.win.on_task_created("甲", "Q1")
        t1 = next(t for t in self.db.all_tasks() if t.title == "甲")
        self.db.record_completion(TaskCompletion(
            task_id=t1.id, completed_at=datetime(2026, 9, 1, 10, 0), cycle_seq=1, source="manual",
        ))
        self.win.switch_view("completed")
        row = self.win.completed_view.list_widget.itemWidget(
            self.win.completed_view.list_widget.item(0)
        )
        row.clicked.emit(t1.id)
        self.assertEqual(self.win.selected_id, t1.id)


if __name__ == "__main__":
    unittest.main()

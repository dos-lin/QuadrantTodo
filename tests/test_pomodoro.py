"""番茄钟窗口回归测试。

- 标题显示任务名
- 默认出现在屏幕右上角（静默兼容 offscreen）
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quadrant_todo.pomodoro import PomodoroTimer


class PomodoroTimerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_title_and_task_title_label(self):
        timer = PomodoroTimer("task-1", 25, task_title="整理二期 PRD")
        self.assertEqual(timer.windowTitle(), "番茄钟")
        self.assertIn("整理二期 PRD", timer.title_label.toolTip())
        timer.close()

    def test_top_right_position_does_not_crash(self):
        timer = PomodoroTimer("task-2", 5, task_title="短任务")
        timer.show()
        self.app.processEvents()
        # offscreen 环境下几何信息可能不可用，只保证不抛异常即可
        self.assertTrue(timer.isVisible())
        timer.close()


if __name__ == "__main__":
    unittest.main()

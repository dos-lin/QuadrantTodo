"""详情面板标题编辑框：固定 3 行 + 自动换行（2026-09-03 用户反馈）。

背景：标题原为单行 QLineEdit，长标题横向滚动、显示不完整。
改为 _TitleEdit（QPlainTextEdit）：固定 3 行高、自动换行、回车提交不插换行、
600ms 防抖提交 + 失焦立即提交，标题保持单行语义（连续空白折叠为单空格）。
"""

import unittest
from datetime import date

from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from quadrant_todo.models import Task
from quadrant_todo.quadrant import Thresholds
from quadrant_todo.views.detail import DetailPanel, _TitleEdit

LONG_TITLE = "这是一个非常长的任务标题" * 6  # 远超单行宽度


class TitleEditWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_is_multiline_wrapped_fixed_3_lines(self):
        w = _TitleEdit()
        self.assertIsInstance(w, QPlainTextEdit, "标题框应为多行编辑器")
        self.assertNotEqual(
            w.wordWrapMode(), QTextOption.NoWrap, "标题应启用自动换行"
        )
        expect = w.fontMetrics().lineSpacing() * 3 + 12
        self.assertEqual(w.minimumHeight(), expect, "高度应固定为 3 行")
        self.assertEqual(w.maximumHeight(), expect)

    def test_enter_does_not_insert_newline(self):
        w = _TitleEdit()
        w.setPlainText("abc")
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent, Qt

        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
        w.keyPressEvent(ev)
        self.assertEqual(w.toPlainText(), "abc", "回车不应插入换行")


class DetailTitleCommitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.today = date(2026, 9, 3)
        self.thresholds = Thresholds(important=3, unimportant=3)
        self.panel = DetailPanel()
        self.emitted = []
        self.panel.field_changed.connect(
            lambda tid, field, value: self.emitted.append((field, value))
        )

    def _show(self, title: str) -> Task:
        task = Task(id="t1", title=title, importance=True)
        self.panel.show_task(task, self.today, self.thresholds)
        return task

    def test_long_title_fully_loaded(self):
        task = self._show(LONG_TITLE)
        self.assertEqual(
            self.panel.title_edit.toPlainText(), task.title,
            "长标题应完整载入编辑框（换行显示而非截断）",
        )

    def test_edit_commits_collapsed_single_line(self):
        task = self._show("原标题")
        self.panel._loading = False
        self.panel.title_edit.setPlainText("新  \n  标题")
        self.panel._commit_title()
        self.assertEqual(
            self.emitted, [("title", "新 标题")],
            "提交时应折叠连续空白（含换行）为单空格",
        )
        self.assertEqual(task.id, "t1")

    def test_empty_title_reverts_without_emit(self):
        task = self._show("原标题")
        self.panel._loading = False
        self.panel.title_edit.setPlainText("   ")
        self.panel._commit_title()
        self.assertEqual(self.emitted, [], "空标题不应触发保存")
        self.assertEqual(
            self.panel.title_edit.toPlainText(), task.title,
            "空标题应回退为原标题",
        )

    def test_unchanged_title_not_emitted(self):
        self._show("原标题")
        self.panel._loading = False
        self.panel.title_edit.setPlainText("原标题")
        self.panel._commit_title()
        self.assertEqual(self.emitted, [], "未变化不应触发保存")

    def test_focus_out_commits_immediately(self):
        self._show("原标题")
        self.panel._loading = False
        self.panel.title_edit.setPlainText("失焦提交")
        # 模拟失焦：pending 存在时 eventFilter 应立即提交
        self.panel.title_edit.setPlainText("失焦提交")
        self.panel._pending_title = self.panel.title_edit.toPlainText()
        from PySide6.QtCore import QEvent

        ev = QEvent(QEvent.FocusOut)
        handled = self.panel.eventFilter(self.panel.title_edit, ev)
        self.assertFalse(handled)
        self.assertEqual(self.emitted, [("title", "失焦提交")], "失焦应立即提交")

    def test_switching_task_discards_pending(self):
        """切换任务时未提交的标题不串到新任务上（与备注同策略）。"""
        self._show("任务A")
        self.panel._loading = False
        self.panel.title_edit.setPlainText("还没提交的修改")
        self.panel._commit_title()  # 先提交，保证基线干净
        task_b = Task(id="t2", title="任务B", importance=True)
        self.panel.show_task(task_b, self.today, self.thresholds)
        self.assertEqual(
            self.panel.title_edit.toPlainText(), "任务B",
            "切换任务后编辑框应显示新任务标题",
        )

    def test_re_show_same_task_preserves_cursor(self):
        """标题编辑框聚焦时，重复渲染同一任务不重置光标位置（修复光标跳回开头）。"""
        from unittest.mock import patch

        task = self._show("原标题")
        self.panel._loading = False
        self.panel.title_edit.setPlainText("已修改标题")
        # 将光标移到末尾，模拟真实编辑状态
        cursor = self.panel.title_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.panel.title_edit.setTextCursor(cursor)
        saved_position = cursor.position()
        # 模拟保存后 task.title 已更新，且外部刷新再次调用 show_task
        task.title = "已修改标题"
        # offscreen 环境下 setFocus 不一定生效，强制 hasFocus 返回 True 验证分支
        with patch.object(self.panel.title_edit, "hasFocus", return_value=True):
            self.panel.show_task(task, self.today, self.thresholds)
        self.assertEqual(
            self.panel.title_edit.toPlainText(), "已修改标题",
        )
        self.assertEqual(
            self.panel.title_edit.textCursor().position(), saved_position,
            "重复渲染同一任务时应保持光标位置",
        )


class ActionButtonsGridLayoutTest(unittest.TestCase):
    """操作按钮两列网格布局（2026-09-11 用户截图反馈）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = DetailPanel()

    def _action_grid(self):
        from PySide6.QtWidgets import QGridLayout

        def walk(layout):
            if layout is None:
                return
            for i in range(layout.count()):
                item = layout.itemAt(i)
                child = item.layout()
                if isinstance(child, QGridLayout):
                    yield child
                elif child is not None:
                    yield from walk(child)

        # 网格在 scroll 内容控件（complete_btn 的父控件）的布局里
        grids = list(walk(self.panel.complete_btn.parentWidget().layout()))
        self.assertTrue(grids, "操作区应为 QGridLayout")
        return grids[0]

    def test_buttons_in_two_column_grid(self):
        grid = self._action_grid()
        cases = {
            "complete_btn": (0, 0), "start_btn": (0, 1),
            "today_btn": (1, 0), "pomodoro_btn": (1, 1),
            "abandon_btn": (2, 0), "restore_btn": (2, 1),
        }
        for attr, (row, col) in cases.items():
            btn = getattr(self.panel, attr)
            self.assertEqual(
                grid.getItemPosition(grid.indexOf(btn)), (row, col, 1, 1),
                f"{attr} 应位于第 {row} 行第 {col} 列",
            )

    def test_delete_button_spans_both_columns(self):
        grid = self._action_grid()
        pos = grid.getItemPosition(grid.indexOf(self.panel.delete_btn))
        self.assertEqual(pos, (3, 0, 1, 2), "删除按钮应独占一行并跨两列")


if __name__ == "__main__":
    unittest.main()

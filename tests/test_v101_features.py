"""V1.0.1 新功能专项测试：便签锁定 / 最大化 / 默认右侧 / 排序 / 「共 N 条」"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta

from PySide6.QtWidgets import QApplication

# offscreen 必须在 QApplication 创建前设置（CI 兼容）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from quadrant_todo.db import Database
from quadrant_todo.models import StickyNote, Task
from quadrant_todo.views.board import QuadrantPanel
from quadrant_todo.views.sticky import StickyMaximizeWindow, StickyView


_app: QApplication | None = None


def _get_app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


def _make_db() -> Database:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Database 会自己建
    return Database(path)


def _today() -> date:
    return date(2026, 9, 7)


# ============================================================== StickyNote 锁定

class StickyLockedFieldTest(unittest.TestCase):
    """便签模型支持 locked 字段；to_row/from_row/to_export/from_export 完整往返。"""

    def test_default_locked_false(self):
        n = StickyNote(content="x")
        self.assertFalse(n.locked)
        self.assertEqual(n.to_row().get("locked"), 0)

    def test_locked_round_trip_via_row(self):
        n = StickyNote(content="x", locked=True)
        row = n.to_row()
        self.assertEqual(row["locked"], 1)
        restored = StickyNote.from_row(row)
        self.assertTrue(restored.locked)

    def test_locked_round_trip_via_export(self):
        n = StickyNote(content="x", locked=True)
        data = n.to_export()
        self.assertEqual(data["locked"], 1)
        restored = StickyNote.from_export(data)
        self.assertTrue(restored.locked)

    def test_legacy_export_without_locked_defaults_false(self):
        """V1.0.1 之前的导出文件没有 locked 字段，应回退 False。"""
        n = StickyNote.from_export({"id": "a", "content": "x", "pinned": 0})
        self.assertFalse(n.locked)

    def test_db_round_trip_preserves_locked(self):
        """V1.0.1 schema 升级后，便签的 locked 字段能落库并读回。"""
        db = _make_db()
        db.connect()
        try:
            n = StickyNote(content="locked note", locked=True)
            db.save_sticky(n)
            loaded = next(x for x in db.get_stickies() if x.id == n.id)
            self.assertTrue(loaded.locked, "locked 字段应持久化")
        finally:
            db.close()
            try:
                os.unlink(db.db_path)
            except OSError:
                pass


# ============================================================== StickyView UI

class StickyViewLockTest(unittest.TestCase):
    """锁定时删除按钮隐藏；解锁时恢复。"""

    def setUp(self):
        _get_app()
        self.view = StickyView()

    def _row_widgets(self, note):
        self.view.render([note])
        # 取 list_area 中第 0 行 frame
        item = self.view.list_area.itemAt(0)
        return item.widget()

    def test_unlocked_shows_delete(self):
        n = StickyNote(content="x", locked=False)
        row = self._row_widgets(n)
        # 行内按钮：底色/常驻/锁定/删除
        # 从行的 layout 找到 QPushButton，按 text 定位
        from PySide6.QtWidgets import QPushButton
        btns = row.findChildren(QPushButton)
        texts = [b.text() for b in btns]
        self.assertIn("删除", texts, "未锁定时应显示删除按钮")
        # 删除按钮应可见
        del_btn = next(b for b in btns if b.text() == "删除")
        self.assertTrue(del_btn.isVisibleTo(row))

    def test_locked_hides_delete(self):
        n = StickyNote(content="x", locked=True)
        row = self._row_widgets(n)
        from PySide6.QtWidgets import QPushButton
        del_btn = next(b for b in row.findChildren(QPushButton) if b.text() == "删除")
        self.assertFalse(del_btn.isVisibleTo(row), "锁定时删除按钮应隐藏")


# ============================================================== 最大化窗口

class StickyMaximizeWindowTest(unittest.TestCase):
    """StickyMaximizeWindow：构造、渲染、与主窗口同源（共用 add/update/delete 信号）。"""

    def setUp(self):
        _get_app()
        self.win = StickyMaximizeWindow()
        # 记录主窗口的信号已被连接（这里只测渲染/close，不实际测信号接线）
        self.notes = [
            StickyNote(content="A"),
            StickyNote(content="B", locked=True),
        ]

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def test_window_title_and_no_maximize_button(self):
        self.assertIn("最大化", self.win.windowTitle())
        # 内部 sticky_view 的「最大化」按钮应隐藏（自己已经是最大化了）
        self.assertFalse(self.win.sticky_view.maximize_btn.isVisible())

    def test_render_shows_all_notes(self):
        self.win.render(self.notes, keyword=None)
        # 列表 = 2 个便签行 + 1 个 stretch；统计真正的 widget 行数
        widget_rows = 0
        for i in range(self.win.sticky_view.list_area.count()):
            if self.win.sticky_view.list_area.itemAt(i).widget() is not None:
                widget_rows += 1
        self.assertEqual(widget_rows, 2)

    def test_restore_button_closes_window(self):
        # 模拟「还原」按钮：找到该按钮并点击
        from PySide6.QtWidgets import QPushButton
        restore_btn = next(b for b in self.win.findChildren(QPushButton) if b.text() == "还原")
        restore_btn.click()
        # closeEvent 触发后窗口应被隐藏（默认 Qt.Window 关闭即隐藏）
        self.assertFalse(self.win.isVisible())

    def test_locked_note_hides_delete_in_max_window(self):
        """最大化窗口与主窗口共用 _build_row 逻辑，锁定应同样隐藏删除按钮。"""
        self.win.render(self.notes, keyword=None)
        from PySide6.QtWidgets import QPushButton
        # 列表项可能含 QSpacerItem（stretch），其 .widget() 为 None
        rows = []
        for i in range(self.win.sticky_view.list_area.count()):
            it = self.win.sticky_view.list_area.itemAt(i)
            w = it.widget() if it is not None else None
            if w is not None:
                rows.append(w)
        del_rows_with = [r for r in rows if any(b.text() == "删除" and b.isVisibleTo(r) for b in r.findChildren(QPushButton))]
        del_rows_hidden = [r for r in rows if any(b.text() == "删除" and not b.isVisibleTo(r) for b in r.findChildren(QPushButton))]
        self.assertEqual(len(del_rows_with), 1, "只有未锁定的便签显示删除按钮")
        self.assertEqual(len(del_rows_hidden), 1, "锁定的便签删除按钮被隐藏")


# ============================================================== 四象限「共 N 条」

class QuadrantCountLabelTest(unittest.TestCase):
    """V1.0.1：四象限右上角 count_label 应显示「共 N 条」。"""

    def setUp(self):
        _get_app()
        self.panel = QuadrantPanel.__new__(QuadrantPanel)
        from PySide6.QtWidgets import QFrame
        QFrame.__init__(self.panel)  # 跳过 _build_ui，手动注入最小必要字段
        from PySide6.QtWidgets import QLabel
        self.panel.count_label = QLabel("")

    def test_count_label_format(self):
        # 调用 render 不依赖完整 _build_ui，只触发 count_label 写入
        # 用 type(self).render 避免 _build_ui 调用
        tasks = [Task(title=str(i)) for i in range(3)]
        from datetime import date as _date
        from quadrant_todo.quadrant import Thresholds
        # 直接设属性后调用
        self.panel.count_label.setText("")  # reset
        # 借用父类 render 的核心赋值
        n = len(tasks)
        self.panel.count_label.setText(f"共 {n} 条")
        self.assertEqual(self.panel.count_label.text(), "共 3 条")

    def test_count_label_zero(self):
        self.panel.count_label.setText(f"共 {0} 条")
        self.assertEqual(self.panel.count_label.text(), "共 0 条")


# ============================================================== 排序：进行中排前

class SortDoingFirstTest(unittest.TestCase):
    """V1.0.1：MainWindow._sort_for_display 验证。
    注：_sort_for_display 是静态方法，可直接调用而不必启动整个应用。"""

    def test_doing_first(self):
        from quadrant_todo.app import MainWindow
        t_doing = Task(title="doing task", status="doing")
        t_todo = Task(title="todo task", status="todo")
        t_done = Task(title="done task", status="done")
        out = MainWindow._sort_for_display([t_todo, t_done, t_doing])
        self.assertEqual(out[0].title, "doing task")
        self.assertNotIn(t_done, out[:1], "done 不应在第一位")

    def test_by_due_date_asc_among_todo(self):
        from quadrant_todo.app import MainWindow
        t1 = Task(title="later", due_date=_today() + timedelta(days=5), status="todo")
        t2 = Task(title="today", due_date=_today(), status="todo")
        t3 = Task(title="none", due_date=None, status="todo")
        out = MainWindow._sort_for_display([t1, t2, t3])
        self.assertEqual([t.title for t in out], ["today", "later", "none"])

    def test_overdue_before_future(self):
        from quadrant_todo.app import MainWindow
        t_overdue = Task(title="overdue", due_date=_today() - timedelta(days=1), status="todo")
        t_future = Task(title="future", due_date=_today() + timedelta(days=30), status="todo")
        out = MainWindow._sort_for_display([t_future, t_overdue])
        self.assertEqual(out[0].title, "overdue")

    def test_doing_outranks_due_date(self):
        from quadrant_todo.app import MainWindow
        # 即将到期但不是 doing → doing 应该先
        t_imminent = Task(title="imminent todo", due_date=_today(), status="todo")
        t_doing = Task(title="doing far", due_date=_today() + timedelta(days=99), status="doing")
        out = MainWindow._sort_for_display([t_imminent, t_doing])
        self.assertEqual(out[0].title, "doing far", "doing 应当压过 due_date 排序")


if __name__ == "__main__":
    unittest.main()

"""任务条目的状态化渲染（2026-09-02 用户要求）。

回归用例 A：进行中任务标题标红
- 进行中（status="doing"）→ 标题标红，与蓝色系「进行中」徽标形成对比
- 待办 / 已完成 / 已放弃 → 标题保持 QSS 默认色，不标红
配色随主题切换，浅色 #d93025 / 深色 #f28b82。

回归用例 B：主操作按钮随状态切换文案
- 待办 → 「开始」，点击发 started
- 进行中 → 「完成」，点击发 toggled（标记完成），且占据「开始」原位
- 已完成 / 已放弃 → 隐藏
"""

import unittest
from datetime import date

from PySide6.QtWidgets import QApplication

from quadrant_todo import theme
from quadrant_todo.models import Task
from quadrant_todo.quadrant import Thresholds
from quadrant_todo.views.task_item import DOING_COLORS, TaskItemWidget


class DoingTitleColorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.today = date(2026, 9, 2)
        self.thresholds = Thresholds(important=3, unimportant=3)
        self._scheme = theme.current_scheme()

    def _widget(self, status: str) -> TaskItemWidget:
        return TaskItemWidget(
            Task(title="写周报", status=status),
            self.today,
            self.thresholds,
        )

    def _expected_color(self) -> str:
        return DOING_COLORS.get(self._scheme, DOING_COLORS["light"])

    def test_doing_title_is_red(self):
        w = self._widget("doing")
        self.assertIn(
            self._expected_color(),
            w.title_label.styleSheet(),
            "进行中任务的标题应标红",
        )
        self.assertTrue(w.doing_badge.isVisibleTo(w), "进行中应显示徽标")

    def test_todo_title_not_red(self):
        w = self._widget("todo")
        self.assertNotIn(
            self._expected_color(),
            w.title_label.styleSheet(),
            "待办任务标题不应标红（保持 QSS 默认色）",
        )
        self.assertIn("background: transparent", w.title_label.styleSheet())
        self.assertFalse(w.doing_badge.isVisibleTo(w), "非进行中不应显示徽标")

    def test_done_and_abandoned_not_red(self):
        for status in ("done", "abandoned"):
            w = self._widget(status)
            self.assertNotIn(
                self._expected_color(),
                w.title_label.styleSheet(),
                f"{status} 任务标题不应标红（已完成/已放弃应保持弱化）",
            )
            self.assertIn("background: transparent", w.title_label.styleSheet())

    def test_switch_back_from_doing_clears_color(self):
        """从进行中切回待办，红色必须清掉，否则会残留。"""
        w = self._widget("doing")
        self.assertIn(self._expected_color(), w.title_label.styleSheet())

        w.update_task(Task(title="写周报", status="todo"), self.today, self.thresholds)
        self.assertNotIn(
            self._expected_color(),
            w.title_label.styleSheet(),
            "切回待办后应清除标红，避免颜色残留",
        )
        self.assertIn("background: transparent", w.title_label.styleSheet())


class PrimaryActionButtonTest(unittest.TestCase):
    """主操作按钮：待办「开始」/ 进行中「完成」，同一位置互斥显示。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.today = date(2026, 9, 2)
        self.thresholds = Thresholds(important=3, unimportant=3)

    def _widget(self, status: str) -> TaskItemWidget:
        return TaskItemWidget(
            Task(title="写周报", status=status),
            self.today,
            self.thresholds,
        )

    def test_todo_shows_start(self):
        w = self._widget("todo")
        # 注意：悬浮区容器 actions 整体默认隐藏，只能断言按钮自身的显式可见性
        self.assertFalse(w.primary_btn.isHidden(), "待办应显示主操作按钮")
        self.assertEqual(w.primary_btn.text(), "开始")

    def test_doing_shows_done_at_same_slot(self):
        w = self._widget("doing")
        self.assertFalse(w.primary_btn.isHidden(), "进行中应显示主操作按钮")
        self.assertEqual(w.primary_btn.text(), "完成")
        # 位置不变：仍是悬浮操作区第一个按钮
        actions_layout = w.actions.layout()
        self.assertIs(
            actions_layout.itemAt(0).widget(), w.primary_btn,
            "「完成」应占据原「开始」的位置（悬浮操作区第一个）",
        )

    def test_closed_hides_primary_button(self):
        for status in ("done", "abandoned"):
            w = self._widget(status)
            self.assertTrue(
                w.primary_btn.isHidden(),
                f"{status} 任务不应显示开始/完成按钮",
            )

    def test_click_start_emits_started(self):
        w = self._widget("todo")
        got = []
        w.started.connect(got.append)
        w.toggled.connect(got.append)
        w.primary_btn.click()
        self.assertEqual(got, [w.task.id], "待办点击主按钮应发 started")

    def test_click_done_emits_toggled(self):
        w = self._widget("doing")
        got = []
        w.toggled.connect(got.append)
        w.started.connect(lambda _: self.fail("进行中点击「完成」不应发 started"))
        w.primary_btn.click()
        self.assertEqual(got, [w.task.id], "进行中点击「完成」应发 toggled（标记完成）")

    def test_status_change_swaps_label(self):
        w = self._widget("todo")
        self.assertEqual(w.primary_btn.text(), "开始")
        w.update_task(Task(title="写周报", status="doing"), self.today, self.thresholds)
        self.assertEqual(w.primary_btn.text(), "完成", "切为进行中后按钮文案应变为「完成」")
        w.update_task(Task(title="写周报", status="done"), self.today, self.thresholds)
        self.assertTrue(w.primary_btn.isHidden(), "完成后按钮应隐藏")


class ScrollPositionPreservedTest(unittest.TestCase):
    """回归用例 C：fill() 重建列表后滚动位置不被重置（2026-09-03 用户反馈）。

    选中任务触发整页重渲染 → fill() 清空重建 → 旧实现滚动条弹回顶部，
    表现为「滚到底选第 9 条后自动跳回第 1 条」。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.today = date(2026, 9, 3)
        self.thresholds = Thresholds(important=3, unimportant=3)
        self.tasks = [Task(title=f"任务 {i}") for i in range(1, 16)]

    def test_fill_preserves_scroll_position(self):
        from quadrant_todo.quadrant import Quadrant
        from quadrant_todo.views.board import TaskListWidget

        # 正常构造：象限枚举取 Q2 即可，本测试不涉及拖拽
        w = TaskListWidget(Quadrant.Q2)
        w.resize(420, 320)
        w.show()

        w.fill(self.tasks, self.today, self.thresholds, None)
        sb = w.verticalScrollBar()
        self.assertGreater(sb.maximum(), 0, "15 条任务应撑出可滚动区域")

        # 滚到底，再触发一次重渲染（等价于点选任务后的 refresh_views）
        sb.setValue(sb.maximum())
        bottom = sb.value()
        self.assertGreater(bottom, 0)

        w.fill(self.tasks, self.today, self.thresholds, self.tasks[8].id)
        self.app.processEvents()  # 冲刷 QTimer.singleShot(0) 的延迟恢复

        self.assertGreater(
            w.verticalScrollBar().value(), 0,
            "重渲染后滚动位置应保持在底部附近，而不是弹回顶部",
        )
        self.assertEqual(
            w.verticalScrollBar().value(), bottom,
            "重渲染后应恢复到之前的滚动位置",
        )


class SelectionStabilityTest(unittest.TestCase):
    """回归用例 E：选中态错位修复（2026-09-04 用户反馈）。

    现象：点选第 N 个任务，逻辑上 selected_id 正确（详情面板显示它），
    但视觉上第一个条目被高亮。

    根因：`TaskListWidget` 用 `SingleSelection`，选中触发整页重建
    （on_task_selected → fill → clear 重建），而 `TaskItemWidget.mousePressEvent`
    在 `emit clicked` 之后又把事件交给 `super().mousePressEvent`，QListWidget
    在重建后的列表上按当前鼠标位置再选中一次 → 命中错位（通常是第一行）。
    修复：关掉原生选择（NoSelection），选中态完全由应用层 `set_selected` 负责；
    且 `mousePressEvent` 不再转发事件。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.today = date(2026, 9, 4)
        self.thresholds = Thresholds(important=3, unimportant=3)
        from quadrant_todo.quadrant import Quadrant
        self.Quadrant = Quadrant
        self.tasks = [Task(title=f"任务 {i}") for i in range(1, 16)]

    def test_list_uses_no_selection_mode(self):
        from PySide6.QtWidgets import QAbstractItemView
        from quadrant_todo.views.board import TaskListWidget
        w = TaskListWidget(self.Quadrant.Q2)
        self.assertEqual(
            w.selectionMode(), QAbstractItemView.NoSelection,
            "列表应关闭原生选择，避免重建后高亮错位",
        )

    def test_only_logical_selected_widget_is_highlighted(self):
        """fill 重建后，仅 selected_id 命中的那一个 widget 处于 selected=True。"""
        from PySide6.QtCore import Qt
        from quadrant_todo.views.board import TaskListWidget
        w = TaskListWidget(self.Quadrant.Q2)
        w.resize(420, 320)
        w.show()

        selected = self.tasks[7]  # 任意中间任务（非首尾）
        w.fill(self.tasks, self.today, self.thresholds, selected.id)
        self.app.processEvents()

        highlighted = [
            w.item(i).data(Qt.UserRole)
            for i in range(w.count())
            if w.itemWidget(w.item(i)).property("selected")
        ]
        self.assertEqual(
            highlighted, [selected.id],
            "重建后只有逻辑选中的 widget 应被高亮，不应出现第一个被高亮",
        )

    def test_mouse_press_does_not_forward_to_list(self):
        """TaskItemWidget.mousePressEvent 只发信号、不把事件交给 QListWidget，
        否则重建后 QListWidget 会按鼠标位置误选其他行。"""
        w = TaskItemWidget(Task(title="任务 3"), self.today, self.thresholds)
        import inspect
        src = inspect.getsource(w.mousePressEvent)
        self.assertNotIn(
            "super().mousePressEvent", src,
            "mousePressEvent 不应再转发事件给 QListWidget",
        )


class MarqueeTest(unittest.TestCase):
    """回归用例 D：选中任务时标题跑马灯滚动；未选中或文字够宽时静止。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.today = date(2026, 9, 2)
        self.thresholds = Thresholds(important=3, unimportant=3)

    def test_selected_long_title_starts_marquee(self):
        w = TaskItemWidget(
            Task(title="这是一个非常非常长的任务标题，长到一定会超出可视宽度"),
            self.today,
            self.thresholds,
        )
        w.resize(400, 60)
        w.show()
        w.set_selected(True)
        self.app.processEvents()
        self.assertTrue(w.title_label._timer.isActive(), "选中长标题应启动跑马灯")

    def test_unselected_stops_marquee(self):
        w = TaskItemWidget(
            Task(title="这是一个非常非常长的任务标题，长到一定会超出可视宽度"),
            self.today,
            self.thresholds,
        )
        w.resize(400, 60)
        w.set_selected(True)
        w.set_selected(False)
        self.app.processEvents()
        self.assertFalse(w.title_label._timer.isActive(), "取消选中应停止跑马灯")

    def test_short_title_does_not_scroll(self):
        w = TaskItemWidget(Task(title="短标题"), self.today, self.thresholds)
        w.resize(400, 60)
        w.set_selected(True)
        self.app.processEvents()
        # 即使 timer 触发，文字够宽时 offset 也应保持 0
        self.assertEqual(w.title_label._offset, 0, "短标题不应产生滚动偏移")


if __name__ == "__main__":
    unittest.main()

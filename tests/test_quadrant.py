"""quadrant.py 单元测试。

对应 PRD 第九章验收标准 9.1（象限自动计算）与 9.2（自动迁移），
以及 F4.2 日待办生成规则。

本测试不依赖 Qt 与数据库，可独立运行：
    python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from quadrant_todo.quadrant import (
    ACTIVE_STATUSES,
    DEFAULT_URGENCY_THRESHOLD,
    Quadrant,
    Thresholds,
    compute_quadrant,
    compute_urgency,
    date_status,
    days_until_due,
    format_date_label,
    format_estimate,
    is_in_daily_todo,
    is_overdue,
    resolve_quadrant,
    suggested_quadrant,
)

TODAY = date(2026, 8, 29)


def d(offset: int) -> date:
    """相对 TODAY 偏移 offset 天的日期。"""
    return TODAY + timedelta(days=offset)


class TestComputeUrgency(unittest.TestCase):
    """PRD 3.3 紧急性判定。"""

    def test_no_due_date_is_not_urgent(self):
        self.assertFalse(compute_urgency(None, TODAY))

    def test_overdue_is_always_urgent(self):
        for offset in (-1, -3, -30):
            self.assertTrue(compute_urgency(d(offset), TODAY), f"offset={offset} 应判定紧急")

    def test_within_threshold_is_urgent(self):
        # 默认阈值 2：今天、明天、后天均紧急
        for offset in (0, 1, 2):
            self.assertTrue(compute_urgency(d(offset), TODAY, 2), f"offset={offset} 应判定紧急")

    def test_beyond_threshold_is_not_urgent(self):
        # 默认阈值 2：大后天起不紧急（PRD 9.1）
        for offset in (3, 7, 30):
            self.assertFalse(compute_urgency(d(offset), TODAY, 2), f"offset={offset} 应判定不紧急")

    def test_threshold_change_recomputes(self):
        """PRD 9.1：阈值改为 7 天后，任务象限正确重算。"""
        five_days_later = d(5)
        self.assertFalse(compute_urgency(five_days_later, TODAY, 2))
        self.assertTrue(compute_urgency(five_days_later, TODAY, 7))

    def test_all_threshold_options(self):
        for threshold in (1, 2, 3, 7):
            self.assertTrue(compute_urgency(d(threshold), TODAY, threshold))
            self.assertFalse(compute_urgency(d(threshold + 1), TODAY, threshold))


class TestComputeQuadrant(unittest.TestCase):
    """PRD 3.3 象限映射表。"""

    def test_mapping_table(self):
        cases = [
            (True, True, Quadrant.Q1),
            (True, False, Quadrant.Q2),
            (False, True, Quadrant.Q3),
            (False, False, Quadrant.Q4),
        ]
        for importance, urgency, expected in cases:
            with self.subTest(importance=importance, urgency=urgency):
                self.assertEqual(compute_quadrant(importance, urgency), expected)


class TestResolveQuadrant(unittest.TestCase):
    """PRD 9.1 端到端象限判定 + 3.3 锁定优先级。"""

    def test_important_due_today_is_q1(self):
        self.assertEqual(resolve_quadrant(True, d(0), None, TODAY), Quadrant.Q1)

    def test_important_due_in_7_days_is_q2(self):
        self.assertEqual(resolve_quadrant(True, d(7), None, TODAY), Quadrant.Q2)

    def test_unimportant_due_tomorrow_is_q3(self):
        self.assertEqual(resolve_quadrant(False, d(1), None, TODAY), Quadrant.Q3)

    def test_unimportant_no_due_date_is_q4(self):
        self.assertEqual(resolve_quadrant(False, None, None, TODAY), Quadrant.Q4)

    def test_overdue_important_stays_q1(self):
        self.assertEqual(resolve_quadrant(True, d(-3), None, TODAY), Quadrant.Q1)

    def test_locked_quadrant_wins(self):
        """PRD 3.3：锁定优先级高于自动计算。"""
        self.assertEqual(resolve_quadrant(False, d(-10), "Q2", TODAY), Quadrant.Q2)
        self.assertEqual(resolve_quadrant(True, d(0), Quadrant.Q4, TODAY), Quadrant.Q4)

    def test_locked_accepts_enum_and_string(self):
        self.assertEqual(resolve_quadrant(True, d(0), Quadrant.Q2, TODAY), Quadrant.Q2)
        self.assertEqual(resolve_quadrant(True, d(0), "Q2", TODAY), Quadrant.Q2)


class TestSuggestedQuadrant(unittest.TestCase):
    """PRD F3.4 「建议移至 Qx」徽标。"""

    def test_suggestion_ignores_lock(self):
        # 锁定在 Q2，但已逾期 → 建议 Q1
        self.assertEqual(suggested_quadrant(True, d(-1), TODAY), Quadrant.Q1)

    def test_suggestion_can_point_backwards(self):
        # 锁定在 Q1，但截止日期尚远 → 建议 Q2（不只是单向建议 Q1）
        self.assertEqual(suggested_quadrant(True, d(30), TODAY), Quadrant.Q2)

    def test_suggestion_matches_resolve_when_unlocked(self):
        for importance in (True, False):
            for offset in (None, -5, 0, 1, 5):
                due = None if offset is None else d(offset)
                self.assertEqual(
                    suggested_quadrant(importance, due, TODAY),
                    resolve_quadrant(importance, due, None, TODAY),
                )


class TestAutoMigration(unittest.TestCase):
    """PRD 9.2 自动迁移：时间推进导致象限变化。"""

    def test_q2_migrates_to_q1_as_deadline_approaches(self):
        """重要任务：D-5 在 Q2，时间推进到阈值内后落到 Q1。"""
        task_due = date(2026, 9, 3)

        day_one = date(2026, 8, 29)   # 距截止 5 天，阈值外
        day_later = date(2026, 9, 1)  # 距截止 2 天，进入阈值

        self.assertEqual(resolve_quadrant(True, task_due, None, day_one, 2), Quadrant.Q2)
        self.assertEqual(resolve_quadrant(True, task_due, None, day_later, 2), Quadrant.Q1)

    def test_overdue_task_keeps_urgent(self):
        task_due = d(0)
        for offset in (1, 3, 10):
            self.assertEqual(
                resolve_quadrant(True, task_due, None, TODAY + timedelta(days=offset), 2),
                Quadrant.Q1,
            )

    def test_unlocked_task_follows_rule_after_unlock(self):
        """PRD 9.2：解锁后立即按规则重算。"""
        locked = resolve_quadrant(True, d(-1), "Q2", TODAY)
        unlocked = resolve_quadrant(True, d(-1), None, TODAY)
        self.assertEqual(locked, Quadrant.Q2)
        self.assertEqual(unlocked, Quadrant.Q1)


class TestDualThresholds(unittest.TestCase):
    """PRD F6.3「每象限阈值可设」：重要/不重要任务各自独立的紧急窗口。

    重要任务的紧急窗口控制 Q1↔Q2，不重要任务的紧急窗口控制 Q3↔Q4。
    """

    def test_important_window_controls_q1_q2(self):
        due_in_5 = d(5)
        # 重要窗口=2：5 天不算紧急 → Q2
        self.assertEqual(
            resolve_quadrant(True, due_in_5, None, TODAY, Thresholds(important=2, unimportant=2)),
            Quadrant.Q2,
        )
        # 重要窗口=7：5 天算紧急 → Q1
        self.assertEqual(
            resolve_quadrant(True, due_in_5, None, TODAY, Thresholds(important=7, unimportant=2)),
            Quadrant.Q1,
        )

    def test_unimportant_window_controls_q3_q4(self):
        due_in_5 = d(5)
        # 不重要窗口=2：不紧急 → Q4
        self.assertEqual(
            resolve_quadrant(False, due_in_5, None, TODAY, Thresholds(important=2, unimportant=2)),
            Quadrant.Q4,
        )
        # 不重要窗口=7：紧急 → Q3
        self.assertEqual(
            resolve_quadrant(False, due_in_5, None, TODAY, Thresholds(important=2, unimportant=7)),
            Quadrant.Q3,
        )

    def test_windows_independent(self):
        """两条窗口互不影响：只调重要窗口不应改变不重要任务的归属。"""
        due_in_5 = d(5)
        base = Thresholds(important=2, unimportant=7)
        self.assertEqual(
            resolve_quadrant(False, due_in_5, None, TODAY, base), Quadrant.Q3
        )
        # 把重要窗口放宽到 7，不重要任务仍应留在 Q3
        self.assertEqual(
            resolve_quadrant(False, due_in_5, None, TODAY, Thresholds(important=7, unimportant=7)),
            Quadrant.Q3,
        )

    def test_default_preserves_legacy_behavior(self):
        """默认 Thresholds()（重要=不重要=2）应与旧单一阈值 2 行为一致。"""
        for importance in (True, False):
            for offset in (1, 2, 3):
                due = d(offset)
                self.assertEqual(
                    resolve_quadrant(importance, due, None, TODAY, Thresholds()),
                    resolve_quadrant(importance, due, None, TODAY, 2),
                )


class TestDailyTodo(unittest.TestCase):
    """PRD F4.2 日待办生成规则。"""

    def test_due_today_is_included(self):
        self.assertTrue(is_in_daily_todo("todo", d(0), False, TODAY))

    def test_overdue_stays_included(self):
        self.assertTrue(is_in_daily_todo("todo", d(-3), False, TODAY))

    def test_future_task_excluded(self):
        self.assertFalse(is_in_daily_todo("todo", d(3), False, TODAY))

    def test_no_due_date_excluded_unless_flagged(self):
        self.assertFalse(is_in_daily_todo("todo", None, False, TODAY))
        self.assertTrue(is_in_daily_todo("todo", None, True, TODAY))

    def test_closed_status_excluded(self):
        for status in ("done", "abandoned"):
            self.assertFalse(is_in_daily_todo(status, d(-5), True, TODAY))

    def test_doing_status_included(self):
        self.assertTrue(is_in_daily_todo("doing", d(0), False, TODAY))


class TestDateHelpers(unittest.TestCase):
    """PRD 3.2 派生字段与 6.4 展示。"""

    def test_days_until_due(self):
        self.assertIsNone(days_until_due(None, TODAY))
        self.assertEqual(days_until_due(d(0), TODAY), 0)
        self.assertEqual(days_until_due(d(3), TODAY), 3)
        self.assertEqual(days_until_due(d(-2), TODAY), -2)

    def test_is_overdue(self):
        self.assertFalse(is_overdue(None, TODAY))
        self.assertFalse(is_overdue(d(0), TODAY))
        self.assertTrue(is_overdue(d(-1), TODAY))

    def test_date_status(self):
        self.assertEqual(date_status(None, TODAY), "none")
        self.assertEqual(date_status(d(-1), TODAY), "overdue")
        self.assertEqual(date_status(d(0), TODAY), "near")
        self.assertEqual(date_status(d(2), TODAY), "near")
        self.assertEqual(date_status(d(3), TODAY), "normal")

    def test_date_label(self):
        self.assertEqual(format_date_label(None, TODAY), "")
        self.assertEqual(format_date_label(d(0), TODAY), "今天到期")
        self.assertEqual(format_date_label(d(2), TODAY), "还剩 2 天")
        self.assertEqual(format_date_label(d(-3), TODAY), "逾期 3 天")

    def test_format_estimate(self):
        self.assertEqual(format_estimate(None), "")
        self.assertEqual(format_estimate(0), "")
        self.assertEqual(format_estimate(30), "30m")
        self.assertEqual(format_estimate(60), "1h")
        self.assertEqual(format_estimate(120), "2h")
        self.assertEqual(format_estimate(90), "1.5h")


class TestConstants(unittest.TestCase):
    def test_default_threshold(self):
        self.assertEqual(DEFAULT_URGENCY_THRESHOLD, 2)

    def test_active_statuses(self):
        self.assertEqual(ACTIVE_STATUSES, frozenset({"todo", "doing"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)

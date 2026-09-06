"""V2.0 模块冒烟（offscreen）：周/月/年待办、周期任务、日历视图、提醒联动、深浅色主题。

验证：模块可 import、视图可渲染、周期重生联动调度、提醒联动解析、主题切换不崩。
不依赖真实显示器。
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import quadrant_todo.config as cfg
from quadrant_todo import theme
from quadrant_todo.db import Database
from quadrant_todo.app import MainWindow
from quadrant_todo.models import Task, TaskCompletion
from quadrant_todo.recurrence import (
    compute_relative_reminder,
    natural_to_rrule,
    next_boundary,
    period_range,
    resolve_reminder,
)


def main() -> int:
    tmp_db = Path(tempfile.mkdtemp()) / "smoke_v2.db"
    db = Database(tmp_db)
    db.connect()

    app = QApplication(sys.argv)
    window = MainWindow(db)
    window.show()

    today = date.today()
    app_today = window.today

    # ---- 1) 主题切换（F24）----
    theme.apply_theme(cfg.THEME_DARK)
    assert theme.is_dark(), "应用深色主题后 is_dark 应为 True"
    theme.apply_theme(cfg.THEME_LIGHT)
    assert not theme.is_dark(), "应用浅色主题后 is_dark 应为 False"
    theme.apply_theme(cfg.THEME_SYSTEM)

    # ---- 2) 周期视图渲染（F15）----
    for kind in ("week", "month", "year"):
        window.switch_period(kind)
        assert window.current_view == "period"
        assert window.current_period_kind == kind
    # 上一期 / 下一期不崩
    window._step_period(1)
    window._step_period(-1)

    # ---- 3) 日历视图渲染 + 月份切换（F17，修复切换无反应）----
    window.switch_view("calendar")
    assert window.current_view == "calendar"
    before = window.calendar_view.label.text()
    assert before, "日历初始应有月份标签"
    # 点击下月：_step_month 更新 anchor 并发射 anchor_changed → app 重渲染
    window.calendar_view._step_month(1)
    after = window.calendar_view.label.text()
    assert before != after, "点击下月后月份标签应变化（修复前不动）"
    expected_next = (date.today().month % 12) + 1
    assert window.calendar_view.anchor.month == expected_next, "anchor 应顺延到下月"
    # 再点上月应回到原月份
    window.calendar_view._step_month(-1)
    assert window.calendar_view.label.text() == before, "再点上月应回到原月份标签"

    # ---- 4) 周期任务创建 + 重生联动调度（F16 / F16.3）----
    task = Task(title="每日锻炼", importance=True, due_date=today)
    task.cycle = "daily"
    task.last_reset = today
    task.reminder_rule = "relative"
    task.reminder_offset = 60
    task.reminder = resolve_reminder("relative", task.due_date, None, 60)
    db.save_task(task)
    window.reload()  # 让内存任务列表包含该任务，供后续 on_task_toggled / on_field_changed 定位

    # 完成该周期任务 → 应写入 task_completion
    window.on_task_toggled(task.id)
    done = db.get_task(task.id)
    assert done.status == "done", "勾选后应为 done"
    assert db.count_completions(task.id) == 1, "完成一次应写入 1 条完成历史"
    recs = db.get_completions_in_range(today - timedelta(days=1), today + timedelta(days=1))
    assert any(r["task_id"] == task.id for r in recs), "完成历史应在时段范围内可查"

    # 跨过边界 → 调度重生
    tomorrow = today + timedelta(days=1)
    assert tomorrow >= next_boundary(task.last_reset, "daily"), "次日应越过日边界"
    reset_n = db.execute_cycle_resets(tomorrow)
    assert reset_n == 1, f"应在跨日后重生 1 条周期任务，实际 {reset_n}"
    revived = db.get_task(task.id)
    assert revived.status == "todo", "重生后应回到 todo"
    assert revived.completed_at is None, "重生后 completed_at 应清空"
    assert revived.due_date == task.due_date + timedelta(days=1), "重生应顺延截止日期一天"
    assert revived.last_reset == tomorrow, "重生后 last_reset 应为当天"

    # ---- 5) 提醒联动（F26）----
    # none
    assert resolve_reminder("none", today, None, None) is None
    # manual 取绝对值
    manual_dt = datetime(today.year, today.month, today.day, 9, 0)
    assert resolve_reminder("manual", today, manual_dt, None) == manual_dt
    # relative 推导
    rel = compute_relative_reminder(today, 60)
    assert rel == datetime(today.year, today.month, today.day, 0, 0) - timedelta(minutes=60)
    # 改标题里的周期规则 → 解析
    assert natural_to_rrule("每工作日") == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert natural_to_rrule("每月15日") == "FREQ=MONTHLY;BYMONTHDAY=15"

    # ---- 6) 详情面板周期/提醒联动字段（F16/F26）----
    window.on_task_selected(task.id)
    assert window.detail.task is not None
    window.on_field_changed(task.id, "reminder_rule", "relative")
    window.on_field_changed(task.id, "reminder_offset", 30)
    updated = db.get_task(task.id)
    assert updated.reminder_rule == "relative"
    assert updated.reminder_offset == 30
    assert updated.reminder is not None, "relative + 有截止日期应算出提醒时间"

    # ---- 7) 周期视图「已完成 (n)」读完成历史不崩 ----
    window.switch_period("month")
    window._render_period()

    # ---- 8) 设置对话框可构造（F6.3 双阈值 / F26.3 默认提醒；已移除主题入口）----
    from quadrant_todo.views.settings import SettingsDialog

    dlg = SettingsDialog(
        window.thresholds.important, window.thresholds.unimportant,
        db.get_setting(cfg.KEY_DEFAULT_REMINDER_RULE, "none"),
        db.get_int_setting(cfg.KEY_DEFAULT_REMINDER_OFFSET, 60),
        db.get_int_setting(cfg.KEY_POMODORO_DURATION, 25),
    )
    assert dlg is not None
    assert not hasattr(dlg, "theme_combo"), "设置中的主题入口应已移除"

    # ---- 9) 时段范围纯粹逻辑（F15.2，开区间）----
    ws, we = period_range("week", today)
    assert (we - ws).days == 7 and ws.weekday() == 0

    db.close()
    print("SMOKE V2 OK: 周期视图/日历/周期重生/提醒联动/设置（无主题入口） 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

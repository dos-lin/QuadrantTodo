"""无显示器环境下的 UI 冒烟测试（offscreen 平台）。

验证：模块可 import、MainWindow 可构造、核心交互不崩。
不依赖真实显示器，仅验证组装正确性。
"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quadrant_todo.db import Database
from quadrant_todo.app import MainWindow
from quadrant_todo.models import Task


def main() -> int:
    tmp_db = Path(tempfile.mkdtemp()) / "smoke.db"
    db = Database(tmp_db)
    db.connect()

    app = QApplication(sys.argv)
    window = MainWindow(db)
    window.show()

    # 1) 初始渲染（空数据）
    assert len(db.all_tasks()) == 0, "空数据库应无任务"

    # 2) 从象限入口创建任务（F2.3：不锁定）
    window.on_task_created("重要且紧急示例", "Q1")
    window.on_task_created("重要不紧急示例", "Q2")
    created = db.all_tasks()
    assert len(created) == 2, f"应创建 2 条任务，实际 {len(created)}"
    for task in created:
        assert task.locked_quadrant is None, "从象限入口创建不应锁定"

    # 3) 切换视图不崩
    window.switch_view("daily")
    window.switch_view("unscheduled")
    window.switch_view("board")

    # 4) 勾选完成（F5.6）
    first = created[0]
    window.on_task_toggled(first.id)
    assert db.get_task(first.id).status == "done", "勾选后应为 done"

    # 5) 取消完成（F4.10）
    window.on_task_toggled(first.id)
    assert db.get_task(first.id).status == "todo", "取消后应为 todo"

    # 6) F2.3 修复的核心：入口创建只定「重不重要」，象限由系统算，不锁定
    from quadrant_todo.quadrant import Quadrant

    target = created[1]  # 从 Q2 入口创建：重要 + 无截止日期
    assert target.quadrant(date.today(), window.thresholds) == Quadrant.Q2, "应落在 Q2"
    assert target.locked_quadrant is None, "入口创建不应锁定"
    assert target.due_date is None, "入口创建不应设备截止日期"

    # 7) 统计更新
    window._update_stats()

    # 8) 详情面板渲染
    window.on_task_selected(target.id)
    assert window.detail.task is not None

    # 9) 「已完成 (n)」「已放弃 (n)」折叠区均已移除（2026-09-01 用户决策）：
    #    四象限中任务完成/取消后立即消失，不保留可见入口。需要回看走任务搜索/数据导出。
    for panel in window.board_view.panels.values():
        assert not hasattr(panel, "done_group"), (
            f"QuadrantPanel 已移除 done_group，但 {panel.quadrant} 仍存在"
        )
        assert not hasattr(panel, "abandoned_group"), (
            f"QuadrantPanel 已移除 abandoned_group，但 {panel.quadrant} 仍存在"
        )

    # 10) 四象限滚动条恒为「按需显示」（2026-09-03 用户反馈）：
    #     内容超出象限可视区即出现滚动条，不再按任务条数开关（少数高任务项也会溢出）。
    from PySide6.QtCore import Qt as _Qt

    for panel in window.board_view.panels.values():
        assert panel.list_widget.verticalScrollBarPolicy() == _Qt.ScrollBarAsNeeded, (
            f"{panel.quadrant} 列表滚动条应为 ScrollBarAsNeeded"
        )

    db.close()
    print("SMOKE OK: 8 项核心交互通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

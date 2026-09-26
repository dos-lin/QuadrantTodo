"""回归：重绘详情面板（含子任务行）时不得闪出提升为顶层的孤儿窗口。

背景（用户反馈 2026-09-19）：clear_layout 对已显示控件先 setParent(None) 再
deleteLater，Qt 会把控件提升为顶层窗口并保持可见，直到 deleteLater 生效，
表现为选中带子任务的待办时闪出一个小窗并抢焦点。修法：先 setVisible(False)。
"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from quadrant_todo.db import Database
from quadrant_todo.app import MainWindow
from quadrant_todo.models import Subtask


def _visible_windows(app: QApplication) -> list:
    return [w for w in app.topLevelWidgets() if w.isWindow() and w.isVisible()]


def test_no_orphan_window_after_detail_rerender() -> None:
    tmp_db = Path(tempfile.mkdtemp()) / "clear_layout.db"
    db = Database(tmp_db)
    db.connect()

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(db)
    window.show()
    app.processEvents()

    window.on_task_created("主任务", "Q1")
    task = db.all_tasks()[0]
    db.save_subtask(Subtask(parent_id=task.id, title="子任务A", sort_order=0))
    window.refresh_views()
    app.processEvents()

    before = set(id(w) for w in _visible_windows(app))

    # 选中 → 详情面板重绘（子任务行被 clear_layout 摘除后重建）
    window.on_task_selected(task.id)
    window.on_task_selected(task.id)  # 二次重绘，确保旧行被摘除过
    app.processEvents()

    after = _visible_windows(app)
    orphans = [w for w in after if id(w) not in before]
    assert not orphans, f"重绘后闪出孤儿顶层窗口: {orphans}"

    # 子任务行本身仍应正常渲染
    assert window.detail.subtask_list.count() == 1

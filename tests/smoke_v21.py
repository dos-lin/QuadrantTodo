"""V2.1 模块冒烟（offscreen）：子任务、标签、搜索、热力图、便签、番茄钟、全局热键。

验证：schema v3 迁移、7 个模块的数据层与视图渲染、视图切换、番茄钟写入、
便签浮层、热键解析。不依赖真实显示器，也不注册真实系统热键。
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quadrant_todo import config, migration
from quadrant_todo.db import Database
from quadrant_todo.app import MainWindow
from quadrant_todo.hotkey import parse_hotkey
from quadrant_todo.models import (
    PomodoroSession,
    StickyNote,
    Subtask,
    Task,
    TaskCompletion,
)


def main() -> int:
    tmp_db = Path(tempfile.mkdtemp()) / "smoke_v21.db"
    db = Database(tmp_db)
    db.connect()

    # ---- 0) schema 已升到 v3，五张新表齐备 ----
    assert migration.current_version(db.conn) == 3, "schema 应为 v3"
    tables = {
        r[0]
        for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for name in ("subtask", "tag", "task_tag", "pomodoro_session", "sticky_note"):
        assert name in tables, f"缺少表 {name}"

    app = QApplication(sys.argv)
    window = MainWindow(db)
    window.show()
    today = window.today

    # ---- 默认全屏：全新库（无 window_state 记录）打开应最大化 ----
    assert db.get_setting(config.KEY_WINDOW_STATE) is None, "全新库不应有窗口状态记录"
    assert window.isMaximized(), "双击 exe 首次打开应默认全屏（最大化）"

    # ---- 1) F20 子任务 ----
    parent = Task(title="写季度复盘", importance=True, due_date=today + timedelta(days=3))
    parent.sort_order = db.next_sort_order()
    db.save_task(parent)
    db.save_subtask(Subtask(parent_id=parent.id, title="拉数据"))
    db.save_subtask(Subtask(parent_id=parent.id, title="写初稿"))
    subs = db.get_subtasks(parent.id)
    assert len(subs) == 2, f"子任务应有 2 条，实际 {len(subs)}"

    window.on_subtask_toggle(parent.id, subs[0].id, True)
    assert db.get_subtasks(parent.id)[0].done, "勾选后子任务应为完成"
    # F20.5 红线：子任务全完成不自动完成父任务
    window.reload()
    assert window._find(parent.id).status != "done", "子任务完成不得自动完成父任务"
    # F20 红线：子任务不影响象限
    assert parent.quadrant(today, window.thresholds) == window._find(parent.id).quadrant(
        today, window.thresholds
    ), "子任务不得影响象限判定"

    # 详情面板渲染子任务
    window.selected_id = parent.id
    window.reload()
    assert window.detail.subtask_progress.text() == "1/2", (
        f"子任务进度应为 1/2，实际 {window.detail.subtask_progress.text()!r}"
    )
    # 右侧详情面板可滚动、备注输入框高度足够（解决输入后内容被裁剪）
    assert window.detail.scroll.isVisible(), "详情面板滚动区应可见"
    assert window.detail.note_edit.minimumHeight() >= 120, "备注输入框最小高度应 >= 120"

    # 提醒默认时间应显示为当天（而非 1752 年最小日期）
    from PySide6.QtCore import QDate

    window.detail.show_task(parent, today, window.thresholds)
    assert window.detail.reminder_edit.dateTime().date() == QDate.currentDate(), (
        f"默认提醒日期应为今天，实际 {window.detail.reminder_edit.dateTime().date().toString()}"
    )
    # 切到「手动（绝对值）」提醒时，默认也应是当天
    window.detail.reminder_rule_combo.blockSignals(True)
    window.detail.reminder_rule_combo.setCurrentIndex(
        window.detail.reminder_rule_combo.findData("manual")
    )
    window.detail.reminder_rule_combo.blockSignals(False)
    window.detail.reminder_edit.blockSignals(True)
    window.detail._load_reminder_rule(parent)
    window.detail.reminder_edit.blockSignals(False)
    assert window.detail.reminder_edit.dateTime().date() == QDate.currentDate(), (
        "切到手动提醒后默认日期仍应为今天"
    )

    # ---- 2) F21 标签 ----
    tag = db.create_tag("重要客户")
    assert tag is not None, "新建标签应成功"
    assert db.create_tag("重要客户") is None, "重名标签应返回 None（唯一约束）"
    window.on_tag_add_requested(parent.id, "重要客户")
    assert db.get_task_tag_ids(parent.id) == [tag.id], "标签应挂到任务上"
    # 复用已存在标签，不应重复创建
    window.on_tag_add_requested(parent.id, "重要客户")
    assert len(db.get_tags()) == 1, "同名标签应复用，不新建"

    window.active_tag_ids = {tag.id}
    window.reload()
    hits = db.tasks_with_all_tags([tag.id])
    assert parent.id in hits, "标签筛选应命中该任务"

    # ---- 3) F22 搜索 ----
    other = Task(title="买牛奶", note="超市")
    other.sort_order = db.next_sort_order()
    db.save_task(other)
    assert len(db.search_tasks("牛奶")) == 1, "标题模糊搜索应命中"
    assert len(db.search_tasks("超市")) == 1, "备注模糊搜索应命中"
    # F22.4 默认不含已完成 / 已放弃
    other.status = "done"
    db.save_task(other)
    assert len(db.search_tasks("牛奶")) == 0, "默认应排除已完成任务"
    assert len(db.search_tasks("牛奶", include_closed=True)) == 1, "开启后应包含已完成"
    other.status = "todo"
    db.save_task(other)

    # 搜索同时覆盖小便签：保存一条含「牛奶」的便签
    sticky = StickyNote(content="记得买牛奶粉")
    db.save_sticky(sticky)
    assert len(db.search_stickies("牛奶")) == 1, "便签内容模糊搜索应命中"

    window.search_edit.setText("牛奶")
    window._run_search()
    assert window.current_view == "search", "搜索后应切到搜索视图"
    assert "任务 1 条" in window.search_view.label.text(), (
        f"搜索计数不对：{window.search_view.label.text()}"
    )
    # 搜索结果页应出现便签命中区
    assert window.search_view.sticky_scroll.isVisibleTo(window.search_view), (
        "搜索结果应包含便签命中区"
    )
    assert "1 条匹配" in window.search_view.sticky_header.text(), (
        f"便签命中计数不对：{window.search_view.sticky_header.text()}"
    )
    # 点击便签命中卡片 → 跳到便签页并按关键词过滤
    window.search_view.sticky_area.itemAt(0).widget().click()
    assert window.current_view == "sticky", "点击便签命中卡片应跳到便签页"
    assert window.sticky_filter_keyword == "牛奶", "便签页应保留搜索词过滤"
    assert "匹配 1 条" in window.sticky_view.title.text(), (
        f"便签页标题应显示过滤计数：{window.sticky_view.title.text()}"
    )
    # 清除筛选 → 恢复显示全部便签
    window.sticky_view.clear_filter_btn.click()
    assert window.sticky_filter_keyword == "", "清除筛选应重置过滤词"
    assert len(window.sticky_view._notes) == 1, "清除筛选后应显示全部便签"
    # 在便签页清空搜索：不跳走，只重置过滤词
    window._clear_search()
    assert window.current_view == "sticky", "在便签页清空搜索不应跳走"
    assert window.sticky_filter_keyword == "", "清空搜索应重置便签过滤词"
    # 在搜索结果页清空搜索 → 回看板（F22.5）
    window.search_edit.setText("牛奶")
    window._run_search()
    assert window.current_view == "search", "重新搜索应切到搜索视图"
    window._clear_search()
    assert window.current_view == "board", "清空搜索应回到看板"

    # ---- 4) F18 热力图 ----
    db.record_completion(TaskCompletion(task_id=parent.id, completed_at=datetime.now()))
    counts = db.daily_completion_counts(today.year)
    assert counts.get(today, 0) >= 1, "今日完成数应 >= 1"
    summary = db.heatmap_summary(today.year, today)
    assert summary["total"] >= 1, "年度累计应 >= 1"
    assert summary["streak"] >= 1, "今日有完成，streak 应 >= 1"
    window.switch_view("heatmap")
    window.reload()
    # 年份应显示在「上一年 / 下一年」导航按钮之间
    assert window.heatmap_view.year_label.text() == str(today.year), (
        f"热力图年份标签应为 {today.year}，实际 {window.heatmap_view.year_label.text()!r}"
    )
    # 12 个月横向均分：网格行内应恰好 12 列（无尾部 stretch 占位），且每列 stretch=1
    grid = window.heatmap_view.grid_layout.itemAt(0).layout()
    assert grid.count() == 12, f"热力图应有 12 个月列，实际 {grid.count()}"
    assert all(grid.stretch(i) == 1 for i in range(12)), "12 个月应横向平均分配空间"

    # ---- 5) F19 小便签 ----
    window.on_sticky_add("记得喝水")
    notes = db.get_stickies()
    assert len(notes) == 2, "便签应已保存（含搜索用例中的牛奶便签）"
    note = next(n for n in notes if n.content == "记得喝水")
    # 便签与任务隔离：不出现在任何任务视图
    assert note.id not in {t.id for t in db.all_tasks()}, "便签不得混入任务表"
    window.on_sticky_update(note.id, "pinned", True)
    assert len(db.get_pinned_stickies()) == 1, "应有一条常驻便签"
    assert note.id in window._sticky_floaters, "勾选常驻后应打开浮层"
    window._close_sticky_floater(note.id)
    assert note.id not in window._sticky_floaters, "关闭浮层后应移除"
    assert len(db.get_stickies()) == 2, "关闭浮层不得删除数据"
    window.switch_view("sticky")
    window.reload()

    # 新建便签对话框：多行输入，尺寸足够大（解决输入框太小问题）
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPlainTextEdit

    from quadrant_todo.views.sticky import StickyAddDialog

    dlg = StickyAddDialog()
    assert dlg.minimumWidth() >= 400 and dlg.minimumHeight() >= 240, "新建便签对话框应足够大"
    dlg.edit.setPlainText("多行\n便签内容")
    assert "多行" in dlg.text(), "对话框应能取回多行文本"
    # 右键便签不应弹出黑色菜单（Qt.NoContextMenu）
    assert dlg.edit.contextMenuPolicy() == Qt.NoContextMenu, (
        "新建对话框的输入框应禁用右键菜单"
    )
    dlg.deleteLater()

    # 便签列表页里的 _ContentEdit 编辑框也应禁用右键菜单
    edits = window.sticky_view.findChildren(QPlainTextEdit)
    assert edits, "便签页应能找到 _ContentEdit 编辑框"
    for ed in edits:
        assert ed.contextMenuPolicy() == Qt.NoContextMenu, (
            "便签内容编辑框应禁用右键菜单"
        )

    # 便签内容区高度固定 + 列表外层用 QScrollArea 滑动浏览 + 删除走二次确认
    from quadrant_todo.views.sticky import _STICKY_CONTENT_HEIGHT
    for ed in edits:
        assert ed.maximumHeight() == _STICKY_CONTENT_HEIGHT, (
            f"便签编辑框应固定高度 {_STICKY_CONTENT_HEIGHT}，实测 {ed.maximumHeight()}"
        )

    from PySide6.QtWidgets import QMessageBox, QScrollArea

    scroll_areas = window.sticky_view.findChildren(QScrollArea)
    assert scroll_areas, "便签页应有 QScrollArea 用于多便签下滑"
    # 多便签时让容器撑出可视区，验证滚动属性生效
    many = [note] + [
        StickyNote(content=f"便签 {i}", id=f"n{i}")
        for i in range(15)
    ]
    window.sticky_view.render(many)
    container_after = scroll_areas[0].widget()
    assert container_after.sizeHint().height() > scroll_areas[0].viewport().sizeHint().height(), (
        "便签多时应能撑出可视区，外层 QScrollArea 才能滑动"
    )

    # 恢复单便签用于删除二次确认测试
    window.sticky_view.render([note])

    # 删除走二次确认：monkey-patch QMessageBox.exec 避免 offscreen 阻塞
    from PySide6.QtWidgets import QPushButton

    real_exec = QMessageBox.exec
    try:
        # 1) 点击删除 → 应弹出警告框（验证弹窗行为）
        QMessageBox.exec = lambda self: QMessageBox.Yes
        received: list[str] = []
        window.sticky_view.delete_requested.connect(lambda nid: received.append(nid))
        del_btn = [b for b in window.sticky_view.findChildren(QPushButton) if b.text() == "删除"][0]
        del_btn.click()
        assert received == [note.id], (
            f"二次确认选「删除」时应触发一次 delete_requested，实际 {received}"
        )

        # 2) 选「取消」不应触发删除
        received.clear()
        QMessageBox.exec = lambda self: QMessageBox.No
        del_btn.click()
        assert not received, "二次确认选「取消」时不得触发 delete_requested"
    finally:
        QMessageBox.exec = real_exec

    # ---- 6) F23 番茄钟 ----
    before_status = window._find(parent.id).status
    db.save_pomodoro_session(
        PomodoroSession(
            task_id=parent.id,
            started_at=datetime.now() - timedelta(minutes=25),
            ended_at=datetime.now(),
            planned_min=25,
            actual_min=25,
            status="done",
        )
    )
    db.save_pomodoro_session(
        PomodoroSession(
            task_id=parent.id,
            started_at=datetime.now() - timedelta(minutes=10),
            ended_at=datetime.now(),
            planned_min=25,
            actual_min=10,
            status="aborted",
        )
    )
    stats = db.pomodoro_stats()
    assert stats["total_cnt"] == 2, f"应有 2 条会话，实际 {stats['total_cnt']}"
    assert stats["total_min"] == 35, f"累计专注应为 35 分钟，实际 {stats['total_min']}"
    # F23.4 红线：番茄钟不触碰任务状态机
    assert window._find(parent.id).status == before_status, "番茄钟不得改变任务状态"
    window.switch_view("stats")
    window.reload()
    assert "累计专注" in window.stats_view.summary.text(), "统计页应展示累计专注"

    # 中止写入 session：直接构造计时器后 abort（不启动事件循环）
    from quadrant_todo.pomodoro import PomodoroTimer

    timer = PomodoroTimer(parent.id, 25)
    finished = []
    timer.finished.connect(finished.append)
    timer.abort()
    assert len(finished) == 1, "中止应触发 finished 信号"
    assert finished[0].status == "aborted", "中止会话状态应为 aborted"

    # ---- 7) F25 全局热键 ----
    assert parse_hotkey("Ctrl+Alt+Q") == (0x0002 | 0x0001, ord("Q")), "热键解析错误"
    assert parse_hotkey("Ctrl+Alt+N") == (0x0002 | 0x0001, ord("N")), "热键解析错误"
    assert parse_hotkey("Q") is None, "无修饰键应视为非法"
    assert parse_hotkey("Ctrl+Alt+F1") is None, "功能键应视为非法"
    assert db.get_setting(config.KEY_GLOBAL_HOTKEY_ENABLED, "0") == "0", "热键默认应关闭"

    # 所有 v2.1 视图均可切换且不崩
    for name in ("tags", "sticky", "stats", "heatmap", "search"):
        window.switch_view(name)
        window.reload()
        assert window.current_view == name, f"切换到 {name} 失败"

    db.close()
    print("SMOKE V2.1 OK: 子任务 / 标签 / 搜索 / 热力图 / 便签 / 番茄钟 / 热键 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

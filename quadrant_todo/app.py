"""应用主窗口与业务组装（PRD F1–F14）。

职责边界：本模块只做「信号 → 业务规则 → 数据层 → 刷新视图」的编排，
所有象限判定一律调用 quadrant.py 的纯函数，不在此处重写规则。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

#: 接收键盘输入的控件类型——焦点落在其上时不拦截空格，保证可正常输入空格（修复 F14 缺陷）
_EDITABLE_TYPES = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)


def _is_editing_widget(widget) -> bool:
    if widget is None:
        return False
    if isinstance(widget, _EDITABLE_TYPES):
        return True
    if isinstance(widget, QComboBox) and widget.isEditable():
        return True
    return False

from . import backup
from . import config
from . import theme
from .db import Database, DatabaseError
from .due_parser import parse_due_from_title
from .models import (
    STATUS_ABANDONED,
    STATUS_DOING,
    STATUS_DONE,
    STATUS_TODO,
    Article,
    Subtask,
    StickyNote,
    Task,
    TaskCompletion,
)
from .pomodoro import PomodoroTimer
from .quadrant import QUADRANT_ORDER, Quadrant, Thresholds
from .recurrence import compute_relative_reminder, resolve_reminder
from .views.board import BoardView
from .views.calendar import CalendarView
from .views.daily import DailyView
from .views.detail import DetailPanel
from .views.heatmap import HeatmapView
from .views.period import PeriodView
from .views.quickadd import QuickAddWindow
from .views.search import SearchView
from .views.settings import SettingsDialog, set_autostart
from .views.stats import StatsView
from .views.article import ArticleView
from .views.sticky import StickyFloater, StickyView
from .views.tags import TagsView
from .views.unscheduled import UnscheduledView

#: 中央视图栈各页索引
VIEW_INDEX = {
    "board": 0,
    "daily": 1,
    "unscheduled": 2,
    "period": 3,
    "calendar": 4,
    "search": 5,
    "tags": 6,
    "sticky": 7,
    "article": 8,
    "stats": 9,
    "heatmap": 10,
}

#: 便签底色轮换（PRD F19.2）
_STICKY_COLORS = ["#FFF9C4", "#FFE0B2", "#C8E6C9", "#BBDEFB", "#E1BEE7", "#F8BBD0"]

EXPORT_FORMAT = "quadrant-todo-export"
EXPORT_VERSION = 1


def _merge_for_import(obj, existing_by_id: dict):
    """增量导入的落库策略（2026-09-11）。

    - 本地不存在该 id            → 直接新增
    - 同 id 且数据完全一致        → 覆盖（结果等价，无副作用）
    - 同 id 但数据不同            → 换新 id 追加为新记录，本地原记录保留

    这样「重复导入同一文件」是幂等的（不会产生副本），
    而任何有差异的记录都不会覆盖掉本地已有数据。
    """
    old = existing_by_id.get(obj.id)
    if old is not None and old.to_export() != obj.to_export():
        obj.id = uuid.uuid4().hex
    return obj


class MainWindow(QMainWindow):
    """主窗口：左侧导航 + 中央视图 + 右侧详情面板。"""

    #: 提醒到期，交由外部弹出通知（参数：Task）
    reminder_triggered = Signal(object)
    #: 用户从快捷键或托盘请求退出
    quit_requested = Signal()
    #: 任务数据发生变化，用于刷新托盘角标
    data_changed = Signal()

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.tasks: list[Task] = []
        self.today = date.today()
        self.selected_id: str | None = None
        self.current_view = "board"
        # 双紧急阈值：重要任务 / 不重要任务各自独立（PRD F6.3「每象限阈值可设」）。
        # 旧版仅有一个 urgency_threshold，作为「重要任务阈值」回退。
        raw_important = db.get_setting(config.KEY_THRESHOLD_IMPORTANT)
        if raw_important is not None:
            important = int(raw_important)
        else:
            important = db.get_int_setting(config.KEY_THRESHOLD, 2)
        raw_unimportant = db.get_setting(config.KEY_THRESHOLD_UNIMPORTANT)
        unimportant = int(raw_unimportant) if raw_unimportant is not None else important
        self.thresholds = Thresholds(important=important, unimportant=unimportant)
        self.current_period_kind = "week"
        # 主题始终跟随系统（设置中已移除手动切换入口），忽略历史 theme 设置值
        self.theme = config.THEME_SYSTEM
        self._pending_delete: Task | None = None

        # v2.1 视图状态
        self.search_keyword: str = ""
        self.search_include_closed: bool = False
        self.sticky_filter_keyword: str = ""  # 搜索词同时过滤便签页
        self.article_filter_keyword: str = ""  # 文章模块搜索词
        self.active_tag_ids: set[str] = set()
        self._sticky_floaters: dict[str, StickyFloater] = {}
        self._pomodoro: PomodoroTimer | None = None
        self._pomodoro_task_id: str | None = None

        self.setWindowTitle("四象限待办")
        self.resize(1280, 860)
        self.setMinimumSize(1024, 768)  # PRD F1.9

        self._load_styles()
        self._build_ui()
        self._connect_signals()
        self._setup_shortcuts()
        self._restore_geometry()
        self._watch_system_theme()
        self.reload()

    # ================================================================ UI 构建

    def _load_styles(self) -> None:
        """加载全局样式表（深浅色主题，PRD F24）。"""
        theme.apply_theme(self.theme)

    def _watch_system_theme(self) -> None:
        """当主题为 system 时，监听系统配色变化自动重应用（PRD F24）。"""
        app = QApplication.instance()
        if app is not None:
            hints = app.styleHints()
            if hints is not None and hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(lambda *_: self._on_system_scheme_changed())

    def _on_system_scheme_changed(self) -> None:
        if self.theme == config.THEME_SYSTEM:
            theme.apply_theme(self.theme)
            self.refresh_views()

    def _apply_default_reminder(self, task: Task, due_date) -> None:
        """新任务套用全局默认提醒联动（PRD F26.3）。"""
        rule = self.db.get_setting(config.KEY_DEFAULT_REMINDER_RULE, config.REMINDER_RULE_NONE)
        offset = self.db.get_int_setting(config.KEY_DEFAULT_REMINDER_OFFSET, config.DEFAULT_REMINDER_OFFSET)
        task.reminder_rule = rule
        task.reminder_offset = offset if rule == config.REMINDER_RULE_RELATIVE else None
        task.reminder = resolve_reminder(rule, due_date, None, task.reminder_offset)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部引导条（PRD F1.13）
        self.onboarding = QFrame()
        self.onboarding.setProperty("role", "onboarding")
        onboard_layout = QHBoxLayout(self.onboarding)
        onboard_layout.setContentsMargins(16, 8, 16, 8)
        onboard_text = QLabel("欢迎使用。试着在象限里添加第一个任务 —— 只需要填标题，象限会自己算出来。")
        onboard_layout.addWidget(onboard_text, 1)
        close_btn = QPushButton("知道了")
        close_btn.setFlat(True)
        close_btn.clicked.connect(self._dismiss_onboarding)
        onboard_layout.addWidget(close_btn)
        root.addWidget(self.onboarding)

        # 顶部搜索框（PRD F22.1 / F22.2 去抖）
        self.search_bar = QFrame()
        self.search_bar.setProperty("role", "search-bar")
        sb_layout = QHBoxLayout(self.search_bar)
        sb_layout.setContentsMargins(16, 8, 16, 8)
        sb_layout.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索任务（标题 / 备注）与小便签　Esc 退出")
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        sb_layout.addWidget(self.search_edit, 1)
        self.search_clear_btn = QPushButton("×")
        self.search_clear_btn.setFlat(True)
        self.search_clear_btn.setFixedWidth(28)
        self.search_clear_btn.clicked.connect(self._clear_search)
        sb_layout.addWidget(self.search_clear_btn)
        root.addWidget(self.search_bar)

        # 输入去抖：停止输入 250ms 后才真正执行搜索
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._run_search)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # 左侧导航
        nav = QFrame()
        nav.setFixedWidth(150)
        nav.setProperty("role", "nav")
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(10, 14, 10, 14)
        nav_layout.setSpacing(4)

        self.board_btn = QPushButton("四象限")
        self.board_btn.setCheckable(True)
        self.board_btn.setChecked(True)
        self.board_btn.clicked.connect(lambda: self.switch_view("board"))
        nav_layout.addWidget(self.board_btn)

        self.daily_btn = QPushButton("日待办")
        self.daily_btn.setCheckable(True)
        self.daily_btn.clicked.connect(lambda: self.switch_view("daily"))
        nav_layout.addWidget(self.daily_btn)

        # 周期视图（周/月/年待办，PRD F15.1）——共用 PeriodView，切换 kind
        self.week_btn = QPushButton("周待办")
        self.week_btn.setCheckable(True)
        self.week_btn.clicked.connect(lambda: self.switch_period("week"))
        nav_layout.addWidget(self.week_btn)

        self.month_btn = QPushButton("月待办")
        self.month_btn.setCheckable(True)
        self.month_btn.clicked.connect(lambda: self.switch_period("month"))
        nav_layout.addWidget(self.month_btn)

        self.year_btn = QPushButton("年待办")
        self.year_btn.setCheckable(True)
        self.year_btn.clicked.connect(lambda: self.switch_period("year"))
        nav_layout.addWidget(self.year_btn)

        self.calendar_btn = QPushButton("日历")
        self.calendar_btn.setCheckable(True)
        self.calendar_btn.clicked.connect(lambda: self.switch_view("calendar"))
        nav_layout.addWidget(self.calendar_btn)

        self.unscheduled_btn = QPushButton("待安排")
        self.unscheduled_btn.setCheckable(True)
        self.unscheduled_btn.clicked.connect(lambda: self.switch_view("unscheduled"))
        nav_layout.addWidget(self.unscheduled_btn)

        # ---- v2.1 导航入口 ----
        self.tags_btn = QPushButton("标签")
        self.tags_btn.setCheckable(True)
        self.tags_btn.clicked.connect(lambda: self.switch_view("tags"))
        nav_layout.addWidget(self.tags_btn)

        self.heatmap_btn = QPushButton("热力图")
        self.heatmap_btn.setCheckable(True)
        self.heatmap_btn.clicked.connect(lambda: self.switch_view("heatmap"))
        nav_layout.addWidget(self.heatmap_btn)

        self.sticky_btn = QPushButton("小便签")
        self.sticky_btn.setCheckable(True)
        self.sticky_btn.clicked.connect(lambda: self.switch_view("sticky"))
        nav_layout.addWidget(self.sticky_btn)

        self.article_btn = QPushButton("文章")
        self.article_btn.setCheckable(True)
        self.article_btn.clicked.connect(lambda: self.switch_view("article"))
        nav_layout.addWidget(self.article_btn)

        self.stats_btn = QPushButton("计时统计")
        self.stats_btn.setCheckable(True)
        self.stats_btn.clicked.connect(lambda: self.switch_view("stats"))
        nav_layout.addWidget(self.stats_btn)

        nav_layout.addStretch(1)

        # 统计区（PRD F6.1 / F6.2 / F6.5）
        self.stats_label = QLabel()
        self.stats_label.setProperty("role", "meta")
        self.stats_label.setWordWrap(True)
        nav_layout.addWidget(self.stats_label)

        self.unscheduled_label = QPushButton()
        self.unscheduled_label.setProperty("role", "link")
        self.unscheduled_label.setFlat(True)
        self.unscheduled_label.clicked.connect(lambda: self.switch_view("unscheduled"))
        nav_layout.addWidget(self.unscheduled_label)

        settings_btn = QPushButton("设置")
        settings_btn.setFlat(True)
        settings_btn.clicked.connect(self.open_settings)
        nav_layout.addWidget(settings_btn)

        body_layout.addWidget(nav)

        # 中央视图
        self.stack = QStackedWidget()
        self.board_view = BoardView()
        self.daily_view = DailyView()
        self.unscheduled_view = UnscheduledView()
        self.period_view = PeriodView()
        self.calendar_view = CalendarView()
        # v2.1
        self.search_view = SearchView()
        self.tags_view = TagsView()
        self.sticky_view = StickyView()
        self.stats_view = StatsView()
        self.heatmap_view = HeatmapView()
        # 文章模块（2026-09-10）
        self.article_view = ArticleView()
        for view in (
            self.board_view,
            self.daily_view,
            self.unscheduled_view,
            self.period_view,
            self.calendar_view,
            self.search_view,
            self.tags_view,
            self.sticky_view,
            self.article_view,
            self.stats_view,
            self.heatmap_view,
        ):
            self.stack.addWidget(view)
        body_layout.addWidget(self.stack, 1)

        # 右侧详情面板（PRD F5.1）
        self.detail = DetailPanel()
        body_layout.addWidget(self.detail)

        root.addWidget(body, 1)

        # 撤销提示条（PRD F5.7）
        self.undo_bar = QFrame()
        self.undo_bar.setProperty("role", "undo")
        undo_layout = QHBoxLayout(self.undo_bar)
        undo_layout.setContentsMargins(16, 8, 16, 8)
        self.undo_label = QLabel()
        undo_layout.addWidget(self.undo_label, 1)
        undo_btn = QPushButton("撤销")
        undo_btn.setFlat(True)
        undo_btn.clicked.connect(self._undo_delete)
        undo_layout.addWidget(undo_btn)
        self.undo_bar.setVisible(False)
        root.addWidget(self.undo_bar)

        self.setCentralWidget(central)

    def _connect_signals(self) -> None:
        # 看板独有：内联创建、跨象限拖拽、象限内排序
        self.board_view.task_created.connect(self.on_task_created)
        self.board_view.task_dropped.connect(self.on_task_dropped)
        self.board_view.order_changed.connect(self.on_order_changed)

        # 看板 + 日待办共有
        for view in (self.board_view, self.daily_view):
            view.task_toggled.connect(self.on_task_toggled)
            view.task_started.connect(self.on_task_started)
            view.task_today_toggled.connect(self.on_today_toggled)
            view.task_delete_requested.connect(self.on_delete_requested)
            view.task_selected.connect(self.on_task_selected)

        self.unscheduled_view.set_due_requested.connect(self._prompt_set_due_date)

        # 周期视图（周/月/年待办，PRD F15）
        self.period_view.task_toggled.connect(self.on_task_toggled)
        self.period_view.task_started.connect(self.on_task_started)
        self.period_view.task_today_toggled.connect(self.on_today_toggled)
        self.period_view.task_delete_requested.connect(self.on_delete_requested)
        self.period_view.task_selected.connect(self.on_task_selected)
        self.period_view.task_activated.connect(self._activate_task)
        self.period_view.kind_selected.connect(self.switch_period)
        self.period_view.anchor_step.connect(self._step_period)

        # 日历视图（PRD F17）
        self.calendar_view.task_selected.connect(self.on_task_selected)
        self.calendar_view.anchor_changed.connect(self._render_calendar)

        # 列表聚焦时按回车 → 聚焦详情（F14）。注意：不再用全局 Return 快捷键，
        # 否则会抢走输入框的回车（主键盘 Key_Return），导致无法创建任务。
        self.board_view.task_activated.connect(self._activate_task)
        self.daily_view.task_activated.connect(self._activate_task)

        self.detail.field_changed.connect(self.on_field_changed)
        self.detail.action_complete.connect(self.on_task_toggled)
        self.detail.action_start.connect(self.on_task_started)
        self.detail.action_today.connect(self.on_today_toggled)
        self.detail.action_abandon.connect(self.on_abandon)
        self.detail.action_restore.connect(self.on_restore)
        self.detail.action_delete.connect(self.on_delete_requested)

        # ---- v2.1 ----
        # F21 标签：详情面板编辑 + 标签筛选页
        self.detail.tags_changed.connect(self.on_task_tags_changed)
        self.detail.tag_add_requested.connect(self.on_tag_add_requested)
        self.tags_view.tag_toggled.connect(self.on_tag_filter_toggled)
        self.tags_view.clear_requested.connect(self.on_tag_filter_cleared)

        # F20 子任务
        self.detail.subtask_add.connect(self.on_subtask_add)
        self.detail.subtask_toggle.connect(self.on_subtask_toggle)
        self.detail.subtask_delete.connect(self.on_subtask_delete)

        # F23 番茄钟
        self.detail.pomodoro_requested.connect(self.start_pomodoro)

        # F22 搜索
        self.search_view.include_changed.connect(self.on_search_include_changed)
        self.search_view.sticky_open_requested.connect(
            lambda: self.switch_view("sticky")
        )

        # F19 小便签
        self.sticky_view.add_requested.connect(self.on_sticky_add)
        self.sticky_view.update_requested.connect(self.on_sticky_update)
        self.sticky_view.delete_requested.connect(self.on_sticky_delete)
        self.sticky_view.filter_clear_requested.connect(self.on_sticky_filter_cleared)

        # 文章模块（2026-09-10）
        self.article_view.add_requested.connect(self.on_article_add)
        self.article_view.update_requested.connect(self.on_article_update)
        self.article_view.delete_requested.connect(self.on_article_delete)
        self.article_view.search_requested.connect(self.on_article_search)
        self.article_view.tag_add_requested.connect(self.on_article_tag_add)
        self.article_view.tag_remove_requested.connect(self.on_article_tag_remove)
        self.article_view.selection_changed.connect(self._render_article)
        self.article_view.export_requested.connect(self.export_article_markdown)

        # F18 热力图年份切换
        self.heatmap_view.year_changed.connect(self._render_heatmap)

    def _setup_shortcuts(self) -> None:
        """键盘快捷键（PRD F14）。仅在应用获得焦点时生效。"""
        pairs = [
            ("Ctrl+N", self.open_quick_add),
            ("Ctrl+1", lambda: self.switch_view("board")),
            ("Ctrl+2", lambda: self.switch_view("daily")),
            ("Ctrl+3", lambda: self.switch_period("week")),
            ("Ctrl+4", lambda: self.switch_period("month")),
            ("Ctrl+5", lambda: self.switch_period("year")),
            ("Ctrl+6", lambda: self.switch_view("calendar")),
            ("Ctrl+7", lambda: self.switch_view("heatmap")),
            ("Ctrl+8", lambda: self.switch_view("article")),
            ("Ctrl+F", self.focus_search),
            ("Ctrl+,", self.open_settings),
            ("Ctrl+Q", self.request_quit),
            ("Escape", self._on_escape),
        ]
        for sequence, handler in pairs:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)

        # 任务导航/操作快捷键：焦点落在文本输入控件时禁用，避免编辑标题/备注时
        # 误触发切换选中、删除、完成等操作，导致光标被重置到开头（#bug-fix）。
        self._task_action_shortcuts: list[QShortcut] = []
        task_action_pairs = [
            ("Up", lambda: self._cycle_selection(-1)),
            ("Down", lambda: self._cycle_selection(1)),
            ("F2", self._rename_selection),
            ("Delete", lambda: self._act_on_selection(self.on_delete_requested)),
            ("Space", lambda: self._act_on_selection(self.on_task_toggled)),
        ]
        for sequence, handler in task_action_pairs:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self._task_action_shortcuts.append(shortcut)

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self._on_focus_changed(None, app.focusWidget() if app else None)

    def _on_focus_changed(self, _old, now) -> None:
        """焦点变化：输入框聚焦时禁用任务操作快捷键，避免干扰文本输入。"""
        editing = _is_editing_widget(now)
        for shortcut in self._task_action_shortcuts:
            shortcut.setEnabled(not editing)

    # ================================================================ 数据

    def reload(self) -> None:
        self.tasks = self._sort_for_display(self.db.all_tasks())
        self.refresh_views()
        self.data_changed.emit()

    @staticmethod
    def _sort_for_display(tasks: list) -> list:
        """V1.0.1：统一任务列表展示顺序。

        排序优先级（用户决策）：
            1. 进行中（status='doing'）排最前
            2. 按 due_date 升序（无截止日期排最后，逾期自然靠前）
            3. 保持原 sort_order（用户拖拽的手动排序）
            4. 创建时间作为最终稳定兜底
        """
        def _due_key(t):
            if getattr(t, "due_date", None) is None:
                return (1, 0)  # 无日期归入第二组（排后）
            return (0, t.due_date.toordinal())

        def _key(t):
            is_doing = 0 if getattr(t, "status", "") == "doing" else 1
            return (is_doing, _due_key(t), getattr(t, "sort_order", 0), getattr(t, "created_at", None))

        return sorted(tasks, key=_key)

    def refresh_views(self) -> None:
        """刷新视图。

        只渲染**当前可见**的那一页。所有视图都是同一份任务的筛选视图，
        隐藏页的内容在切换过去时再渲染即可。若每次数据变更都全量重建，
        热力图一年就有 365 个格子、日历也有数十个单元格，既拖慢响应，
        也会显著放大 Qt 对象的析构风险。
        """
        name = self.current_view
        board_cb = self.on_task_toggled
        select_cb = self.on_task_selected

        if name == "board":
            self.board_view.render(self.tasks, self.today, self.thresholds, self.selected_id)
        elif name == "daily":
            self.daily_view.render(self.tasks, self.today, self.thresholds, self.selected_id, board_cb, select_cb)
        elif name == "unscheduled":
            self.unscheduled_view.render(self.tasks, self.today, self.thresholds)
        elif name == "period":
            self._render_period()
        elif name == "calendar":
            self._render_calendar()
        elif name == "search":
            self._render_search()
        elif name == "tags":
            self._render_tags()
        elif name == "sticky":
            self._render_sticky()
        elif name == "article":
            self._render_article()
        elif name == "stats":
            self._render_stats()
        elif name == "heatmap":
            self._render_heatmap()

        # 详情面板与侧栏统计常驻可见，始终刷新
        self._render_detail()
        self._update_stats()
        self.apply_onboarding_state()

    def _render_detail(self) -> None:
        task = self._find(self.selected_id) if self.selected_id else None
        if task:
            self.detail.show_task(
                task,
                self.today,
                self.thresholds,
                all_tags=self.db.get_tags(),
                active_tag_ids=self.db.get_task_tag_ids(task.id),
                subtasks=self.db.get_subtasks(task.id),
            )
        else:
            self.detail.show_empty()

    # ------------------------------------------------------------ v2.1 视图渲染

    def _render_tags(self) -> None:
        """F21.3 / F21.4：标签 chip + 多标签 AND 筛选结果。"""
        tags = self.db.get_tags()
        if self.active_tag_ids:
            hit_ids = set(self.db.tasks_with_all_tags(sorted(self.active_tag_ids)))
            tasks = [t for t in self.tasks if t.id in hit_ids]
        else:
            tasks = self.tasks
        self.tags_view.render(
            tags, self.active_tag_ids, tasks, self.today, self.thresholds,
            self.selected_id,
            on_toggle=self.on_task_toggled,
            on_start=self.on_task_started,
            on_today=self.on_today_toggled,
            on_delete=self.on_delete_requested,
            on_select=self.on_task_selected,
        )

    def _render_sticky(self) -> None:
        """F19.1：便签列表。与任务完全隔离；有搜索词时仅显示匹配便签。"""
        keyword = self.sticky_filter_keyword
        notes = self.db.search_stickies(keyword) if keyword else self.db.get_stickies()
        self.sticky_view.render(notes, keyword=keyword or None)

    def _render_stats(self) -> None:
        """F23.5：计时统计（把 task_id 排行映射为任务标题）。"""
        stats = self.db.pomodoro_stats()
        id_to_title = {t.id: t.title for t in self.tasks}
        ranking = [(id_to_title.get(tid, "（已删除任务）"), minutes)
                   for tid, minutes in stats.get("ranking", [])]
        self.stats_view.render(stats, ranking)

    def _render_heatmap(self) -> None:
        """F18：年度热力图。"""
        year = self.heatmap_view.year
        counts = self.db.daily_completion_counts(year)
        summary = self.db.heatmap_summary(year, self.today)
        self.heatmap_view.render(counts, summary["total"], summary["streak"])

    def _render_search(self) -> None:
        """F22：搜索结果（任务 + 小便签）。"""
        keyword = self.search_keyword
        stickies: list = []
        if keyword:
            stickies = self.db.search_stickies(keyword)
        if not keyword:
            self.search_view.render(
                [], self.today, self.thresholds, keyword, self.search_include_closed,
                self.selected_id, on_select=self.on_task_selected,
                stickies=[],
            )
            return
        tasks = self.db.search_tasks(keyword, self.search_include_closed)
        self.search_view.render(
            tasks, self.today, self.thresholds, keyword, self.search_include_closed,
            self.selected_id,
            on_toggle=self.on_task_toggled,
            on_start=self.on_task_started,
            on_today=self.on_today_toggled,
            on_delete=self.on_delete_requested,
            on_select=self.on_task_selected,
            stickies=stickies,
        )

    def _find(self, task_id: str | None) -> Task | None:
        if not task_id:
            return None
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def _save(self, task: Task, log_event: str | None = None, **params) -> bool:
        """保存任务。失败时提示并保留用户输入（PRD F5.12 / 4.5）。"""
        try:
            self.db.save_task(task)
            if log_event:
                self.db.log_event(log_event, **params)
            return True
        except DatabaseError as exc:
            self.detail.show_error(f"数据保存失败：{exc}")
            return False

    def _update_stats(self) -> None:
        """顶部与侧栏统计（PRD F6.1 / F6.2 / F6.5）。"""
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end = (month_start + timedelta(days=32)).replace(day=1)
        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        year_end = year_start.replace(year=year_start.year + 1)

        month_done = self.db.count_done_in_range(month_start, month_end)
        month_total = self.db.count_planned_in_range(month_start, month_end)
        year_done = self.db.count_done_in_range(year_start, year_end)
        year_total = self.db.count_planned_in_range(year_start, year_end)
        unscheduled = self.db.count_unscheduled()

        self.stats_label.setText(
            f"{self.today.strftime('%Y年%m月%d日')}\n"
            f"本月 {month_done} / {month_total}\n"
            f"本年 {year_done} / {year_total}"
        )
        self.unscheduled_label.setText(f"待安排 {unscheduled} 条")
        self.unscheduled_label.setVisible(unscheduled > 0)

    # ================================================================ 视图切换

    def switch_view(self, name: str) -> None:
        self.current_view = name
        self.stack.setCurrentIndex(VIEW_INDEX[name])
        self.board_btn.setChecked(name == "board")
        self.daily_btn.setChecked(name == "daily")
        self.week_btn.setChecked(name == "period" and self.current_period_kind == "week")
        self.month_btn.setChecked(name == "period" and self.current_period_kind == "month")
        self.year_btn.setChecked(name == "period" and self.current_period_kind == "year")
        self.calendar_btn.setChecked(name == "calendar")
        self.unscheduled_btn.setChecked(name == "unscheduled")
        self.tags_btn.setChecked(name == "tags")
        self.heatmap_btn.setChecked(name == "heatmap")
        self.sticky_btn.setChecked(name == "sticky")
        self.article_btn.setChecked(name == "article")
        self.stats_btn.setChecked(name == "stats")
        # 视图内容按需渲染：切过去时才渲染该页
        self.refresh_views()

    def switch_period(self, kind: str) -> None:
        """切换周期视图类型（周/月/年），并复位到以今天为锚点（PRD F15.4）。"""
        self.current_period_kind = kind
        self.period_view.set_kind(kind)
        self.period_view.set_anchor(date.today())
        self.period_view.anchor = date.today()
        self.switch_view("period")

    def _step_period(self, delta: int) -> None:
        """上一期 / 下一期（仅改变视图窗口，不改变数据，PRD F15.4）。"""
        if self.current_view != "period":
            return
        from datetime import timedelta

        kind = self.current_period_kind
        anchor = self.period_view.anchor
        if kind == "week":
            anchor = anchor + timedelta(weeks=delta)
        elif kind == "month":
            y, m = anchor.year, anchor.month + delta
            if m > 12:
                y, m = y + 1, 1
            elif m < 1:
                y, m = y - 1, 12
            anchor = anchor.replace(year=y, month=m, day=1)
        elif kind == "year":
            anchor = anchor.replace(year=anchor.year + delta)
        self.period_view.anchor = anchor
        self.period_view.set_anchor(anchor)
        self._render_period()

    def _render_period(self) -> None:
        from .recurrence import period_range

        start, end = period_range(self.current_period_kind, self.period_view.anchor)
        completions = self.db.get_completions_in_range(start, end)
        self.period_view.render(
            self.tasks, self.today, self.thresholds, self.selected_id,
            completions, self.on_task_toggled, self.on_task_selected,
        )

    def _render_calendar(self) -> None:
        """上/下月切换后仅重渲染日历视图（不改变数据，PRD F17）。"""
        self.calendar_view.render(self.tasks, self.today, self.thresholds)

    # ================================================================ F22 搜索

    def _on_search_text_changed(self, text: str) -> None:
        """输入去抖（PRD F22.2）。"""
        self._search_timer.start()

    def _run_search(self) -> None:
        keyword = self.search_edit.text().strip()
        if keyword == self.search_keyword and self.current_view == "search":
            return
        self.search_keyword = keyword
        self.sticky_filter_keyword = keyword
        if keyword:
            # switch_view 内部会触发渲染
            self.switch_view("search")
        elif self.current_view == "search":
            # 清空搜索 → 回到看板（F22.5）
            self.switch_view("board")

    def _clear_search(self) -> None:
        self.search_edit.clear()
        self._search_timer.stop()
        self.search_keyword = ""
        self.sticky_filter_keyword = ""
        if self.current_view == "search":
            self.switch_view("board")

    def on_search_include_changed(self, include: bool) -> None:
        """F22.4：是否包含已完成 / 已放弃。"""
        self.search_include_closed = bool(include)
        self._render_search()

    def on_sticky_filter_cleared(self) -> None:
        """便签页清除搜索词过滤（不影响顶部搜索框状态）。"""
        self.sticky_filter_keyword = ""
        self._render_sticky()

    def focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    # ================================================================ F21 标签

    def on_task_tags_changed(self, task_id: str, tag_ids: list) -> None:
        """F21.2：整体替换任务的标签集合。"""
        self.db.set_task_tags(task_id, list(tag_ids))
        self.db.log_event("tag_change", count=len(tag_ids))
        self.refresh_views()

    def on_tag_add_requested(self, task_id: str, name: str) -> None:
        """F21.1：标签名唯一；已存在则复用，不存在则新建。"""
        tag = self.db.find_tag_by_name(name)
        if tag is None:
            color = _STICKY_COLORS[len(self.db.get_tags()) % len(_STICKY_COLORS)]
            tag = self.db.create_tag(name, color)
            if tag is None:
                self.detail.show_error(f"标签「{name}」创建失败")
                return
        current = self.db.get_task_tag_ids(task_id)
        if tag.id in current:
            self.refresh_views()
            return
        self.db.set_task_tags(task_id, current + [tag.id])
        self.refresh_views()

    def on_tag_filter_toggled(self, tag_id: str) -> None:
        """F21.4：多标签 AND 筛选。"""
        if tag_id in self.active_tag_ids:
            self.active_tag_ids.discard(tag_id)
        else:
            self.active_tag_ids.add(tag_id)
        self._render_tags()

    def on_tag_filter_cleared(self) -> None:
        self.active_tag_ids.clear()
        self._render_tags()

    # ================================================================ F20 子任务

    def on_subtask_add(self, task_id: str, title: str) -> None:
        """F20.1：新增子任务。"""
        existing = self.db.get_subtasks(task_id)
        self.db.save_subtask(Subtask(parent_id=task_id, title=title, sort_order=len(existing)))
        self.db.log_event("subtask_add")
        self.refresh_views()

    def on_subtask_toggle(self, task_id: str, subtask_id: str, done: bool) -> None:
        """F20.2：勾选子任务。F20.5 红线：不影响父任务状态，也不影响象限。"""
        for sub in self.db.get_subtasks(task_id):
            if sub.id == subtask_id:
                sub.done = bool(done)
                self.db.save_subtask(sub)
                break
        self.db.log_event("subtask_toggle", done=bool(done))
        self.refresh_views()

    def on_subtask_delete(self, task_id: str, subtask_id: str) -> None:
        self.db.delete_subtask(subtask_id)
        self.refresh_views()

    # ================================================================ F23 番茄钟

    def start_pomodoro(self, task_id: str) -> None:
        """F23.1：开始一次番茄钟。F23.4 红线：不触碰任务状态机。"""
        if self._pomodoro is not None:
            QMessageBox.information(self, "番茄钟", "已有番茄钟正在进行中。")
            return
        minutes = self.db.get_int_setting(config.KEY_POMODORO_DURATION, 25)
        task = self.db.get_task(task_id)
        title = task.title if task else ""
        self._pomodoro_task_id = task_id
        self._pomodoro = PomodoroTimer(task_id, minutes, task_title=title)
        self._pomodoro.finished.connect(self._on_pomodoro_finished)
        self._pomodoro.show()

    def _on_pomodoro_finished(self, session) -> None:
        """F23.3：自然结束与中止都写入 session。"""
        self.db.save_pomodoro_session(session)
        self.db.log_event("pomodoro_end", status=session.status, actual_min=session.actual_min)
        self._pomodoro = None
        self._pomodoro_task_id = None
        self.refresh_views()

    # ================================================================ F19 小便签

    def on_sticky_add(self, content: str) -> None:
        """F19.1：新建便签。与任务完全隔离，不进入任何任务视图。"""
        color = _STICKY_COLORS[len(self.db.get_stickies()) % len(_STICKY_COLORS)]
        self.db.save_sticky(StickyNote(content=content, color=color))
        self.refresh_views()

    def on_sticky_update(self, note_id: str, field: str, value) -> None:
        for note in self.db.get_stickies():
            if note.id != note_id:
                continue
            if field == "content":
                note.content = value
            elif field == "color":
                note.color = value
            elif field == "pinned":
                note.pinned = bool(value)
                if note.pinned:
                    self._open_sticky_floater(note)
                else:
                    self._close_sticky_floater(note.id)
            elif field == "locked":  # V1.0.1
                note.locked = bool(value)
            elif field == "top":  # 文章模块：列表置顶
                note.top = bool(value)
            self.db.save_sticky(note)
            break
        self.refresh_views()

    def on_sticky_delete(self, note_id: str) -> None:
        self._close_sticky_floater(note_id)
        self.db.delete_sticky(note_id)
        self.refresh_views()

    # ================================================================ 文章模块（2026-09-10）

    def _render_article(self) -> None:
        """渲染文章列表页；搜索态按关键词过滤，并带上当前文章的标签。"""
        keyword = self.article_filter_keyword
        if keyword:
            articles = self.db.search_articles(keyword)
            # 列表高亮用去掉前缀后的纯净查询词（title:/content:/tag: 前缀不进高亮）
            _, highlight_kw = self.db.parse_search_query(keyword)
        else:
            articles = self.db.get_articles()
            highlight_kw = None
        active_id = getattr(self.article_view, "_selected_id", None)
        self.article_view.render(
            articles,
            keyword=highlight_kw or None,
            all_tags=self.db.get_tags(),
            active_tag_ids=self.db.get_article_tag_ids(active_id) if active_id else [],
        )

    def on_article_add(self, title: str) -> None:
        article = Article(title=title or "无标题文章")
        self.db.save_article(article)
        # 新建后自动选中并强制刷新右侧
        self.article_view._selected_id = article.id
        self.article_view._rendered_selected_id = None
        self.refresh_views()

    def on_article_update(self, article_id: str, field: str, value) -> None:
        article = self.db.get_article(article_id)
        if article is None:
            return
        if field == "title":
            article.title = value
        elif field == "content":
            article.content = value
        elif field == "tags":
            self.db.set_article_tags(article_id, list(value))
            self._render_article()
            return
        self.db.save_article(article)
        self.refresh_views()

    def on_article_delete(self, article_id: str) -> None:
        reply = QMessageBox.question(
            self, "删除文章", "确认删除这篇文章吗？删除后无法恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.db.delete_article(article_id)
        if getattr(self.article_view, "_selected_id", None) == article_id:
            self.article_view._selected_id = None
            self.article_view._rendered_selected_id = None
        self.refresh_views()

    def on_article_search(self, keyword: str) -> None:
        self.article_filter_keyword = keyword
        self._render_article()

    def on_article_tag_add(self, article_id: str, name: str) -> None:
        tag = self.db.create_tag(name)
        if tag is None:
            tag = self.db.find_tag_by_name(name)
        if tag is None:
            return
        current = self.db.get_article_tag_ids(article_id)
        if tag.id not in current:
            self.db.set_article_tags(article_id, current + [tag.id])
        self._render_article()

    def on_article_tag_remove(self, article_id: str, tag_id: str) -> None:
        current = self.db.get_article_tag_ids(article_id)
        self.db.set_article_tags(article_id, [t for t in current if t != tag_id])
        self._render_article()

    def _open_sticky_floater(self, note: StickyNote) -> None:
        """F19.3：打开（或复用）常驻浮层。
        V1.0.1：默认定位在屏幕最右侧，垂直居中；多个常驻便签依次向左错开避免重叠。
        """
        existing = self._sticky_floaters.get(note.id)
        if existing is not None:
            existing.show()
            existing.raise_()
            return
        floater = StickyFloater(note)
        floater.close_requested.connect(self._close_sticky_floater)
        self._sticky_floaters[note.id] = floater
        self._position_sticky_floater(floater)
        floater.show()

    @staticmethod
    def _position_sticky_floater(floater: StickyFloater) -> None:
        """V1.0.1：默认放在屏幕最右侧（垂直居中）。同屏多便签时由调用方控制左右错开。"""
        screen = floater.screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 16
        x = geo.x() + geo.width() - floater.width() - margin
        y = geo.y() + (geo.height() - floater.height()) // 2
        floater.move(x, y)

    def _close_sticky_floater(self, note_id: str) -> None:
        """F19.4：关闭浮层仅隐藏，便签数据与 pinned 标记均保留。"""
        floater = self._sticky_floaters.pop(note_id, None)
        if floater is not None:
            floater.close()
            floater.deleteLater()

    def restore_pinned_stickies(self) -> None:
        """启动时按 pinned 恢复常驻浮层（PRD F19.5）。
        V1.0.1：多个便签依次向左错开，避免在屏幕最右端堆叠重叠。"""
        for i, note in enumerate(self.db.get_pinned_stickies()):
            self._open_sticky_floater(note)
            if i > 0 and self._sticky_floaters.get(note.id) is not None:
                floater = self._sticky_floaters[note.id]
                # 多个常驻便签：每个向左偏移 232px（220 宽 + 12 间距）
                screen = floater.screen()
                if screen is None:
                    continue
                geo = screen.availableGeometry()
                margin = 16
                x = geo.x() + geo.width() - floater.width() * (i + 1) - 12 * i - margin
                y = geo.y() + (geo.height() - floater.height()) // 2
                floater.move(x, y)

    # ================================================================ 任务操作

    def on_task_selected(self, task_id: str) -> None:
        self.selected_id = task_id
        self._render_detail()
        self.refresh_views()

    def on_task_created(self, title: str, quadrant_value: str) -> None:
        """F2.3：入口只决定「重不重要」，象限交由系统计算，不锁定。
        F2.10：标题中的日期自动识别为截止日期，从而自动落入对应象限。
        """
        quadrant = Quadrant(quadrant_value)
        importance = quadrant in (Quadrant.Q1, Quadrant.Q2)
        parsed = parse_due_from_title(title, self.today)

        task = Task(title=parsed.title, importance=importance, due_date=parsed.due_date)
        task.sort_order = self.db.next_sort_order()
        self._apply_default_reminder(task, parsed.due_date)
        self.db.save_task(task)
        self.db.log_event(
            "task_create",
            quadrant=task.quadrant(self.today, self.thresholds).value,
            has_due_date=parsed.due_date is not None,
            importance=importance,
        )
        self.reload()

    def on_task_dropped(self, task_id: str, target_value: str) -> None:
        """F1.8：把拖拽翻译为属性修改，而不是直接改象限。

        拖入 Q1/Q2 → 重要；Q3/Q4 → 不重要
        拖入 Q1/Q3 → 截止日期设为今天；Q2/Q4 → 延后至阈值之外
        两条规则同时执行，只弹一次合并确认框。
        """
        task = self._find(task_id)
        if not task:
            return
        target = Quadrant(target_value)
        current = task.quadrant(self.today, self.thresholds)
        if current == target:
            return

        want_important = target in (Quadrant.Q1, Quadrant.Q2)
        want_urgent = target in (Quadrant.Q1, Quadrant.Q3)

        changes: list[str] = []
        new_importance = task.importance
        new_due = task.due_date

        if task.importance != want_important:
            new_importance = want_important
            changes.append("标记为重要" if want_important else "取消重要标记")

        if want_urgent:
            if task.due_date is None or task.due_date > self.today:
                new_due = self.today
                changes.append("将截止日期设为今天")
        else:
            if task.due_date is not None:
                thr = self.thresholds.for_importance(task.importance)
                if (task.due_date - self.today).days <= thr:
                    new_due = self.today + timedelta(days=thr + 1)
                    changes.append(f"将截止日期延后至 {new_due.strftime('%m-%d')}")

        if not changes:
            return

        box = QMessageBox(self)
        box.setWindowTitle("移动任务")
        box.setText(
            f"将「{task.title}」移到 {target.value}，需要：\n\n· " + "\n· ".join(changes)
        )
        box.setInformativeText("任务仍会按规则自动计算象限。")
        auto_btn = box.addButton("确定", QMessageBox.AcceptRole)
        lock_btn = box.addButton("锁定到此象限", QMessageBox.YesRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is auto_btn:
            task.importance = new_importance
            task.due_date = new_due
            task.locked_quadrant = None
        elif clicked is lock_btn:
            task.importance = new_importance
            task.due_date = new_due
            task.locked_quadrant = target.value
        else:
            return

        self.db.save_task(task)
        self.db.log_event("quadrant_lock" if task.locked_quadrant else "task_update", to=target.value)
        self.reload()

    def on_order_changed(self, quadrant_value: str, ordered_ids: list[str]) -> None:
        """F1.11：保存象限内手动排序结果。"""
        for index, task_id in enumerate(ordered_ids):
            task = self._find(task_id)
            if task and task.sort_order != index:
                task.sort_order = index
                self.db.save_task(task)
        self.reload()

    def on_task_toggled(self, task_id: str) -> None:
        """勾选完成（F5.6）/ 取消完成（F4.10）。"""
        task = self._find(task_id)
        if not task:
            return
        if task.status == STATUS_ABANDONED:
            return

        if task.status == STATUS_DONE:
            task.status = STATUS_TODO
            task.completed_at = None
            self.db.log_event("task_uncomplete", quadrant=task.quadrant(self.today, self.thresholds).value)
        else:
            # 子任务约束（2026-09-10）：存在未完成子任务时禁止完成主任务
            if self.db.get_incomplete_subtask_count(task.id) > 0:
                QMessageBox.warning(
                    self, "无法完成",
                    "请先完成该任务下的全部子任务，再标记主任务完成。",
                )
                return
            task.status = STATUS_DONE
            task.completed_at = datetime.now()
            self.db.log_event(
                "task_complete",
                quadrant=task.quadrant(self.today, self.thresholds).value,
                is_overdue=task.is_overdue_on(self.today),
            )
            # F15.6 / F16.3：写入不可变完成历史（周期任务重生后不丢失，
            # 普通任务也计入周期视图「已完成 (n)」分组）
            seq = self.db.count_completions(task.id) + 1
            self.db.record_completion(TaskCompletion(
                task_id=task.id,
                completed_at=task.completed_at,
                cycle_seq=seq,
                source="manual",
            ))
        self._save(task)
        self.reload()

    def on_task_started(self, task_id: str) -> None:
        """F5.5：切换为进行中（仅状态，不计时）。"""
        task = self._find(task_id)
        if not task or task.status != STATUS_TODO:
            return
        task.status = STATUS_DOING
        self._save(task, "task_start", quadrant=task.quadrant(self.today, self.thresholds).value)
        self.reload()

    def on_today_toggled(self, task_id: str) -> None:
        """加入/移出今日 —— 以截止日期表达（2026-09-11 调整）。

        加入今日 → 截止日期设为今天；移出今日 → 截止日期清空。
        today_flag 同步跟随，保证与历史数据（旧版靠 today_flag 标记）口径一致。
        """
        task = self._find(task_id)
        if not task or task.is_closed:
            return
        if task.due_date == self.today:          # 已是今天 → 移出
            task.due_date = None
            task.today_flag = False
            event = "remove_from_today"
        else:                                    # 加入今天
            task.due_date = self.today
            task.today_flag = True
            event = "add_to_today"
        self._save(task, event, has_due_date=task.due_date is not None)
        self.reload()

    def on_abandon(self, task_id: str) -> None:
        task = self._find(task_id)
        if not task:
            return
        task.status = STATUS_ABANDONED
        self._save(task, "task_abandon")
        self.reload()

    def on_restore(self, task_id: str) -> None:
        """F5.13：已放弃任务恢复为待开始。"""
        task = self._find(task_id)
        if not task:
            return
        task.status = STATUS_TODO
        task.completed_at = None
        self._save(task)
        self.reload()

    def on_delete_requested(self, task_id: str) -> None:
        """F5.7：二次确认 + 5 秒撤销。"""
        task = self._find(task_id)
        if not task:
            return
        reply = QMessageBox.question(
            self, "删除任务", f"确定删除「{task.title}」？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if self.selected_id == task_id:
            self.selected_id = None
        self.db.delete_task(task_id)
        self._pending_delete = task
        self.reload()
        self._show_undo(task)

    def on_field_changed(self, task_id: str, field: str, value) -> None:
        """F5.11：即时保存，无保存按钮。"""
        task = self._find(task_id)
        if not task:
            return

        if field == "title":
            if not value or not str(value).strip():
                self.detail.show_error("标题不能为空")
                return
            task.title = str(value).strip()
        elif field == "note":
            task.note = value
            if self._save(task):
                # 备注变更不影响任何列表视图，仅更新 DB；
                # 避免 reload() 重刷右侧面板导致输入框失焦/滚动复位。
                return
            return
        elif field == "due_date":
            task.due_date = value
        elif field == "estimate":
            task.estimate = value
        elif field == "reminder":
            task.reminder = value
            task.reminder_fired = False  # F9.10：修改提醒后允许再次触发
        elif field == "locked_quadrant":
            task.locked_quadrant = value
            self.db.log_event("quadrant_lock" if value else "quadrant_unlock", to=value or "auto")
        elif field == "cycle":
            old = task.cycle
            task.cycle = value
            if value == "none":
                # F16.5：改回「无」→ 退化为普通任务，清空周期字段
                task.custom_rule = None
                task.last_reset = None
            elif old == "none":
                # F16.2：从「无」改为具体周期 → 设置 last_reset = today
                task.last_reset = self.today
            self.db.log_event("cycle_task_create" if value != "none" else "cycle_task_clear", cycle=value)
        elif field == "custom_rule":
            task.custom_rule = value
        elif field == "reminder_rule":
            task.reminder_rule = value
            # F26.2 / F26.6：切换规则时按新规则重算 reminder
            task.reminder = resolve_reminder(
                value, task.due_date, task.reminder, task.reminder_offset
            )
            self.db.log_event("reminder_rule_set", rule=value, offset=task.reminder_offset)
        elif field == "reminder_offset":
            task.reminder_offset = value
            if task.reminder_rule == "relative":
                # F26.2：relative 下提前量变化 → 重算 reminder
                task.reminder = compute_relative_reminder(task.due_date, value)
        else:
            return

        # 修改 dueDate / 周期 / 提醒规则后，若处于 relative 模式需同步重算提醒
        if field == "due_date" and task.reminder_rule == "relative":
            task.reminder = compute_relative_reminder(task.due_date, task.reminder_offset)

        if self._save(task):
            self.reload()

    # ================================================================ 撤销

    def _show_undo(self, task: Task) -> None:
        self.undo_label.setText(f"已删除「{task.title}」")
        self.undo_bar.setVisible(True)
        QTimer.singleShot(config.UNDO_TIMEOUT_SECONDS * 1000, self._hide_undo)

    def _hide_undo(self) -> None:
        self.undo_bar.setVisible(False)
        self._pending_delete = None

    def _undo_delete(self) -> None:
        if not self._pending_delete:
            return
        task = self._pending_delete
        self.db.save_task(task)
        self._hide_undo()
        self.reload()

    # ================================================================ 跨日与提醒

    def on_day_changed(self, today: date) -> None:
        """F12.2 / F4.7：跨日时重算象限、重置今日标记、刷新视图。"""
        self.today = today
        self.db.reset_today_flags()
        self.db.set_setting(config.KEY_LAST_OPEN_DATE, today.isoformat())
        self.reload()

    def on_reminders_due(self, tasks: list[Task]) -> None:
        """F9.2 / F9.6：弹出通知并标记已触发，避免重复打扰。"""
        for task in tasks:
            self.db.mark_reminder_fired(task.id)
            self.reminder_triggered.emit(task)

    # ================================================================ 交互入口

    def open_quick_add(self) -> None:
        if not hasattr(self, "_quick_add"):
            self._quick_add = QuickAddWindow()
            self._quick_add.submitted.connect(self._on_quick_add)
        self._quick_add.show_and_focus()

    def _on_quick_add(self, title: str, importance: bool) -> None:
        """F2.10：标题中的日期自动识别为截止日期。"""
        parsed = parse_due_from_title(title, self.today)
        task = Task(title=parsed.title, importance=importance, due_date=parsed.due_date)
        task.sort_order = self.db.next_sort_order()
        self._apply_default_reminder(task, parsed.due_date)
        self.db.save_task(task)
        self.db.log_event(
            "task_create",
            importance=importance,
            has_due_date=parsed.due_date is not None,
        )
        self.reload()

    def open_settings(self) -> None:
        dialog = SettingsDialog(
            self.thresholds.important, self.thresholds.unimportant,
            self.db.get_setting(config.KEY_DEFAULT_REMINDER_RULE, config.REMINDER_RULE_NONE),
            self.db.get_int_setting(config.KEY_DEFAULT_REMINDER_OFFSET, config.DEFAULT_REMINDER_OFFSET),
            self.db.get_int_setting(config.KEY_POMODORO_DURATION, 25),
            self.db.get_setting(config.KEY_GLOBAL_HOTKEY_ENABLED, "0") == "1",
            self.db.get_setting(config.KEY_HOTKEY_TOGGLE, "Ctrl+Alt+Q"),
            self.db.get_setting(config.KEY_HOTKEY_QUICKADD, "Ctrl+Alt+N"),
            self.db.get_setting(config.KEY_BACKUP_ENABLED, config.DEFAULT_BACKUP_ENABLED) == "1",
            self.db.get_int_setting(
                config.KEY_BACKUP_INTERVAL_DAYS, config.DEFAULT_BACKUP_INTERVAL_DAYS
            ),
            self.db.get_setting(config.KEY_BACKUP_TIME, config.DEFAULT_BACKUP_TIME),
            self.db.get_int_setting(
                config.KEY_BACKUP_KEEP_COUNT, config.DEFAULT_BACKUP_KEEP_COUNT
            ),
            self.db.get_setting(config.KEY_LAST_BACKUP_AT, ""),
            self,
        )
        dialog.thresholds_changed.connect(self.on_thresholds_changed)
        dialog.autostart_changed.connect(lambda enabled: set_autostart(enabled))
        dialog.export_requested.connect(self.export_data)
        dialog.import_requested.connect(self.import_data)
        dialog.data_dir_change_requested.connect(self.on_data_dir_change_requested)
        dialog.default_reminder_changed.connect(self.on_default_reminder_changed)
        dialog.pomodoro_changed.connect(self.on_pomodoro_duration_changed)
        dialog.hotkey_changed.connect(self.on_hotkey_changed)
        dialog.backup_changed.connect(self.on_backup_changed)
        dialog.backup_now_requested.connect(lambda: self.on_backup_now(dialog))
        dialog.exec()

    def on_pomodoro_duration_changed(self, minutes: int) -> None:
        """F23.6：番茄钟默认时长设置。"""
        self.db.set_setting(config.KEY_POMODORO_DURATION, int(minutes))

    def on_hotkey_changed(self, enabled: bool, toggle: str, quickadd: str) -> None:
        """F25.5：热键开关与组合变更，立即生效。"""
        self.db.set_setting(config.KEY_GLOBAL_HOTKEY_ENABLED, "1" if enabled else "0")
        self.db.set_setting(config.KEY_HOTKEY_TOGGLE, toggle)
        self.db.set_setting(config.KEY_HOTKEY_QUICKADD, quickadd)
        if hasattr(self, "_on_hotkey_settings_changed"):
            self._on_hotkey_settings_changed()

    def on_backup_changed(self, enabled: bool, interval_days: int,
                          moment: str, keep_count: int) -> None:
        """F10.5：自动备份策略（开关 / 频率 / 时刻 / 保留份数）。"""
        self.db.set_setting(config.KEY_BACKUP_ENABLED, "1" if enabled else "0")
        self.db.set_setting(config.KEY_BACKUP_INTERVAL_DAYS, int(interval_days))
        self.db.set_setting(config.KEY_BACKUP_TIME, moment)
        self.db.set_setting(config.KEY_BACKUP_KEEP_COUNT, int(keep_count))

    def on_backup_now(self, dialog: SettingsDialog | None = None) -> None:
        """F10.5：立即备份一次，不受频率与时刻限制。"""
        path = backup.run(self.db)
        if path is None:
            QMessageBox.warning(self, "备份失败", "未能创建备份文件，请查看日志了解详情。")
            return
        if dialog is not None:
            dialog.set_last_backup_at(self.db.get_setting(config.KEY_LAST_BACKUP_AT, ""))

    def on_data_dir_change_requested(self, new_dir: str) -> None:
        """F10.1：更改数据文件位置——迁移现有数据到新目录并持久化，重启后生效。"""
        new_dir = Path(new_dir).resolve()
        if new_dir == config.DATA_DIR.resolve():
            QMessageBox.information(self, "提示", "所选目录与当前数据目录相同。")
            return
        reply = QMessageBox.question(
            self, "更改数据位置",
            f"将把现有数据（任务库、备份、日志）移动到：\n{new_dir}\n\n"
            "修改后需要重启应用才能生效，是否继续？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.db.close()
            config.migrate_data_to(new_dir)
            config.save_data_dir(new_dir)
        except OSError as exc:
            QMessageBox.critical(
                self, "迁移失败",
                f"数据移动失败：{exc}\n应用将继续使用原位置。",
            )
            try:
                self.db.connect()
                self.reload()
            except Exception:
                pass
            return
        QMessageBox.information(self, "设置已保存", "数据位置已更改，请重启应用以生效。")
        self.quit_requested.emit()

    def on_thresholds_changed(self, important: int, unimportant: int) -> None:
        """F6.4：修改双阈值后立即全量重算。"""
        self.thresholds = Thresholds(important=int(important), unimportant=int(unimportant))
        self.db.set_setting(config.KEY_THRESHOLD_IMPORTANT, self.thresholds.important)
        self.db.set_setting(config.KEY_THRESHOLD_UNIMPORTANT, self.thresholds.unimportant)
        self.reload()

    def on_default_reminder_changed(self, rule: str, offset: int) -> None:
        """F26.3：持久化全局默认提醒联动规则与提前量。"""
        self.db.set_setting(config.KEY_DEFAULT_REMINDER_RULE, rule)
        self.db.set_setting(config.KEY_DEFAULT_REMINDER_OFFSET, offset)

    def _prompt_set_due_date(self, task_id: str) -> None:
        """F6.6：为待安排任务快速设置截止日期。"""
        task = self._find(task_id)
        if not task:
            return
        text, ok = QInputDialog.getText(
            self, "设置截止日期", "输入日期（YYYY-MM-DD）", text=self.today.isoformat()
        )
        if not ok or not text.strip():
            return
        try:
            new_date = date.fromisoformat(text.strip())
        except ValueError:
            QMessageBox.warning(self, "日期无效", "请输入形如 2026-09-01 的日期")
            return
        task.due_date = new_date
        self._save(task)
        self.reload()

    def request_quit(self) -> None:
        self._save_geometry()
        self.quit_requested.emit()

    # ================================================================ 导入导出

    def export_data(self) -> None:
        """F10.3 / F10.6：导出为 JSON。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出数据", f"quadrant-todo-{date.today().isoformat()}.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        payload = {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exportedAt": datetime.now().isoformat(),
            "settings": {
                "urgency_threshold_important": self.thresholds.important,
                "urgency_threshold_unimportant": self.thresholds.unimportant,
            },
            "tasks": [task.to_export() for task in self.db.all_tasks()],
            "stickies": [note.to_export() for note in self.db.get_stickies()],
            "articles": [a.to_export() for a in self.db.get_articles()],
            "articleTags": [
                {"articleId": a.id, "tagId": tid}
                for a in self.db.get_articles()
                for tid in self.db.get_article_tag_ids(a.id)
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.db.log_event("data_export")
        QMessageBox.information(self, "导出完成", f"已导出到\n{path}")

    def export_article_markdown(self, article_id: str) -> None:
        """单篇文章导出为 Markdown（零依赖，含标签）。"""
        article = next((a for a in self.db.get_articles() if a.id == article_id), None)
        if article is None:
            return
        tag_names = self.db.get_article_tag_names(article_id)
        safe = re.sub(r'[\\/:*?"<>|]', "_", article.title).strip()
        default_name = (safe or article.created_at.strftime("%Y%m%d")) + ".md"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出文章", default_name, "Markdown 文件 (*.md)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(article.to_markdown(tag_names))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.db.log_event("article_export")
        QMessageBox.information(self, "导出完成", f"已导出到\n{path}")

    def import_data(self) -> None:
        """F10.4 / F10.6：导入 JSON —— 增量合并（2026-09-11 起不再全量替换）。

        不清空现有数据。逐条按 id 比对：
        * 本地无此 id        → 新增
        * 同 id 且完全一致   → 覆盖（等价无变化，重复导入同一文件幂等）
        * 同 id 但数据不同   → 换新 id 追加，本地原记录保留
        """
        path, _ = QFileDialog.getOpenFileName(self, "导入数据", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "导入失败", f"文件无法解析：{exc}")
            return

        if payload.get("format") != EXPORT_FORMAT:
            QMessageBox.warning(self, "导入失败", "文件格式不匹配，不是本应用的导出文件")
            return
        if payload.get("version") != EXPORT_VERSION:
            QMessageBox.warning(
                self, "导入失败", f"文件版本为 {payload.get('version')}，当前仅支持版本 {EXPORT_VERSION}"
            )
            return

        reply = QMessageBox.question(
            self, "导入数据",
            "将以「增量」方式导入，不会清空现有数据：\n"
            "· 完全相同的记录 → 覆盖（重复导入不产生副本）\n"
            "· 有差异的记录 → 作为新记录追加，原记录保留\n\n"
            "确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # ---- 任务（增量）
        existing_tasks = {t.id: t for t in self.db.all_tasks()}
        for item in payload.get("tasks", []):
            self.db.save_task(_merge_for_import(Task.from_export(item), existing_tasks))

        # ---- 便签（增量）
        existing_stickies = {n.id: n for n in self.db.get_stickies()}
        for item in payload.get("stickies", []):
            self.db.save_sticky(_merge_for_import(StickyNote.from_export(item), existing_stickies))

        # ---- 文章（增量）；若因内容不同而另存为新 id，其标签关联要跟着映射到新 id
        existing_articles = {a.id: a for a in self.db.get_articles()}
        article_id_map: dict[str, str] = {}
        for item in payload.get("articles", []):
            obj = Article.from_export(item)
            old_id = obj.id
            obj = _merge_for_import(obj, existing_articles)
            article_id_map[old_id] = obj.id
            self.db.save_article(obj)

        # ---- 文章标签：与本地已有标签取并集，不做整体替换
        merged_tags: dict[str, set[str]] = {}
        for pair in payload.get("articleTags", []):
            aid = pair.get("articleId")
            tid = pair.get("tagId")
            if aid and tid:
                merged_tags.setdefault(article_id_map.get(aid, aid), set()).add(tid)
        for aid, tids in merged_tags.items():
            self.db.set_article_tags(aid, list(set(self.db.get_article_tag_ids(aid)) | tids))
        settings = payload.get("settings") or {}
        if "urgency_threshold_important" in settings:
            self.thresholds = Thresholds(
                important=int(settings["urgency_threshold_important"]),
                unimportant=int(settings.get(
                    "urgency_threshold_unimportant",
                    settings["urgency_threshold_important"],
                )),
            )
        elif "urgency_threshold" in settings:  # 兼容旧版导出
            value = int(settings["urgency_threshold"])
            self.thresholds = Thresholds(important=value, unimportant=value)
        self.db.set_setting(config.KEY_THRESHOLD_IMPORTANT, self.thresholds.important)
        self.db.set_setting(config.KEY_THRESHOLD_UNIMPORTANT, self.thresholds.unimportant)
        self.db.log_event("data_import")
        self.reload()
        QMessageBox.information(
            self, "导入完成",
            "已按增量方式导入（原有数据保留）。\n"
            f"当前共：任务 {len(self.db.all_tasks())} 条、"
            f"便签 {len(self.db.get_stickies())} 条、"
            f"文章 {len(self.db.get_articles())} 篇。",
        )

    # ================================================================ 选中与快捷键

    def _ordered_ids(self) -> list[str]:
        """当前视图中的任务顺序，供上下键导航使用。"""
        if self.current_view == "board":
            grouped: dict[str, list[Task]] = {q.value: [] for q in QUADRANT_ORDER}
            for task in self.tasks:
                if task.is_closed:
                    continue
                grouped[task.quadrant(self.today, self.thresholds).value].append(task)
            ids: list[str] = []
            for quadrant in QUADRANT_ORDER:
                ids.extend(t.id for t in grouped[quadrant.value])
            return ids

        if self.current_view == "daily":
            grouped = {q.value: [] for q in QUADRANT_ORDER}
            for task in self.tasks:
                if task.is_closed or not task.in_daily_todo(self.today):
                    continue
                grouped[task.quadrant(self.today, self.thresholds).value].append(task)
            ids = []
            for quadrant in QUADRANT_ORDER:
                ids.extend(t.id for t in grouped[quadrant.value])
            return ids

        if self.current_view == "period":
            from .recurrence import period_range

            start, end = period_range(self.current_period_kind, self.period_view.anchor)
            grouped = {q.value: [] for q in QUADRANT_ORDER}
            for task in self.tasks:
                if task.is_closed or task.due_date is None:
                    continue
                if start <= task.due_date < end:
                    grouped[task.quadrant(self.today, self.thresholds).value].append(task)
            ids = []
            for quadrant in QUADRANT_ORDER:
                ids.extend(t.id for t in grouped[quadrant.value])
            return ids

        if self.current_view == "calendar":
            return []

        if self.current_view == "search":
            if not self.search_keyword:
                return []
            return [t.id for t in self.db.search_tasks(self.search_keyword, self.search_include_closed)]

        if self.current_view == "tags":
            if not self.active_tag_ids:
                return [t.id for t in self.tasks if not t.is_closed]
            hit_ids = set(self.db.tasks_with_all_tags(sorted(self.active_tag_ids)))
            return [t.id for t in self.tasks if t.id in hit_ids and not t.is_closed]

        # 便签 / 统计 / 热力图不含任务条目
        if self.current_view in ("sticky", "stats", "heatmap"):
            return []

        return [t.id for t in self.tasks if t.due_date is None and not t.is_closed]

    def _cycle_selection(self, delta: int) -> None:
        ids = self._ordered_ids()
        if not ids:
            return
        if self.selected_id in ids:
            index = (ids.index(self.selected_id) + delta) % len(ids)
        else:
            index = 0 if delta > 0 else len(ids) - 1
        self.on_task_selected(ids[index])

    def _act_on_selection(self, handler) -> None:
        if self.selected_id:
            handler(self.selected_id)

    def _activate_task(self, task_id: str) -> None:
        """列表聚焦时按回车：选中并聚焦详情（PRD F14）。"""
        if task_id != self.selected_id:
            self.selected_id = task_id
            self.refresh_views()
        self._focus_detail()

    def _focus_detail(self) -> None:
        if self.selected_id:
            self.detail.title_edit.setFocus()
            self.detail.title_edit.selectAll()

    def _rename_selection(self) -> None:
        self._focus_detail()

    def _on_escape(self) -> None:
        # Esc 优先退出搜索（F22.5），其次才是取消选中
        if self.current_view == "search" or self.search_keyword:
            self._clear_search()
            return
        if self.selected_id:
            self.selected_id = None
            self.refresh_views()

    # ================================================================ 窗口

    def _restore_geometry(self) -> None:
        """F11.2 / F11.3：恢复窗口尺寸与位置；双击 exe 打开默认全屏（最大化）。

        以 window_state 作为「是否默认全屏」的判断依据：
        - 无状态记录（首次启动 / 旧版本升级上来）：默认全屏；
        - 记录为 maximized：恢复几何后最大化；
        - 记录为 normal：恢复几何（窗记忆生效）。
        这样在已有旧几何数据的情况下也能默认全屏，同时尊重用户后续手动还原。
        """
        state = self.db.get_setting(config.KEY_WINDOW_STATE)
        geom = self.db.get_setting(config.KEY_GEOMETRY)

        def _apply_maximized() -> None:
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

        # 无状态记录：默认全屏
        if state is None:
            _apply_maximized()
            return

        # 有记录但无有效几何：直接全屏/默认
        if not geom:
            if state == "maximized":
                _apply_maximized()
            return

        try:
            values = [int(part) for part in geom.split(",")]
            if len(values) != 4:
                if state == "maximized":
                    _apply_maximized()
                return
            x, y, width, height = values
            screen = self.screen().availableGeometry()
            if not screen.intersects(QRect(x, y, width, height)):
                if state == "maximized":
                    _apply_maximized()
                return
            self.setGeometry(x, y, width, height)
        except (ValueError, AttributeError):
            if state == "maximized":
                _apply_maximized()
            return

        if state == "maximized":
            _apply_maximized()

    def _save_geometry(self) -> None:
        geometry = self.geometry()
        self.db.set_setting(
            config.KEY_GEOMETRY,
            f"{geometry.x()},{geometry.y()},{geometry.width()},{geometry.height()}",
        )
        self.db.set_setting(
            config.KEY_WINDOW_STATE,
            "maximized" if self.isMaximized() else "normal",
        )

    def closeEvent(self, event) -> None:
        """F8.2：关闭窗口只隐藏，进程继续常驻托盘。"""
        self._save_geometry()
        event.ignore()
        self.hide()

    def _dismiss_onboarding(self) -> None:
        self.onboarding.setVisible(False)
        self.db.set_setting(config.KEY_ONBOARDING_DONE, 1)

    def apply_onboarding_state(self) -> None:
        """F1.13：首次启动（无任务）时展示引导条。"""
        done = self.db.get_setting(config.KEY_ONBOARDING_DONE)
        has_tasks = bool(self.tasks)
        self.onboarding.setVisible(not done and not has_tasks)

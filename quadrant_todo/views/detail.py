"""任务详情面板（PRD F5）。

F5.9  字段可编辑      F5.10 控件规格      F5.11 即时保存（无保存按钮）
F5.12 保存失败保留输入  F5.8  已完成时隐藏「开始」「加入今日」
F3.5  显示象限判定依据
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, Signal, QTimer
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import ESTIMATE_PRESETS, REMINDER_RULE_NONE, REMINDER_RULE_MANUAL, REMINDER_RULE_RELATIVE
from ..models import Subtask, Tag, Task
from ..quadrant import QUADRANT_LABELS, QUADRANT_ORDER, Quadrant, format_estimate
from ..recurrence import compute_relative_reminder, natural_to_rrule, rrule_label, resolve_reminder
from .common import clear_layout


class _TitleEdit(QPlainTextEdit):
    """标题编辑框：固定 3 行高、自动换行，长标题完整可见（2026-09-03 用户反馈）。

    回车 = 提交并失焦（不插入换行，标题保持单行语义）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("任务标题")
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 3 行高度：行距 × 3 + 上下内边距
        self.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 12)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.clearFocus()
            return
        super().keyPressEvent(event)


class _DueCalendarWidget(QCalendarWidget):
    """截止日期日历弹窗（PRD F5.10 修正）。

    未设置截止日期时，due_edit 处于最小日期并展示「未设置」。
    原生弹窗会默认选中该最小日期（1752 年），导致打开日历落在远古。
    此处在未设置状态下将选中日期定位到今天，方便快速选择。
    """

    def __init__(self, date_edit: "QDateEdit", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._date_edit = date_edit

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._date_edit.date() == self._date_edit.minimumDate():
            self.setSelectedDate(QDate.currentDate())

ESTIMATE_CUSTOM_INDEX = -1


class DetailPanel(QFrame):
    """右侧常驻详情面板。"""

    #: 字段变更：(任务 id, 字段名, 新值)
    field_changed = Signal(str, str, object)
    action_complete = Signal(str)
    action_start = Signal(str)
    action_today = Signal(str)
    action_abandon = Signal(str)
    action_delete = Signal(str)
    action_restore = Signal(str)
    # v2.1 新增信号
    tags_changed = Signal(str, list)          # (task_id, [tag_id,...]) 标签集合整体替换
    tag_add_requested = Signal(str, str)      # (task_id, 新标签名) 新建或复用
    subtask_add = Signal(str, str)            # (task_id, 标题)
    subtask_toggle = Signal(str, str, bool)   # (task_id, subtask_id, done)
    subtask_delete = Signal(str, str)         # (task_id, subtask_id)
    pomodoro_requested = Signal(str)          # (task_id) 开始番茄钟

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.task: Task | None = None
        self._loading = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(320)
        self._build_ui()
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self._commit_note)
        self._pending_note: str | None = None

        # 标题防抖提交（与备注同模式；回车失焦也会立即提交）
        self._title_timer = QTimer(self)
        self._title_timer.setSingleShot(True)
        self._title_timer.timeout.connect(self._commit_title)
        self._pending_title: str | None = None
        self.show_empty()

    # ---------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 14, 14, 14)
        self.layout.setSpacing(10)

        self.empty_label = QLabel("选择任务查看详情")
        self.empty_label.setProperty("role", "empty-hint")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.empty_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.layout.addWidget(self.scroll)

        self.form = QWidget()
        form = QVBoxLayout(self.form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        # 标题（多行编辑框：固定 3 行 + 自动换行，长标题完整可见）
        self.title_edit = _TitleEdit()
        self.title_edit.textChanged.connect(self._on_title_text_changed)
        self.title_edit.installEventFilter(self)
        form.addWidget(self._row("标题", self.title_edit))

        # 象限
        self.quadrant_combo = QComboBox()
        self.quadrant_combo.addItem("自动（按规则计算）", None)
        for quadrant in QUADRANT_ORDER:
            self.quadrant_combo.addItem(f"{quadrant.value} {QUADRANT_LABELS[quadrant]}", quadrant.value)
        self.quadrant_combo.currentIndexChanged.connect(self._on_quadrant_changed)
        form.addWidget(self._row("象限", self.quadrant_combo))

        self.hint_label = QLabel()
        self.hint_label.setProperty("role", "meta")
        self.hint_label.setWordWrap(True)
        form.addWidget(self.hint_label)

        # 截止日期
        date_widget = QWidget()
        date_layout = QHBoxLayout(date_widget)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.setSpacing(4)
        self.due_edit = QDateEdit()
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("yyyy-MM-dd")
        self.due_edit.setSpecialValueText("未设置")
        self.due_edit.setMinimumDate(QDateEdit().minimumDate())
        self.due_edit.setCalendarWidget(_DueCalendarWidget(self.due_edit))
        self.due_edit.dateChanged.connect(self._on_due_changed)
        date_layout.addWidget(self.due_edit)
        self.due_clear_btn = QPushButton("清除")
        self.due_clear_btn.setFlat(True)
        self.due_clear_btn.clicked.connect(self._on_due_cleared)
        date_layout.addWidget(self.due_clear_btn)
        form.addWidget(self._row("截止日期", date_widget))

        # 预计耗时
        self.estimate_combo = QComboBox()
        for minutes in ESTIMATE_PRESETS:
            self.estimate_combo.addItem(format_estimate(minutes), minutes)
        self.estimate_combo.addItem("自定义…", ESTIMATE_CUSTOM_INDEX)
        self.estimate_combo.addItem("未设置", 0)
        self.estimate_combo.currentIndexChanged.connect(self._on_estimate_changed)
        form.addWidget(self._row("预计耗时", self.estimate_combo))

        # 提醒联动（PRD F26.1）
        self.reminder_rule_combo = QComboBox()
        self.reminder_rule_combo.addItem("无提醒", "none")
        self.reminder_rule_combo.addItem("手动（绝对值）", "manual")
        self.reminder_rule_combo.addItem("相对截止日期", "relative")
        self.reminder_rule_combo.currentIndexChanged.connect(self._on_reminder_rule_changed)
        form.addWidget(self._row("提醒规则", self.reminder_rule_combo))

        # 手动绝对值
        self.reminder_widget = QWidget()
        remind_layout = QHBoxLayout(self.reminder_widget)
        remind_layout.setContentsMargins(0, 0, 0, 0)
        remind_layout.setSpacing(4)
        self.reminder_edit = QDateTimeEdit()
        self.reminder_edit.setCalendarPopup(True)
        self.reminder_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.reminder_edit.setSpecialValueText("未设置")
        self.reminder_edit.dateTimeChanged.connect(self._on_reminder_changed)
        remind_layout.addWidget(self.reminder_edit)
        self.reminder_clear_btn = QPushButton("清除")
        self.reminder_clear_btn.setFlat(True)
        self.reminder_clear_btn.clicked.connect(self._on_reminder_cleared)
        remind_layout.addWidget(self.reminder_clear_btn)
        form.addWidget(self._row("提醒时间", self.reminder_widget))

        # 相对截止日期
        self.reminder_offset_widget = QWidget()
        offset_layout = QHBoxLayout(self.reminder_offset_widget)
        offset_layout.setContentsMargins(0, 0, 0, 0)
        offset_layout.setSpacing(4)
        self.reminder_offset_spin = QSpinBox()
        self.reminder_offset_spin.setRange(0, 60 * 24 * 30)  # 最长提前 30 天
        self.reminder_offset_spin.setSuffix(" 分钟前")
        self.reminder_offset_spin.valueChanged.connect(self._on_reminder_offset_changed)
        offset_layout.addWidget(self.reminder_offset_spin)
        self.reminder_offset_preview = QLabel()
        self.reminder_offset_preview.setProperty("role", "meta")
        offset_layout.addWidget(self.reminder_offset_preview, 1)
        form.addWidget(self._row("提前量", self.reminder_offset_widget))

        # 周期（PRD F16.1，v2.0 启用）
        self.cycle_combo = QComboBox()
        self.cycle_combo.addItem("不重复", "none")
        self.cycle_combo.addItem("每天", "daily")
        self.cycle_combo.addItem("每周", "weekly")
        self.cycle_combo.addItem("每月", "monthly")
        self.cycle_combo.addItem("自定义", "custom")
        self.cycle_combo.currentIndexChanged.connect(self._on_cycle_changed)
        form.addWidget(self._row("周期", self.cycle_combo))

        # 自定义周期规则（仅 custom 显示，PRD F16.6）
        self.custom_rule_widget = QWidget()
        cr_layout = QVBoxLayout(self.custom_rule_widget)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.setSpacing(2)
        self.custom_rule_edit = QLineEdit()
        self.custom_rule_edit.setPlaceholderText("如：每工作日 / 每2周 / 每月15日")
        self.custom_rule_edit.editingFinished.connect(self._on_custom_rule_changed)
        cr_layout.addWidget(self.custom_rule_edit)
        self.custom_rule_label = QLabel()
        self.custom_rule_label.setProperty("role", "meta")
        cr_layout.addWidget(self.custom_rule_label)
        form.addWidget(self.custom_rule_widget)

        # 子任务 / 清单（PRD F20）—— 置于备注之前（2026-09-10 用户要求）
        form.addWidget(self._build_subtasks_ui())

        # 备注
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("备注")
        self.note_edit.setMinimumHeight(120)
        self.note_edit.setToolTip("即时保存，最多 2000 字")
        self.note_edit.textChanged.connect(self._on_note_changed)
        form.addWidget(self._row("备注", self.note_edit))

        # 标签（PRD F21）
        form.addWidget(self._build_tags_ui())

        # 元信息
        self.meta_label = QLabel()
        self.meta_label.setProperty("role", "meta")
        self.meta_label.setWordWrap(True)
        form.addWidget(self.meta_label)

        form.addStretch(1)

        # 操作区
        actions = QVBoxLayout()
        actions.setSpacing(6)
        self.complete_btn = QPushButton("标记完成")
        self.complete_btn.clicked.connect(lambda: self._emit_id(self.action_complete))
        actions.addWidget(self.complete_btn)

        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(lambda: self._emit_id(self.action_start))
        actions.addWidget(self.start_btn)

        self.today_btn = QPushButton("加入今日")
        self.today_btn.clicked.connect(lambda: self._emit_id(self.action_today))
        actions.addWidget(self.today_btn)

        # 番茄钟（PRD F23.1）：不触碰任务状态机
        self.pomodoro_btn = QPushButton("开始番茄钟")
        self.pomodoro_btn.clicked.connect(lambda: self._emit_id(self.pomodoro_requested))
        actions.addWidget(self.pomodoro_btn)

        row = QHBoxLayout()
        self.abandon_btn = QPushButton("放弃")
        self.abandon_btn.clicked.connect(lambda: self._emit_id(self.action_abandon))
        row.addWidget(self.abandon_btn)

        self.restore_btn = QPushButton("恢复任务")
        self.restore_btn.clicked.connect(lambda: self._emit_id(self.action_restore))
        row.addWidget(self.restore_btn)
        actions.addLayout(row)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(lambda: self._emit_id(self.action_delete))
        actions.addWidget(self.delete_btn)

        form.addLayout(actions)
        self.scroll.setWidget(self.form)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        self.layout.addWidget(self.error_label)

    # ------------------------------------------------- 标签区（PRD F21）

    def _build_tags_ui(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        caption = QLabel("标签")
        caption.setProperty("role", "field-label")
        layout.addWidget(caption)

        self.tag_chip_widget = QWidget()
        self.tag_chip_row = QHBoxLayout(self.tag_chip_widget)
        self.tag_chip_row.setContentsMargins(0, 0, 0, 0)
        self.tag_chip_row.setSpacing(4)
        layout.addWidget(self.tag_chip_widget)

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("输入标签名后回车添加")
        self.tag_input.returnPressed.connect(self._on_tag_committed)
        layout.addWidget(self.tag_input)
        return container

    def _load_tags(self, all_tags: list[Tag], active_ids: list[str]) -> None:
        """渲染已挂载标签 chip（点击 = 移除），F21.1 / F21.2。"""
        self._all_tags = list(all_tags)
        self._active_tag_ids = list(active_ids)

        clear_layout(self.tag_chip_row)
        active = set(active_ids)
        for tag in all_tags:
            if tag.id not in active:
                continue
            chip = QPushButton(tag.name)
            chip.setFlat(True)
            chip.setProperty("tagcolor", tag.color)
            chip.setToolTip("点击移除标签")
            chip.clicked.connect(lambda _c=False, tid=tag.id: self._on_tag_removed(tid))
            self.tag_chip_row.addWidget(chip)
        self.tag_chip_row.addStretch(1)

    def _on_tag_committed(self) -> None:
        if self._loading or not self.task:
            return
        name = self.tag_input.text().strip()
        if not name:
            return
        self.tag_input.clear()
        self.tag_add_requested.emit(self.task.id, name)

    def _on_tag_removed(self, tag_id: str) -> None:
        if self._loading or not self.task:
            return
        remaining = [tid for tid in getattr(self, "_active_tag_ids", []) if tid != tag_id]
        self.tags_changed.emit(self.task.id, remaining)

    # ------------------------------------------------- 子任务区（PRD F20）

    def _build_subtasks_ui(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        caption = QLabel("子任务")
        caption.setProperty("role", "field-label")
        head.addWidget(caption)
        head.addStretch(1)
        self.subtask_progress = QLabel()
        self.subtask_progress.setProperty("role", "meta")
        head.addWidget(self.subtask_progress)
        layout.addLayout(head)

        self.subtask_list = QVBoxLayout()
        self.subtask_list.setSpacing(2)
        layout.addLayout(self.subtask_list)

        self.subtask_input = QLineEdit()
        self.subtask_input.setPlaceholderText("添加子任务后回车")
        self.subtask_input.returnPressed.connect(self._on_subtask_committed)
        layout.addWidget(self.subtask_input)
        return container

    def _load_subtasks(self, subtasks: list[Subtask]) -> None:
        """渲染子任务清单 + 进度标识（F20.3）。"""
        clear_layout(self.subtask_list)
        done = sum(1 for s in subtasks if s.done)
        for sub in subtasks:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)

            box = QCheckBox()
            box.setChecked(bool(sub.done))
            box.stateChanged.connect(
                lambda state, sid=sub.id: self._on_subtask_toggled(sid, bool(state))
            )
            h.addWidget(box)

            label = QLabel(sub.title)
            label.setProperty("role", "subtask-done" if sub.done else "subtask")
            self._polish(label)
            h.addWidget(label, 1)

            del_btn = QPushButton("×")
            del_btn.setFlat(True)
            del_btn.setFixedWidth(24)
            del_btn.clicked.connect(lambda _c=False, sid=sub.id: self._on_subtask_deleted(sid))
            h.addWidget(del_btn)

            self.subtask_list.addWidget(row)

        total = len(subtasks)
        if total:
            # 完成约束提示：存在未完成子任务时，主任务不能标记为完成
            suffix = "（须全部完成才能标记主任务完成）" if done < total else ""
            self.subtask_progress.setText(f"{done}/{total}{suffix}")
        else:
            self.subtask_progress.setText("")

    def _on_subtask_committed(self) -> None:
        if self._loading or not self.task:
            return
        title = self.subtask_input.text().strip()
        if not title:
            return
        self.subtask_input.clear()
        self.subtask_add.emit(self.task.id, title)

    def _on_subtask_toggled(self, subtask_id: str, done: bool) -> None:
        if self._loading or not self.task:
            return
        self.subtask_toggle.emit(self.task.id, subtask_id, done)

    def _on_subtask_deleted(self, subtask_id: str) -> None:
        if self._loading or not self.task:
            return
        self.subtask_delete.emit(self.task.id, subtask_id)

    @staticmethod
    def _polish(widget: QWidget) -> None:
        """动态属性变更后强制重刷样式。"""
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _row(self, label: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        caption = QLabel(label)
        caption.setProperty("role", "field-label")
        layout.addWidget(caption)
        layout.addWidget(widget)
        return container

    # ---------------------------------------------------------------- 渲染

    def show_empty(self) -> None:
        self.task = None
        self.empty_label.setVisible(True)
        self.scroll.setVisible(False)
        self.error_label.setVisible(False)
        self._pending_note = None
        self._note_timer.stop()
        self._pending_title = None
        self._title_timer.stop()

    def show_task(
        self,
        task: Task,
        today: date,
        thresholds,
        all_tags: list[Tag] | None = None,
        active_tag_ids: list[str] | None = None,
        subtasks: list[Subtask] | None = None,
    ) -> None:
        self.task = task
        self._today = today
        self._thresholds = thresholds
        self.empty_label.setVisible(False)
        self.scroll.setVisible(True)
        self.error_label.setVisible(False)
        self._pending_note = None
        self._note_timer.stop()
        self._pending_title = None
        self._title_timer.stop()
        self._loading = True

        self.title_edit.setPlainText(task.title)
        self.note_edit.setPlainText(task.note)
        self._load_quadrant(task)
        self._load_due(task)
        self._load_estimate(task)
        self._load_cycle(task)
        self._load_reminder_rule(task)
        # v2.1：标签与子任务由 app 层提供（避免面板直连数据库）
        self._load_tags(all_tags or [], active_tag_ids or [])
        self._load_subtasks(subtasks or [])
        self._update_hint(task)
        self._update_meta(task)
        self._update_actions(task)

        self._loading = False

    def _load_quadrant(self, task: Task) -> None:
        index = self.quadrant_combo.findData(task.locked_quadrant)
        self.quadrant_combo.setCurrentIndex(max(index, 0))

    def _load_due(self, task: Task) -> None:
        from PySide6.QtCore import QDate

        if task.due_date:
            self.due_edit.setDate(QDate(task.due_date.year, task.due_date.month, task.due_date.day))
        else:
            self.due_edit.setDate(self.due_edit.minimumDate())

    def _load_estimate(self, task: Task) -> None:
        if not task.estimate:
            self.estimate_combo.setCurrentIndex(self.estimate_combo.count() - 1)  # 未设置
            return
        index = self.estimate_combo.findData(task.estimate)
        if index >= 0:
            self.estimate_combo.setCurrentIndex(index)
        else:
            self.estimate_combo.insertItem(
                self.estimate_combo.count() - 1, format_estimate(task.estimate), task.estimate
            )
            self.estimate_combo.setCurrentIndex(self.estimate_combo.count() - 2)

    def _load_cycle(self, task: Task) -> None:
        index = self.cycle_combo.findData(task.cycle or "none")
        self.cycle_combo.setCurrentIndex(max(index, 0))
        is_custom = (task.cycle or "none") == "custom"
        self.custom_rule_widget.setVisible(is_custom)
        if is_custom and task.custom_rule:
            self.custom_rule_edit.setText(rrule_label(task.custom_rule))
        elif is_custom:
            self.custom_rule_edit.clear()
        self.custom_rule_label.clear()

    def _today_default(self) -> QDateTime:
        """手动提醒的默认时间：当天下一个整点（不早于现在），避免默认落在远古最小日期。"""
        now = QDateTime.currentDateTime()
        hour = now.time().hour()
        if hour >= 23:
            return QDateTime(now.date(), QTime(23, 59))
        return QDateTime(now.date(), QTime(hour + 1, 0))

    def _load_reminder_rule(self, task: Task) -> None:
        rule = task.reminder_rule or REMINDER_RULE_NONE
        idx = self.reminder_rule_combo.findData(rule)
        self.reminder_rule_combo.setCurrentIndex(max(idx, 0))

        if task.reminder:
            self.reminder_edit.setDateTime(
                QDateTime(
                    QDate(task.reminder.year, task.reminder.month, task.reminder.day),
                    QTime(task.reminder.hour, task.reminder.minute),
                )
            )
        else:
            # 无提醒时默认显示当天（而非 1752 年最小日期），便于直接微调
            self.reminder_edit.setDateTime(self._today_default())

        self.reminder_offset_spin.setValue(int(task.reminder_offset or 0))
        self._update_reminder_visibility(task)

    def _update_reminder_visibility(self, task: Task | None = None) -> None:
        """根据提醒规则切换手动/相对输入区（PRD F26.1 / F26.2）。"""
        rule = self.reminder_rule_combo.currentData() or REMINDER_RULE_NONE
        self.reminder_widget.setVisible(rule == REMINDER_RULE_MANUAL)
        self.reminder_offset_widget.setVisible(rule == REMINDER_RULE_RELATIVE)
        # 相对模式预览
        if rule == REMINDER_RULE_RELATIVE and task is not None:
            preview = resolve_reminder(
                rule, task.due_date, task.reminder, int(self.reminder_offset_spin.value() or 0)
            )
            if preview:
                self.reminder_offset_preview.setText(f"→ {preview.strftime('%Y-%m-%d %H:%M')}")
            else:
                self.reminder_offset_preview.setText("无截止日期，暂不提醒")
        else:
            self.reminder_offset_preview.clear()

    def _update_hint(self, task: Task) -> None:
        """F3.5：显示象限判定依据。"""
        quadrant = task.quadrant(self._today, self._thresholds)
        if task.locked_quadrant:
            suggested = task.suggested(self._today, self._thresholds)
            if suggested.value != task.locked_quadrant:
                self.hint_label.setText(
                    f"已锁定在 {task.locked_quadrant}，按规则应落在 {suggested.value}。"
                )
            else:
                self.hint_label.setText(f"已锁定在 {task.locked_quadrant}，与规则一致。")
            return

        urgency = "紧急" if task.quadrant(self._today, self._thresholds) in (
            Quadrant.Q1, Quadrant.Q3
        ) else "不紧急"
        important = "重要" if task.importance else "不重要"
        reason = f"因{important}且{urgency}，落在 {quadrant.value}"
        if task.due_date is not None:
            delta = task.days_until(self._today)
            if delta is not None and delta < 0:
                reason += f"（截止日期已逾期 {abs(delta)} 天）"
            elif delta == 0:
                reason += "（截止日期为今天）"
            else:
                reason += f"（截止日期还剩 {delta} 天）"
        else:
            reason += "（未设置截止日期）"
        self.hint_label.setText(reason)

    def _update_meta(self, task: Task) -> None:
        from ..models import STATUS_LABELS

        lines = [f"状态：{STATUS_LABELS.get(task.status, task.status)}"]
        lines.append(f"创建于 {task.created_at.strftime('%Y-%m-%d %H:%M')}")
        if task.completed_at:
            lines.append(f"完成于 {task.completed_at.strftime('%Y-%m-%d %H:%M')}")
        self.meta_label.setText("\n".join(lines))

    def _update_actions(self, task: Task) -> None:
        """F5.8：已完成时隐藏「开始」与「加入今日」。"""
        closed = task.status == "done"
        self.start_btn.setVisible(not closed and task.status == "todo")
        self.today_btn.setVisible(not task.is_closed)
        self.today_btn.setText("移出今日" if task.today_flag else "加入今日")
        self.complete_btn.setText("取消完成" if closed else "标记完成")
        # 番茄钟对已完成任务无意义
        self.pomodoro_btn.setVisible(not task.is_closed)
        self.restore_btn.setVisible(task.status == "abandoned")
        self.abandon_btn.setVisible(task.status != "abandoned")

    def show_error(self, message: str) -> None:
        """F5.12：保存失败时提示并保留用户输入。"""
        self.error_label.setText(message)
        self.error_label.setVisible(True)

    # ---------------------------------------------------------------- 交互

    def _emit_id(self, signal) -> None:
        if self.task:
            signal.emit(self.task.id)

    def eventFilter(self, obj, event) -> bool:
        """标题框失焦时立即提交，不等 600ms 防抖（避免切换任务丢改动）。"""
        if obj is self.title_edit and event.type() == event.Type.FocusOut:
            if self._pending_title is not None:
                self._title_timer.stop()
                self._commit_title()
        return super().eventFilter(obj, event)

    def _on_title_text_changed(self) -> None:
        """标题输入防抖（600ms）；换行/空白统一折叠为单空格。"""
        if self._loading or not self.task:
            return
        self._pending_title = self.title_edit.toPlainText()
        self._title_timer.stop()
        self._title_timer.start(600)

    def _commit_title(self) -> None:
        """防抖到期或失焦后提交标题；与 DB 不一致才触发保存。"""
        if self._loading or not self.task or self._pending_title is None:
            return
        raw = self._pending_title
        self._pending_title = None
        # 标题保持单行语义：折叠所有连续空白（含用户误敲的换行）
        title = " ".join(raw.split()).strip()
        if not title:
            self.show_error("标题不能为空")
            self._loading = True
            self.title_edit.setPlainText(self.task.title)
            self._loading = False
            return
        if title == self.task.title:
            return
        self.field_changed.emit(self.task.id, "title", title)

    def _on_quadrant_changed(self) -> None:
        if self._loading or not self.task:
            return
        value = self.quadrant_combo.currentData()
        if value != self.task.locked_quadrant:
            self.field_changed.emit(self.task.id, "locked_quadrant", value)

    def _on_due_changed(self, value) -> None:
        if self._loading or not self.task:
            return
        # specialValueText 对应最小日期，视为未设置
        if value == self.due_edit.minimumDate():
            if self.task.due_date is not None:
                self.field_changed.emit(self.task.id, "due_date", None)
            return
        new_date = date(value.year(), value.month(), value.day())
        if new_date != self.task.due_date:
            self.field_changed.emit(self.task.id, "due_date", new_date)

    def _on_due_cleared(self) -> None:
        if self._loading or not self.task or self.task.due_date is None:
            return
        self.field_changed.emit(self.task.id, "due_date", None)

    def _on_estimate_changed(self) -> None:
        if self._loading or not self.task:
            return
        minutes = self.estimate_combo.currentData()
        if minutes == ESTIMATE_CUSTOM_INDEX:
            self._prompt_custom_estimate()
            return
        if minutes != self.task.estimate:
            self.field_changed.emit(self.task.id, "estimate", minutes or None)

    def _on_cycle_changed(self) -> None:
        if self._loading or not self.task:
            return
        value = self.cycle_combo.currentData()
        if value == self.task.cycle:
            return
        self.custom_rule_widget.setVisible(value == "custom")
        if value != "custom":
            self.custom_rule_label.clear()
        self.field_changed.emit(self.task.id, "cycle", value)

    def _on_custom_rule_changed(self) -> None:
        if self._loading or not self.task:
            return
        text = self.custom_rule_edit.text().strip()
        if not text:
            self.custom_rule_label.clear()
            return
        rule = natural_to_rrule(text)
        if rule is None:
            self.custom_rule_label.setText("无法识别，请按示例填写（每工作日 / 每2周 / 每月15日）")
            self.custom_rule_label.setProperty("role", "error")
            self.style().unpolish(self.custom_rule_label)
            self.style().polish(self.custom_rule_label)
            return
        self.custom_rule_label.setText(f"解析为：{rrule_label(rule)}")
        self.custom_rule_label.setProperty("role", "meta")
        self.style().unpolish(self.custom_rule_label)
        self.style().polish(self.custom_rule_label)
        self.field_changed.emit(self.task.id, "custom_rule", rule)

    def _on_reminder_rule_changed(self) -> None:
        if self._loading or not self.task:
            return
        rule = self.reminder_rule_combo.currentData()
        if rule == self.task.reminder_rule:
            self._update_reminder_visibility(self.task)
            return
        self._update_reminder_visibility(self.task)
        # 进入手动模式且无提醒：预填当天，便于直接微调（不立即保存）
        if rule == REMINDER_RULE_MANUAL and self.task.reminder is None:
            self.reminder_edit.blockSignals(True)
            self.reminder_edit.setDateTime(self._today_default())
            self.reminder_edit.blockSignals(False)
        self.field_changed.emit(self.task.id, "reminder_rule", rule)

    def _on_reminder_offset_changed(self) -> None:
        if self._loading or not self.task:
            return
        value = self.reminder_offset_spin.value()
        if value == (self.task.reminder_offset or 0):
            return
        self.field_changed.emit(self.task.id, "reminder_offset", value)

    def _prompt_custom_estimate(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        minutes, ok = QInputDialog.getInt(
            self, "自定义预计耗时", "分钟数（1–999）", self.task.estimate or 30, 1, 999, 1
        )
        if ok:
            self.field_changed.emit(self.task.id, "estimate", minutes)
        else:
            self._load_estimate(self.task)

    def _on_reminder_changed(self, value) -> None:
        if self._loading or not self.task:
            return
        if value == self.reminder_edit.minimumDateTime():
            if self.task.reminder is not None:
                self.field_changed.emit(self.task.id, "reminder", None)
            return
        new_dt = datetime(value.date().year(), value.date().month(), value.date().day(),
                          value.time().hour(), value.time().minute())
        if new_dt <= datetime.now():
            # F9.8：不允许设置早于当前时间的提醒
            self.show_error("提醒时间需要晚于现在")
            self._load_reminder_rule(self.task)
            return
        self.field_changed.emit(self.task.id, "reminder", new_dt)

    def _on_reminder_cleared(self) -> None:
        if self._loading or not self.task or self.task.reminder is None:
            return
        self.field_changed.emit(self.task.id, "reminder", None)
        # 清除后回到当天默认值显示，方便再次设置（不保存）
        self.reminder_edit.blockSignals(True)
        self.reminder_edit.setDateTime(self._today_default())
        self.reminder_edit.blockSignals(False)

    def _on_note_changed(self) -> None:
        """备注输入防抖保存：避免逐字触发全量刷新导致输入框失焦/滚动复位。"""
        if self._loading or not self.task:
            return
        note = self.note_edit.toPlainText()
        if len(note) > 2000:
            self.show_error("备注最多 2000 字")
            return
        self.error_label.setVisible(False)
        self._pending_note = note
        self._note_timer.stop()
        self._note_timer.start(600)

    def _commit_note(self) -> None:
        """防抖后提交备注；若与 DB 中一致则不触发保存。"""
        if self._loading or not self.task or self._pending_note is None:
            return
        note = self._pending_note
        self._pending_note = None
        if note != self.task.note:
            self.field_changed.emit(self.task.id, "note", note)

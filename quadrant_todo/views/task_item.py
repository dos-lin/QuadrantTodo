"""任务条目组件（PRD F1.10 任务条目构成）。

布局：
    [✓] [进行中] 标题                        [🔒][建议] (开始|完成|加入今日|删除)
                 今天到期 · 30m

常驻元素：复选框、标题、日期标签、预计耗时、进行中标识、锁定图标、建议徽标
悬浮操作区：主操作按钮 / 加入今日 / 删除（鼠标悬浮或选中时显示，避免挤压标题空间）

主操作按钮随状态切换文案与行为，位置固定在悬浮区第一个（PRD F1.10）：
    待办   → 「开始」  点击切换为进行中
    进行中 → 「完成」  点击标记完成（2026-09-02 用户要求：占用「开始」原位）
    已完成 / 已放弃 → 隐藏
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from .. import theme
from ..models import STATUS_DOING, Task
from ..quadrant import date_status

# 进行中状态的标题配色（PRD 之外，2026-09-02 用户要求）：浅色 / 深色两套，随主题切换。
# 与蓝色系的「进行中」徽标形成对比，让「正在做的任务」在列表里一眼可辨。
DOING_COLORS = {
    "light": "#d93025",  # 红色：进行中
    "dark": "#f28b82",
}

# 日期状态配色（PRD 6.4）：浅色 / 深色两套，随主题切换
DATE_COLORS = {
    "light": {
        "normal": "#8a8f98",   # 灰色：还剩 X 天
        "near": "#d98700",     # 橙色：临近
        "overdue": "#d93025",  # 红色：逾期
    },
    "dark": {
        "normal": "#9aa0a6",
        "near": "#fdd663",
        "overdue": "#f28b82",
    },
}

# 悬浮操作标签配色：常规操作用蓝色（与主题链接色 #1a73e8 一致），删除用红色危险色。
# 仅写简单属性（color/padding/...），hover 背景放父容器 actions 的扁平规则里，
# 因为 Qt QSS 不支持在 widget 内联样式里写 `QLabel:hover { }` 嵌套子规则（会报解析失败）。
_CHIP_STYLE_BLUE = (
    "color: #1a73e8; padding: 1px 6px; border-radius: 4px;"
    " font-size: 12px; background: transparent;"
)
_CHIP_STYLE_RED = (
    "color: #d93025; padding: 1px 6px; border-radius: 4px;"
    " font-size: 12px; background: transparent;"
)


class _MarqueeLabel(QWidget):
    """选中时长标题自动左右滚动（跑马灯）；未选中或文字够宽时静止显示。

    标题颜色通过 QSS/palette 控制，与现有「进行中标红」逻辑保持一致。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = ""
        self._selected = False
        self._offset = 0
        self._direction = 1
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.setToolTip(self._text)

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._step)

    def set_text(self, text: str) -> None:
        self._text = text
        self._offset = 0
        self._direction = 1
        self.setToolTip(text)
        self.update()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0
            self._direction = 1
            self.update()

    def _text_width(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance(self._text)

    def _step(self) -> None:
        if not self._selected or not self._text:
            return
        text_w = self._text_width()
        visible_w = self.width()
        if text_w <= visible_w:
            self._offset = 0
            return
        max_offset = text_w - visible_w
        self._offset += self._direction * 1
        if self._offset >= max_offset:
            self._offset = max_offset
            self._direction = -1
        elif self._offset <= 0:
            self._offset = 0
            self._direction = 1
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(0, fm.height() + 4)

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(40, fm.height() + 4)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        color = self.palette().text().color()
        painter.setPen(color)
        painter.setFont(self.font())
        fm = QFontMetrics(self.font())
        x = -self._offset
        y = (self.height() + fm.ascent() - fm.descent()) // 2
        painter.drawText(x, y, self._text)


class _ChipLabel(QLabel):
    """可点击的小标签按钮（悬浮操作区用）。

    用 QLabel 而非 QPushButton：QLabel 直接绘制文字、紧贴内容，不会像 QPushButton
    那样被全局 QSS 的 padding 把「完成 / 加入今日 / 删除」文字底部裁掉（与「进行中」
    徽标同一渲染路径，显示效果一致）。
    """

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)

    def click(self) -> None:
        """兼容 QPushButton 的点击 API（测试与代码调用均按此约定）。"""
        self.clicked.emit()


class TaskItemWidget(QFrame):
    """单条任务的可视单元。"""

    toggled = Signal(str)            # 勾选状态切换
    started = Signal(str)            # 点击开始
    add_to_today = Signal(str)       # 加入/移出今日
    delete_requested = Signal(str)   # 删除
    clicked = Signal(str)            # 选中

    def __init__(self, task: Task, today: date, threshold: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.task = task
        self.today = today
        self.threshold = threshold
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # 2026-09-16 用户要求条目更紧凑（单行布局）：48→28，一屏能看到更多待办
        self.setMinimumHeight(28)
        self.setAttribute(Qt.WA_Hover, True)
        self._build_ui()
        self._apply_state()

    # ---------------------------------------------------------------- UI 构建

    def _build_ui(self) -> None:
        # 2026-09-16 用户要求「一夜能看到更多待办」：改为单行布局——
        # 日期 / 耗时元信息从第二行移到标题行右端，条目高度 ~38px → ~26px。
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 3, 8, 3)
        root.setSpacing(6)

        self.check = QCheckBox()
        self.check.setStyleSheet("background: transparent;")
        self.check.setToolTip("标记完成 / 取消完成")
        self.check.stateChanged.connect(lambda _: self.toggled.emit(self.task.id))
        root.addWidget(self.check, 0, Qt.AlignVCenter)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.doing_badge = QLabel("进行中")
        self.doing_badge.setProperty("badge", "doing")
        title_row.addWidget(self.doing_badge)

        self.title_label = _MarqueeLabel()
        title_row.addWidget(self.title_label, 1)
        root.addLayout(title_row, 1)

        # 元信息（日期 + 预计耗时）：单行显示，挤压时由标题让位
        self.meta_label = QLabel()
        self.meta_label.setProperty("role", "meta")
        root.addWidget(self.meta_label, 0, Qt.AlignVCenter)

        # 右侧标记区
        self.lock_label = QLabel("锁定")
        self.lock_label.setProperty("badge", "lock")
        self.lock_label.setToolTip("已手动锁定，不再自动迁移")
        root.addWidget(self.lock_label, 0, Qt.AlignVCenter)

        self.hint_label = QLabel()
        self.hint_label.setProperty("badge", "hint")
        root.addWidget(self.hint_label, 0, Qt.AlignVCenter)

        # 悬浮操作区：用 QLabel 小标签（_ChipLabel）替代 QPushButton，避免文字被裁
        self.actions = QWidget()
        # hover 背景用扁平规则放父容器（danger 用字符串属性，确保 [danger='true'] 匹配）
        self.actions.setStyleSheet(
            "background: transparent;"
            "QLabel[role='chip']:hover { background: rgba(26,115,232,0.10); }"
            "QLabel[role='chip'][danger='true']:hover { background: rgba(217,48,37,0.10); }"
        )
        actions_layout = QHBoxLayout(self.actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        # 主操作按钮：待办 → 「开始」，进行中 → 「完成」（同一位置，互斥显示）
        self.primary_btn = _ChipLabel("开始")
        self.primary_btn.setProperty("role", "chip")
        self.primary_btn.setToolTip("切换为进行中")
        self.primary_btn.setStyleSheet(_CHIP_STYLE_BLUE)
        self.primary_btn.clicked.connect(self._on_primary_clicked)
        actions_layout.addWidget(self.primary_btn)

        self.today_btn = _ChipLabel()
        self.today_btn.setProperty("role", "chip")
        self.today_btn.setStyleSheet(_CHIP_STYLE_BLUE)
        self.today_btn.clicked.connect(lambda: self.add_to_today.emit(self.task.id))
        actions_layout.addWidget(self.today_btn)

        self.delete_btn = _ChipLabel("删除")
        self.delete_btn.setProperty("role", "chip")
        self.delete_btn.setProperty("danger", "true")
        self.delete_btn.setStyleSheet(_CHIP_STYLE_RED)
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.task.id))
        actions_layout.addWidget(self.delete_btn)

        self.actions.setVisible(False)
        root.addWidget(self.actions, 0, Qt.AlignVCenter)

    # ---------------------------------------------------------------- 状态渲染

    def _apply_state(self) -> None:
        task = self.task

        self.title_label.set_text(task.title)
        self.title_label.setToolTip(task.title)
        self.check.setChecked(task.status == "done")

        # 已完成 / 已放弃 用删除线弱化
        strike = task.status in ("done", "abandoned")
        font = self.title_label.font()
        font.setStrikeOut(strike)
        self.title_label.setFont(font)
        self.title_label.setEnabled(not strike)

        self.doing_badge.setVisible(task.status == STATUS_DOING)

        # 进行中：标题标红，强化"正在做"的辨识度（已完成/已放弃不标红，保持弱化）
        if task.status == STATUS_DOING and not strike:
            scheme = theme.current_scheme()
            color = DOING_COLORS.get(scheme, DOING_COLORS["light"])
            self.title_label.setStyleSheet(f"color: {color}; background: transparent;")
        else:
            self.title_label.setStyleSheet("background: transparent;")

        # 元信息：日期标签 + 预计耗时。无日期时不显示日期标签（PRD 6.4），
        # 但保留「无截止日期」提示，便于用户识别待安排任务（PRD F6.5）。
        parts = []
        label = task.date_label(self.today, self.threshold)
        if label:
            parts.append(label)
        if task.estimate_label:
            parts.append(task.estimate_label)
        if not parts:
            parts.append("无截止日期")

        scheme_colors = DATE_COLORS.get(theme.current_scheme(), DATE_COLORS["light"])
        color = scheme_colors.get(self._date_status(), scheme_colors["normal"])
        self.meta_label.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
        self.meta_label.setText("  ·  ".join(parts))

        # 锁定图标（PRD F1.7）
        self.lock_label.setVisible(bool(task.locked_quadrant))

        # 建议迁移徽标（PRD F3.4）
        hint = task.needs_migration_hint(self.today, self.threshold)
        if hint:
            self.hint_label.setText(f"建议移至 {hint.value}")
            self.hint_label.setToolTip(
                f"按规则应落在 {hint.value}，但当前已手动锁定在 {task.locked_quadrant}"
            )
        self.hint_label.setVisible(bool(hint))

        # 操作区按钮状态
        # 主操作按钮：待办显示「开始」，进行中在同一位置显示「完成」（2026-09-02）
        doing = task.status == STATUS_DOING
        self.primary_btn.setText("完成" if doing else "开始")
        self.primary_btn.setToolTip("标记完成" if doing else "切换为进行中")
        self.primary_btn.setVisible(task.status in ("todo", STATUS_DOING))
        self.today_btn.setVisible(not task.is_closed)
        # 2026-09-11：以「截止日期是否为今天」判断加入/移出今日
        self.today_btn.setText("移出今日" if task.due_date == self.today else "加入今日")
        self.delete_btn.setVisible(True)

        self.setProperty("selected", False)
        self.setProperty("closed", strike)

    def _date_status(self) -> str:
        thr = self.threshold.for_importance(self.task.importance)
        return date_status(self.task.due_date, self.today, thr)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._refresh_style()
        self.title_label.set_selected(selected)
        self.actions.setVisible(selected or self.underMouse())

    def enterEvent(self, event) -> None:
        self.actions.setVisible(True)
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.property("selected"):
            self.actions.setVisible(False)
        self._refresh_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        # 仅发选中信号，不转发给 QListWidget：选中会触发整页重建（见 board.fill），
        # 若继续把事件交给 QListWidget，它会在重建后的列表上按当前鼠标位置再选一次，
        # 命中位置错位 → 原生高亮落到错误行（用户反馈 2026-09-04）。
        self.clicked.emit(self.task.id)

    def _on_primary_clicked(self) -> None:
        """主操作按钮：待办 → 开始；进行中 → 完成。

        复用同一个按钮位置，避免进行中任务失去「点一下就完成」的快捷入口。
        """
        if self.task.status == STATUS_DOING:
            self.toggled.emit(self.task.id)
        else:
            self.started.emit(self.task.id)

    def _refresh_style(self) -> None:
        """触发 QSS 重新应用（动态属性变化后必须调用）。"""
        self.style().unpolish(self)
        self.style().polish(self)

    def update_task(self, task: Task, today: date, threshold: int) -> None:
        """用新数据刷新条目。"""
        self.task = task
        self.today = today
        self.threshold = threshold
        self._apply_state()

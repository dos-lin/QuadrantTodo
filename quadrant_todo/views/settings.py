"""设置对话框（PRD F6 / F8.7 / F10.3–F10.4 / F13.7 / F25.5）。

页签：通用、热键、数据、关于。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTime, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..config import APP_TITLE, BACKUP_DIR, DATA_DIR, VERSION
from ..hotkey import parse_hotkey
from ..quadrant import DEFAULT_URGENCY_THRESHOLD, URGENCY_THRESHOLD_OPTIONS

BUILD_TIME = datetime.now().strftime("%Y-%m-%d %H:%M")

# 开源仓库地址（2026-09-03 用户要求：设置中展示并支持一键跳转 / 复制）
REPO_URL = "https://github.com/dos-lin/QuadrantTodo.git"


# ---------------------------------------------------------------- 开机启动

def _run_key_path() -> str:
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_autostart_enabled() -> bool:
    """读取注册表判断开机自启（仅 Windows）。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _run_key_path()) as key:
            value, _ = winreg.QueryValueEx(key, APP_TITLE)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """写入/删除注册表开机启动项（PRD F8.7）。失败返回 False。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _run_key_path(), 0, winreg.KEY_WRITE) as key:
            if enabled:
                exe = sys.executable
                if not exe.lower().endswith(("quadranttodo.exe", "pythonw.exe")):
                    # 开发环境下用启动脚本；打包后即为 exe 自身
                    exe = str(Path(sys.argv[0]).resolve())
                winreg.SetValueEx(key, APP_TITLE, 0, winreg.REG_SZ, f'"{exe}"')
            else:
                try:
                    winreg.DeleteValue(key, APP_TITLE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


class SettingsDialog(QDialog):
    """设置对话框。"""

    thresholds_changed = Signal(int, int)
    autostart_changed = Signal(bool)
    default_reminder_changed = Signal(str, int)
    pomodoro_changed = Signal(int)
    hotkey_changed = Signal(bool, str, str)
    export_requested = Signal()
    import_requested = Signal()
    data_dir_change_requested = Signal(str)
    #: 自动备份策略（是否启用, 间隔天数, 时刻 HH:MM:SS, 保留份数）
    backup_changed = Signal(bool, int, str, int)
    #: 立即备份一次
    backup_now_requested = Signal()

    def __init__(self, threshold_important: int, threshold_unimportant: int,
                 default_reminder_rule: str = "none", default_reminder_offset: int = 60,
                 pomodoro_duration: int = 25,
                 hotkey_enabled: bool = False,
                 hotkey_toggle: str = "Ctrl+Alt+Q",
                 hotkey_quickadd: str = "Ctrl+Alt+N",
                 backup_enabled: bool = True,
                 backup_interval_days: int = config.DEFAULT_BACKUP_INTERVAL_DAYS,
                 backup_time: str = config.DEFAULT_BACKUP_TIME,
                 backup_keep_count: int = config.DEFAULT_BACKUP_KEEP_COUNT,
                 last_backup_at: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(420)
        self.setMinimumHeight(360)
        self._build_ui(
            threshold_important, threshold_unimportant, default_reminder_rule,
            default_reminder_offset, pomodoro_duration, hotkey_enabled, hotkey_toggle, hotkey_quickadd,
            backup_enabled, backup_interval_days, backup_time, backup_keep_count, last_backup_at,
        )

    def _build_ui(self, threshold_important: int, threshold_unimportant: int,
                  default_reminder_rule: str, default_reminder_offset: int, pomodoro_duration: int,
                  hotkey_enabled: bool, hotkey_toggle: str, hotkey_quickadd: str,
                  backup_enabled: bool, backup_interval_days: int, backup_time: str,
                  backup_keep_count: int, last_backup_at: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.tabs.addTab(
            self._build_general_tab(
                threshold_important, threshold_unimportant,
                default_reminder_rule, default_reminder_offset, pomodoro_duration,
            ),
            "通用",
        )
        self.tabs.addTab(
            self._build_hotkey_tab(hotkey_enabled, hotkey_toggle, hotkey_quickadd),
            "热键",
        )
        self.tabs.addTab(
            self._build_data_tab(
                backup_enabled, backup_interval_days, backup_time,
                backup_keep_count, last_backup_at,
            ),
            "数据",
        )
        self.tabs.addTab(self._build_about_tab(), "关于")

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _build_general_tab(self, threshold_important: int, threshold_unimportant: int,
                           default_reminder_rule: str, default_reminder_offset: int,
                           pomodoro_duration: int) -> QWidget:
        """通用页：阈值、默认提醒、番茄钟、开机启动。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        # 紧急窗口（双阈值：重要 / 不重要 各自独立，PRD F6.3「每象限阈值可设」）
        layout.addWidget(self._caption("紧急判定阈值"))
        row = QHBoxLayout()
        row.addWidget(QLabel("重要任务紧急窗口"))
        row.addStretch(1)
        self.threshold_important_combo = QComboBox()
        for value in URGENCY_THRESHOLD_OPTIONS:
            self.threshold_important_combo.addItem(f"{value} 天", value)
        idx_i = self.threshold_important_combo.findData(threshold_important or DEFAULT_URGENCY_THRESHOLD)
        self.threshold_important_combo.setCurrentIndex(max(idx_i, 0))
        row.addWidget(self.threshold_important_combo)
        layout.addLayout(row)

        row_u = QHBoxLayout()
        row_u.addWidget(QLabel("不重要任务紧急窗口"))
        row_u.addStretch(1)
        self.threshold_unimportant_combo = QComboBox()
        for value in URGENCY_THRESHOLD_OPTIONS:
            self.threshold_unimportant_combo.addItem(f"{value} 天", value)
        idx_u = self.threshold_unimportant_combo.findData(threshold_unimportant or DEFAULT_URGENCY_THRESHOLD)
        self.threshold_unimportant_combo.setCurrentIndex(max(idx_u, 0))
        row_u.addWidget(self.threshold_unimportant_combo)
        layout.addLayout(row_u)

        def _emit_thresholds() -> None:
            self.thresholds_changed.emit(
                self.threshold_important_combo.currentData(),
                self.threshold_unimportant_combo.currentData(),
            )

        self.threshold_important_combo.currentIndexChanged.connect(lambda *_: _emit_thresholds())
        self.threshold_unimportant_combo.currentIndexChanged.connect(lambda *_: _emit_thresholds())

        hint = QLabel("截止日期剩余天数不超过该值时，对应重要性的任务判定为「紧急」；"
                      "重要任务控制 Q1↔Q2，不重要任务控制 Q3↔Q4")
        hint.setProperty("role", "meta")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        # 默认提醒联动（PRD F26.3）
        layout.addWidget(self._caption("默认提醒"))
        reminder_row = QHBoxLayout()
        reminder_row.addWidget(QLabel("新建任务时"))
        reminder_row.addStretch(1)
        self.reminder_rule_combo = QComboBox()
        reminder_rule_labels = {
            config.REMINDER_RULE_NONE: "不提醒",
            config.REMINDER_RULE_RELATIVE: "相对截止前",
        }
        for value in (config.REMINDER_RULE_NONE, config.REMINDER_RULE_RELATIVE):
            self.reminder_rule_combo.addItem(reminder_rule_labels.get(value, value), value)
        ridx = self.reminder_rule_combo.findData(default_reminder_rule or config.REMINDER_RULE_NONE)
        self.reminder_rule_combo.setCurrentIndex(max(ridx, 0))
        reminder_row.addWidget(self.reminder_rule_combo)
        self.reminder_offset_spin = QSpinBox()
        self.reminder_offset_spin.setRange(1, 60 * 24 * 7)
        self.reminder_offset_spin.setSuffix(" 分钟")
        self.reminder_offset_spin.setValue(default_reminder_offset or config.DEFAULT_REMINDER_OFFSET)
        self.reminder_offset_spin.setEnabled(default_reminder_rule == config.REMINDER_RULE_RELATIVE)
        reminder_row.addWidget(self.reminder_offset_spin)
        layout.addLayout(reminder_row)
        reminder_hint = QLabel("「相对截止前」按截止日期提前该时长触发提醒")
        reminder_hint.setProperty("role", "meta")
        reminder_hint.setWordWrap(True)
        layout.addWidget(reminder_hint)

        def _emit_reminder() -> None:
            rule = self.reminder_rule_combo.currentData()
            self.reminder_offset_spin.setEnabled(rule == config.REMINDER_RULE_RELATIVE)
            self.default_reminder_changed.emit(rule, self.reminder_offset_spin.value())

        self.reminder_rule_combo.currentIndexChanged.connect(lambda *_: _emit_reminder())
        self.reminder_offset_spin.valueChanged.connect(lambda *_: _emit_reminder())

        layout.addSpacing(8)

        # 番茄钟时长（PRD F23.1 / F23.6）
        layout.addWidget(self._caption("番茄钟"))
        pomo_row = QHBoxLayout()
        pomo_row.addWidget(QLabel("单次时长"))
        pomo_row.addStretch(1)
        self.pomodoro_spin = QSpinBox()
        self.pomodoro_spin.setRange(1, 180)
        self.pomodoro_spin.setSuffix(" 分钟")
        self.pomodoro_spin.setValue(pomodoro_duration or 25)
        pomo_row.addWidget(self.pomodoro_spin)
        layout.addLayout(pomo_row)
        pomo_hint = QLabel("开始番茄钟时的默认倒计时长度，可在设置中随时调整")
        pomo_hint.setProperty("role", "meta")
        pomo_hint.setWordWrap(True)
        layout.addWidget(pomo_hint)

        self.pomodoro_spin.valueChanged.connect(
            lambda value: self.pomodoro_changed.emit(int(value))
        )

        layout.addSpacing(8)

        # 开机启动
        layout.addWidget(self._caption("启动"))
        self.autostart_box = QCheckBox("开机自动启动")
        self.autostart_box.setChecked(is_autostart_enabled())
        self.autostart_box.stateChanged.connect(
            lambda state: self.autostart_changed.emit(bool(state))
        )
        layout.addWidget(self.autostart_box)

        layout.addStretch(1)
        return page

    def _build_hotkey_tab(self, hotkey_enabled: bool, hotkey_toggle: str,
                          hotkey_quickadd: str) -> QWidget:
        """热键页：全局热键总开关与按键设置。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.hotkey_box = QCheckBox("启用系统级全局热键（应用失焦时仍生效）")
        self.hotkey_box.setChecked(bool(hotkey_enabled))
        layout.addWidget(self.hotkey_box)

        self.hotkey_form = QWidget()
        hk_layout = QVBoxLayout(self.hotkey_form)
        hk_layout.setContentsMargins(0, 0, 0, 0)
        hk_layout.setSpacing(6)

        toggle_row = QHBoxLayout()
        toggle_row.addWidget(QLabel("呼出 / 隐藏"))
        toggle_row.addStretch(1)
        self.hotkey_toggle_edit = QLineEdit(hotkey_toggle)
        self.hotkey_toggle_edit.setMaximumWidth(140)
        toggle_row.addWidget(self.hotkey_toggle_edit)
        hk_layout.addLayout(toggle_row)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("快速添加"))
        add_row.addStretch(1)
        self.hotkey_quickadd_edit = QLineEdit(hotkey_quickadd)
        self.hotkey_quickadd_edit.setMaximumWidth(140)
        add_row.addWidget(self.hotkey_quickadd_edit)
        hk_layout.addLayout(add_row)

        hk_hint = QLabel("格式如 Ctrl+Alt+Q，需含至少一个修饰键（Ctrl/Alt/Shift/Win）。"
                         "若被其他程序占用会注册失败并回退为「仅焦点内生效」。")
        hk_hint.setProperty("role", "meta")
        hk_hint.setWordWrap(True)
        hk_layout.addWidget(hk_hint)
        layout.addWidget(self.hotkey_form)
        self.hotkey_form.setEnabled(bool(hotkey_enabled))

        def _on_hotkey_toggled(state) -> None:
            enabled = bool(state)
            # F25.5：开启前二次确认，说明「失焦仍生效」的影响
            if enabled:
                box = QMessageBox(self)
                box.setWindowTitle("启用全局热键")
                box.setText("全局热键在本应用未获得焦点时依然生效，可能与系统或其他软件冲突。")
                box.setInformativeText("确定要启用吗？")
                box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                box.setDefaultButton(QMessageBox.No)
                if box.exec() != QMessageBox.Yes:
                    self.hotkey_box.setChecked(False)
                    return
            self.hotkey_form.setEnabled(enabled)
            self._emit_hotkey()

        self.hotkey_box.stateChanged.connect(_on_hotkey_toggled)
        self.hotkey_toggle_edit.editingFinished.connect(self._emit_hotkey)
        self.hotkey_quickadd_edit.editingFinished.connect(self._emit_hotkey)

        layout.addStretch(1)
        return page

    def set_last_backup_at(self, value: str) -> None:
        """刷新「上次备份」显示（立即备份完成后由主窗口回调）。"""
        if not value:
            self.last_backup_label.setText("尚未备份")
            return
        try:
            moment = datetime.fromisoformat(value)
            self.last_backup_label.setText(f"上次备份：{moment:%Y-%m-%d %H:%M:%S}")
        except ValueError:
            self.last_backup_label.setText(f"上次备份：{value}")

    def _build_data_tab(self, backup_enabled: bool, backup_interval_days: int,
                        backup_time: str, backup_keep_count: int,
                        last_backup_at: str) -> QWidget:
        """数据页：导入 / 导出 / 自动备份策略 / 打开目录 / 更改位置。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        layout.addWidget(self._caption("备份与恢复"))
        data_row = QHBoxLayout()
        self.export_btn = QPushButton("导出数据")
        self.export_btn.clicked.connect(self.export_requested.emit)
        data_row.addWidget(self.export_btn)

        self.import_btn = QPushButton("导入数据")
        self.import_btn.clicked.connect(self.import_requested.emit)
        data_row.addWidget(self.import_btn)

        self.open_dir_btn = QPushButton("打开数据目录")
        self.open_dir_btn.clicked.connect(self._open_data_dir)
        data_row.addWidget(self.open_dir_btn)
        layout.addLayout(data_row)

        layout.addSpacing(8)

        # 自动备份策略（PRD F10.5 扩展：频率 / 时刻 / 保留份数可配）
        layout.addWidget(self._caption("自动备份"))

        self.backup_enabled_check = QCheckBox("启用自动备份")
        self.backup_enabled_check.setChecked(backup_enabled)
        layout.addWidget(self.backup_enabled_check)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("备份频率"))
        interval_row.addStretch(1)
        self.backup_interval_spin = QSpinBox()
        self.backup_interval_spin.setRange(
            config.BACKUP_MIN_INTERVAL_DAYS, config.BACKUP_MAX_INTERVAL_DAYS
        )
        self.backup_interval_spin.setSuffix(" 天")
        self.backup_interval_spin.setValue(backup_interval_days)
        interval_row.addWidget(self.backup_interval_spin)
        layout.addLayout(interval_row)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("备份时刻"))
        time_row.addStretch(1)
        self.backup_time_edit = QTimeEdit()
        self.backup_time_edit.setDisplayFormat("HH:mm:ss")
        parsed = QTime.fromString(backup_time or config.DEFAULT_BACKUP_TIME, "HH:mm:ss")
        self.backup_time_edit.setTime(parsed if parsed.isValid() else QTime(20, 0, 0))
        time_row.addWidget(self.backup_time_edit)
        layout.addLayout(time_row)

        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("保留份数"))
        keep_row.addStretch(1)
        self.backup_keep_spin = QSpinBox()
        self.backup_keep_spin.setRange(
            config.BACKUP_MIN_KEEP_COUNT, config.BACKUP_MAX_KEEP_COUNT
        )
        self.backup_keep_spin.setSuffix(" 份")
        self.backup_keep_spin.setValue(backup_keep_count)
        keep_row.addWidget(self.backup_keep_spin)
        layout.addLayout(keep_row)

        backup_hint = QLabel("每天到达备份时刻、且距上次备份已满设定天数时自动备份。"
                             "同一天重复备份会覆盖当天文件，所以「保留 N 份」就是最近 N 个有备份的日期。")
        backup_hint.setProperty("role", "meta")
        backup_hint.setWordWrap(True)
        layout.addWidget(backup_hint)

        now_row = QHBoxLayout()
        self.backup_now_btn = QPushButton("立即备份")
        self.backup_now_btn.clicked.connect(self.backup_now_requested.emit)
        now_row.addWidget(self.backup_now_btn)
        self.last_backup_label = QLabel()
        self.last_backup_label.setProperty("role", "meta")
        now_row.addWidget(self.last_backup_label)
        now_row.addStretch(1)
        layout.addLayout(now_row)
        self.set_last_backup_at(last_backup_at)

        def _sync_backup_enabled(enabled: bool) -> None:
            self.backup_interval_spin.setEnabled(enabled)
            self.backup_time_edit.setEnabled(enabled)
            self.backup_keep_spin.setEnabled(enabled)

        def _emit_backup() -> None:
            enabled = self.backup_enabled_check.isChecked()
            _sync_backup_enabled(enabled)
            self.backup_changed.emit(
                enabled,
                self.backup_interval_spin.value(),
                self.backup_time_edit.time().toString("HH:mm:ss"),
                self.backup_keep_spin.value(),
            )

        self.backup_enabled_check.toggled.connect(lambda *_: _emit_backup())
        self.backup_interval_spin.valueChanged.connect(lambda *_: _emit_backup())
        self.backup_time_edit.timeChanged.connect(lambda *_: _emit_backup())
        self.backup_keep_spin.valueChanged.connect(lambda *_: _emit_backup())
        # 仅同步可用性，不在此处 emit，避免打开设置就把当前值写回
        _sync_backup_enabled(backup_enabled)

        layout.addSpacing(8)

        layout.addWidget(self._caption("数据位置"))
        path_label = QLabel(f"数据文件位置：{DATA_DIR}")
        path_label.setProperty("role", "meta")
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(path_label)

        change_row = QHBoxLayout()
        change_hint = QLabel("默认保存在程序所在目录，可更改为其他位置")
        change_hint.setProperty("role", "meta")
        change_hint.setWordWrap(True)
        change_row.addWidget(change_hint)
        change_row.addStretch(1)
        self.change_dir_btn = QPushButton("更改位置…")
        self.change_dir_btn.clicked.connect(self._change_data_dir)
        change_row.addWidget(self.change_dir_btn)
        layout.addLayout(change_row)

        layout.addStretch(1)
        return page

    def _build_about_tab(self) -> QWidget:
        """关于页：版本、构建时间、开源仓库。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        about = QLabel(f"版本 {VERSION}\n构建时间 {BUILD_TIME}")
        about.setProperty("role", "meta")
        about.setWordWrap(True)
        layout.addWidget(about)

        repo_row = QHBoxLayout()
        repo_link = QLabel(
            f'<a href="{REPO_URL}">GitHub：dos-lin/QuadrantTodo</a>'
        )
        repo_link.setProperty("role", "link")
        repo_link.setWordWrap(True)
        repo_link.setOpenExternalLinks(False)
        repo_link.linkActivated.connect(lambda _: self._open_repo())
        repo_row.addWidget(repo_link, 1)
        copy_btn = QPushButton("复制")
        copy_btn.setMaximumWidth(64)
        copy_btn.setToolTip("复制仓库地址")
        copy_btn.clicked.connect(self._copy_repo)
        repo_row.addWidget(copy_btn)
        layout.addLayout(repo_row)

        layout.addStretch(1)
        return page

    def _emit_hotkey(self) -> None:
        """校验热键格式后发出；格式非法时保留原值并提示。"""
        toggle = self.hotkey_toggle_edit.text().strip()
        quickadd = self.hotkey_quickadd_edit.text().strip()
        if parse_hotkey(toggle) is None or parse_hotkey(quickadd) is None:
            QMessageBox.warning(
                self, "热键格式不正确",
                "请使用「修饰键+字母或数字」格式，例如 Ctrl+Alt+Q。",
            )
            return
        self.hotkey_changed.emit(self.hotkey_box.isChecked(), toggle, quickadd)

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "field-label")
        return label

    def _change_data_dir(self) -> None:
        new_dir = QFileDialog.getExistingDirectory(self, "选择数据文件目录", str(DATA_DIR))
        if new_dir:
            self.data_dir_change_requested.emit(new_dir)

    def _open_data_dir(self) -> None:
        import subprocess

        path = DATA_DIR
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _open_repo(self) -> None:
        """在浏览器中打开 GitHub 仓库。"""
        QDesktopServices.openUrl(QUrl(REPO_URL))

    def _copy_repo(self) -> None:
        """复制仓库地址到剪贴板。"""
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(REPO_URL)
        sender = self.sender()
        if isinstance(sender, QPushButton):
            sender.setText("已复制")

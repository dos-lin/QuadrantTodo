"""托盘快速添加窗口（PRD F2.9 / F8.8）。

不打开主窗口即可记录任务。创建规则同 F2.3：开关只决定「重要吗」，
象限一律交由系统计算，不锁定。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..due_parser import parse_due_from_title

_DEFAULT_HINT = "象限由截止日期自动计算，不用手动分类"



class QuickAddWindow(QWidget):
    """轻量任务录入窗口。"""

    #: (标题, 是否重要)
    submitted = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("快速添加任务")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.resize(360, 120)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("要做什么？回车即创建（标题里写「15号」「9月15日」会自动设为截止日期）")
        self.title_edit.installEventFilter(self)
        self.title_edit.textChanged.connect(self._update_hint)
        layout.addWidget(self.title_edit)

        row = QHBoxLayout()
        self.important_box = QCheckBox("重要")
        row.addWidget(self.important_box)
        row.addStretch(1)

        self.submit_btn = QPushButton("添加")
        self.submit_btn.clicked.connect(self._submit)
        row.addWidget(self.submit_btn)
        layout.addLayout(row)

        self.hint = QLabel(_DEFAULT_HINT)
        self.hint.setProperty("role", "meta")
        layout.addWidget(self.hint)

    def _update_hint(self, _text: str = "") -> None:
        """实时预览标题里识别到的截止日期（PRD F2.10）。"""
        parsed = parse_due_from_title(self.title_edit.text(), date.today())
        if parsed.due_date is None:
            self.hint.setText(_DEFAULT_HINT)
            return
        self.hint.setText(f"识别到截止日期 {parsed.due_date.isoformat()}，标题：{parsed.title}")

    def _submit(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            return
        self.submitted.emit(title, self.important_box.isChecked())
        self.title_edit.clear()
        self.hide()

    def eventFilter(self, obj, event) -> bool:
        # 主键盘回车(Key_Return)与小键盘回车(Key_Enter)都创建（PRD F2.9）
        if obj is self.title_edit and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._submit()
                return True
        return super().eventFilter(obj, event)

    def show_and_focus(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.title_edit.setFocus()

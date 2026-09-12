"""F14 修复：输入框聚焦时任务操作快捷键不拦截，保证可正常输入空格/删除等。"""
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

_app = QApplication.instance() or QApplication([])

from quadrant_todo.app import _is_editing_widget


def test_editing_widgets_detected() -> None:
    assert _is_editing_widget(QLineEdit()) is True
    assert _is_editing_widget(QTextEdit()) is True
    assert _is_editing_widget(QPlainTextEdit()) is True
    assert _is_editing_widget(QDateEdit()) is True  # QAbstractSpinBox 子类


def test_combobox_depends_on_editable() -> None:
    editable = QComboBox()
    editable.setEditable(True)
    assert _is_editing_widget(editable) is True

    fixed = QComboBox()
    assert _is_editing_widget(fixed) is False


def test_non_editing_widgets_not_blocked() -> None:
    assert _is_editing_widget(QWidget()) is False
    assert _is_editing_widget(None) is False


def _shortcut_enabled_states(win) -> list[bool]:
    return [s.isEnabled() for s in win._task_action_shortcuts]


def test_task_action_shortcuts_disabled_on_edit_focus() -> None:
    """MainWindow 焦点联动：输入框聚焦时禁用任务操作快捷键（Space/Delete/Up/Down/F2），离开后恢复。"""
    from quadrant_todo.app import MainWindow

    db = __import__("quadrant_todo.db", fromlist=["Database"]).Database()
    db.connect()
    try:
        win = MainWindow(db)
        # 初始无焦点控件：任务操作快捷键应启用
        assert all(_shortcut_enabled_states(win)) is True

        editor = QLineEdit()
        # 模拟焦点落到输入框（offscreen 下 setFocus 未必 emit focusChanged，直接驱动 handler）
        win._on_focus_changed(None, editor)
        assert any(_shortcut_enabled_states(win)) is False

        # 焦点离开输入框到其他非编辑控件：恢复启用
        win._on_focus_changed(editor, QWidget())
        assert all(_shortcut_enabled_states(win)) is True
        win.close()
    finally:
        db.close()

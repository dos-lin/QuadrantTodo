"""深浅色主题（PRD F24）。

主题三态：system / light / dark。
- system：跟随系统配色（QStyleHints.colorScheme / 高对比度设置）
- light / dark：强制指定

实现：light 样式即现有 styles.qss；dark 样式为 styles/dark.qss（覆盖同名选择器）。
整体交给 QApplication.setStyleSheet，覆盖所有窗口与对话框。
`current_scheme()` 供组件在运行期读取当前实际配色（用于个别内联色）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from . import config

_LIGHT_QSS: str | None = None
_DARK_QSS: str | None = None
_ACTIVE_SCHEME = config.THEME_LIGHT


def _load() -> None:
    global _LIGHT_QSS, _DARK_QSS
    base = Path(__file__).resolve().parent
    light_path = base / "styles.qss"
    dark_path = base / "styles" / "dark.qss"
    _LIGHT_QSS = light_path.read_text(encoding="utf-8") if light_path.exists() else ""
    _DARK_QSS = dark_path.read_text(encoding="utf-8") if dark_path.exists() else ""


def resolve_scheme(theme: str) -> str:
    """将主题偏好解析为具体的 light / dark。"""
    if theme != config.THEME_SYSTEM:
        return config.THEME_DARK if theme == config.THEME_DARK else config.THEME_LIGHT
    # system：跟随系统
    hints = QGuiApplication.styleHints()
    if hints is not None:
        try:
            if hints.colorScheme() == Qt.ColorScheme.Dark:
                return config.THEME_DARK
        except AttributeError:
            pass
    return config.THEME_LIGHT


def apply_theme(theme: str) -> None:
    """应用主题：合并 light(+dark) 样式表到 QApplication，并记录实际配色。"""
    global _LIGHT_QSS, _DARK_QSS, _ACTIVE_SCHEME
    if _LIGHT_QSS is None:
        _load()
    scheme = resolve_scheme(theme)
    _ACTIVE_SCHEME = scheme
    qss = _LIGHT_QSS or ""
    if scheme == config.THEME_DARK and _DARK_QSS:
        qss = qss + "\n" + _DARK_QSS
    app = QGuiApplication.instance()
    if app is not None:
        app.setStyleSheet(qss)


def current_scheme() -> str:
    """当前实际配色（light / dark），供内联颜色组件读取。"""
    return _ACTIVE_SCHEME


def is_dark() -> bool:
    return _ACTIVE_SCHEME == config.THEME_DARK

"""系统级全局热键（PRD F25）。

仅依赖 PySide6 + ctypes（调用 Win32 RegisterHotKey），不引入第三方库（决策 #3）。
通过 QAbstractNativeEventFilter 在 Qt 事件循环内捕获 WM_HOTKEY，即使应用失焦
（最小化到托盘 / 其他窗口前置）仍可触发。注册失败回退到「仅焦点内生效」并提示。
"""

from __future__ import annotations

import ctypes
import logging
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, Signal

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_ID_TOGGLE = 1
_ID_QUICKADD = 2

_KEY_MODS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """将 'Ctrl+Alt+Q' 解析为 (modifiers, vk)。不合法的组合返回 None。

    约束（PRD F25.2）：
    · 必须以单个字母或数字结尾；
    · 必须包含至少一个修饰键——否则会劫持普通按键（例如裸 'Q' 会让
      用户在任何程序里都打不出 Q），这是不可接受的系统级副作用；
    · 不支持功能键（F1 等）与符号键。
    """
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    key = parts[-1].upper()
    if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        vk = ord(key)
    else:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _KEY_MODS:
            return None
        mods |= _KEY_MODS[p]
    if mods == 0:
        return None
    return mods, vk


class HotkeyFilter(QAbstractNativeEventFilter):
    """捕获 WM_HOTKEY 并转发为 hotkey_id 信号。"""

    activated = Signal(int)

    def nativeEventFilter(self, event_type, message):
        if event_type == "windows_generic_MSG":
            try:
                msg = ctypes.cast(int(message), ctypes.POINTER(ctypes.wintypes.MSG)).contents
                if msg.message == WM_HOTKEY:
                    self.activated.emit(int(msg.wParam))
            except Exception:  # 解析异常不应影响主循环
                logger.exception("全局热键消息解析失败")
        return False, 0


class HotkeyManager:
    """注册并监听两条全局热键（呼出/隐藏、快速添加）。"""

    def __init__(self, host_win_id: int):
        self._win_id = int(host_win_id)
        self._filter: HotkeyFilter | None = None
        self._registered: set[int] = set()
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self, toggle_key: str, quickadd_key: str, on_activate: Callable[[str], None]) -> bool:
        """注册热键并安装事件过滤器。返回是否成功（全失败则 False）。

        on_activate(action): action ∈ {'toggle', 'quickadd'}。
        """
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            logger.warning("非 Windows 平台，全局热键不可用")
            return False

        self._filter = HotkeyFilter()
        self._filter.activated.connect(
            lambda hid: on_activate("toggle" if hid == _ID_TOGGLE else "quickadd")
        )

        ok = False
        spec = [(toggle_key, _ID_TOGGLE, "toggle"), (quickadd_key, _ID_QUICKADD, "quickadd")]
        for key_text, hid, _ in spec:
            parsed = parse_hotkey(key_text)
            if parsed is None:
                logger.warning("热键格式无法解析：%s", key_text)
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(self._win_id, hid, mods, vk):
                self._registered.add(hid)
                ok = True
            else:
                logger.warning("全局热键注册失败（可能与其他程序冲突）：%s", key_text)

        if ok:
            from PySide6.QtWidgets import QApplication

            QApplication.instance().installNativeEventFilter(self._filter)
            self._enabled = True
        return ok

    def disable(self) -> None:
        """注销热键并移除事件过滤器（F25.6 回退 / 关闭时）。"""
        if not self._registered:
            self._enabled = False
            return
        try:
            user32 = ctypes.windll.user32
            for hid in self._registered:
                user32.UnregisterHotKey(self._win_id, hid)
        except AttributeError:
            pass
        if self._filter is not None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._filter)
            self._filter = None
        self._registered.clear()
        self._enabled = False

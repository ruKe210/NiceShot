from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractEventDispatcher, QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QKeySequence

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

VK_MAP = {
    "SPACE": 0x20,
    "TAB": 0x09,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PRINTSCREEN": 0x2C,
    "SNAPSHOT": 0x2C,
    "PAUSE": 0x13,
    "BREAK": 0x13,
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
}


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [p.strip().upper() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("空快捷键")
    mods = 0
    key = None
    for part in parts:
        if part in {"CTRL", "CONTROL"}:
            mods |= MOD_CONTROL
        elif part == "SHIFT":
            mods |= MOD_SHIFT
        elif part == "ALT":
            mods |= MOD_ALT
        elif part in {"WIN", "META", "SUPER"}:
            mods |= MOD_WIN
        else:
            key = part
    if key is None:
        raise ValueError("缺少主键")
    if key in VK_MAP:
        vk = VK_MAP[key]
    elif len(key) == 1:
        vk = ord(key)
    else:
        raise ValueError(f"不支持的按键：{key}")
    return mods, vk


def format_key_event(event, allow_single: bool = False) -> str | None:
    key = event.key()
    if key in (
        Qt.Key_Control,
        Qt.Key_Shift,
        Qt.Key_Alt,
        Qt.Key_Meta,
        Qt.Key_unknown,
    ):
        return None
    parts: list[str] = []
    mods = event.modifiers()
    if mods & Qt.ControlModifier:
        parts.append("Ctrl")
    if mods & Qt.ShiftModifier:
        parts.append("Shift")
    if mods & Qt.AltModifier:
        parts.append("Alt")
    if mods & Qt.MetaModifier:
        parts.append("Win")
    name = QKeySequence(key).toString()
    if not name:
        return None
    parts.append(name.upper() if len(name) == 1 else name)
    if not parts:
        return None
    if len(parts) < 2 and not allow_single:
        return None
    return "+".join(parts)


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, callback: Callable[[], None]) -> None:
        super().__init__()
        self.hotkey_id = hotkey_id
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        raw = event_type.data() if hasattr(event_type, "data") else event_type
        if raw not in (b"windows_generic_MSG", "windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
            self.callback()
            return True, 1
        return False, 0


class GlobalHotkey:
    def __init__(self, hwnd: int, hotkey_id: int, callback: Callable[[], None]) -> None:
        self.hwnd = hwnd
        self.hotkey_id = hotkey_id
        self.filter = HotkeyFilter(hotkey_id, callback)
        dispatcher = QAbstractEventDispatcher.instance()
        if dispatcher is not None:
            dispatcher.installNativeEventFilter(self.filter)
        self._registered = False

    def register(self, hotkey_text: str) -> None:
        self.unregister()
        mods, vk = parse_hotkey(hotkey_text)
        ok = ctypes.windll.user32.RegisterHotKey(
            self.hwnd,
            self.hotkey_id,
            mods | MOD_NOREPEAT,
            vk,
        )
        if not ok:
            raise RuntimeError(f"快捷键 {hotkey_text} 注册失败，可能已被其他程序占用。")
        self._registered = True

    def unregister(self) -> None:
        if not self._registered:
            return
        ctypes.windll.user32.UnregisterHotKey(self.hwnd, self.hotkey_id)
        self._registered = False

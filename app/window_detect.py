from __future__ import annotations

import ctypes
from ctypes import wintypes

import win32con
import win32gui

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
OVERLAY_TITLE = "NiceShotOverlay"
INCLUDE_TOOL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd"}
SKIP_CLASSES = {"CEF-OSC-WIDGET", "Tao Thread Event Target"}


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return hr == 0 and cloaked.value != 0
    except Exception:
        return False


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return win32gui.GetWindowRect(hwnd)


def enum_windows() -> list[tuple[int, tuple[int, int, int, int], str]]:
    """按 Z 序（顶到低）返回可见顶层窗口。"""
    windows: list[tuple[int, tuple[int, int, int, int], str]] = []

    def callback(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.IsIconic(hwnd):
            return True
        if _is_cloaked(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd)
        if cls in SKIP_CLASSES:
            return True
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex_style & win32con.WS_EX_TOOLWINDOW and cls not in INCLUDE_TOOL_CLASSES:
            return True
        title = win32gui.GetWindowText(hwnd)
        if title in {OVERLAY_TITLE, "NiceShotHost", "NiceShotScrollBar", "NiceShotScrollPreview", "NiceShotPin"}:
            return True
        left, top, right, bottom = get_window_rect(hwnd)
        if right - left <= 2 or bottom - top <= 2:
            return True
        windows.append((hwnd, (left, top, right, bottom), title))
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def hit_test(
    windows: list[tuple[int, tuple[int, int, int, int], str]],
    x: int,
    y: int,
) -> tuple[int, tuple[int, int, int, int], str] | None:
    for item in windows:
        left, top, right, bottom = item[1]
        if left <= x < right and top <= y < bottom:
            return item
    return None

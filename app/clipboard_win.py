from __future__ import annotations

import struct
import time
from io import BytesIO
from pathlib import Path

import win32clipboard
import win32con
from PIL import Image

from app.config import temp_dir


def _make_dib(image: Image.Image) -> bytes:
    output = BytesIO()
    image.convert("RGB").save(output, "BMP")
    return output.getvalue()[14:]


def _make_png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _make_hdrop(filepath: str) -> bytes:
    abs_path = str(Path(filepath).resolve())
    dropfiles = struct.pack("Iiiii", 20, 0, 0, 0, 1)
    encoded = abs_path.encode("utf-16-le") + b"\x00\x00\x00\x00"
    return dropfiles + encoded


def cleanup_old_temp(max_age_sec: int = 24 * 3600) -> None:
    folder = temp_dir()
    now = time.time()
    for path in folder.glob("NiceShot_*.png"):
        try:
            if now - path.stat().st_mtime > max_age_sec:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def copy_image(image: Image.Image) -> Path:
    """写入剪贴板：位图 + PNG + 文件（可粘贴到文件夹）。"""
    cleanup_old_temp()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filepath = temp_dir() / f"NiceShot_{stamp}.png"
    image.save(filepath, "PNG")

    dib = _make_dib(image)
    png = _make_png(image)
    hdrop = _make_hdrop(str(filepath))

    cf_png = win32clipboard.RegisterClipboardFormat("PNG")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
        win32clipboard.SetClipboardData(cf_png, png)
        win32clipboard.SetClipboardData(win32con.CF_HDROP, hdrop)
    finally:
        win32clipboard.CloseClipboard()
    return filepath

from __future__ import annotations

import sys
from pathlib import Path

STARTUP_NAME = "NiceShot.lnk"


def _startup_dir() -> Path:
    from os import environ

    return (
        Path(environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def shortcut_path() -> Path:
    return _startup_dir() / STARTUP_NAME


def pythonw_path() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def script_path() -> Path:
    return project_root() / "main.py"


def is_enabled() -> bool:
    return shortcut_path().exists()


def set_enabled(enabled: bool) -> None:
    path = shortcut_path()
    if not enabled:
        if path.exists():
            path.unlink()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(path))
    shortcut.Targetpath = pythonw_path()
    shortcut.Arguments = f'"{script_path()}"'
    shortcut.WorkingDirectory = str(project_root())
    shortcut.Description = "NiceShot 截图工具"
    shortcut.WindowStyle = 7
    shortcut.save()

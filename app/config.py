from __future__ import annotations

import json
from pathlib import Path

APP_NAME = "NiceShot"
DEFAULT_HOTKEY = "Ctrl+Shift+A"
DEFAULTS = {
    "hotkey": DEFAULT_HOTKEY,
    "autostart": False,
}


def config_dir() -> Path:
    from os import environ

    root = Path(environ.get("APPDATA", Path.home()))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict:
    path = config_path()
    data = dict(DEFAULTS)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save_config(data: dict) -> None:
    merged = dict(DEFAULTS)
    merged.update(data)
    config_path().write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def temp_dir() -> Path:
    import tempfile

    path = Path(tempfile.gettempdir()) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path

"""Where the app keeps its local files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AutoPoster"
DEFAULT_PORT = 8765


def data_dir() -> Path:
    """Per-user data folder. Override with AUTO_POSTER_DATA_DIR (used by tests)."""
    override = os.environ.get("AUTO_POSTER_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(base) / "auto-poster"
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_dir() -> Path:
    path = data_dir() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "auto-poster.db"

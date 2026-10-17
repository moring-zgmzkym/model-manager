"""Filesystem locations used by ModelDock."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parent
PROJECT_DIR = BACKEND_DIR.parent
SERVERS_DIR = BACKEND_DIR / "servers"
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
PLUGINS_DIR = PROJECT_DIR / "plugins"


def data_dir() -> Path:
    """Directory holding config, logs, uploads and outputs.

    Override with the MODELDOCK_DATA environment variable.
    """
    root = Path(os.environ.get("MODELDOCK_DATA") or (PROJECT_DIR / "data")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def sub_dir(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path

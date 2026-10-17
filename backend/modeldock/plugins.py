"""Load user plugins from ``plugins/*.py``.

A plugin is a Python file exposing ``detect(root: Path, scan) -> dict | None``.
Return ``None`` when the folder isn't yours, otherwise a dict that may hold
``adapter``, ``description``, ``launch`` (LaunchConfig fields), ``notes`` and
``capabilities`` (list of Capability dicts). Files starting with ``_`` are skipped.
"""

from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path
from typing import Any, Callable

from .paths import PLUGINS_DIR

Detector = Callable[[Path, Any], "dict | None"]


def load_plugins(directory: Path = PLUGINS_DIR) -> tuple[list[Detector], list[dict[str, Any]]]:
    detectors: list[Detector] = []
    info: list[dict[str, Any]] = []
    if not directory.is_dir():
        return detectors, info
    for f in sorted(directory.glob("*.py")):
        if f.name.startswith("_"):
            continue
        entry: dict[str, Any] = {"file": f.name, "ok": False}
        try:
            spec = importlib.util.spec_from_file_location(f"modeldock_plugin_{f.stem}", f)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, "detect", None)
            if callable(fn):
                detectors.append(fn)
                entry.update(ok=True, name=getattr(mod, "NAME", f.stem), description=getattr(mod, "DESCRIPTION", ""))
            else:
                entry["error"] = "缺少 detect() 函数"
        except Exception as exc:
            entry["error"] = f"{exc.__class__.__name__}: {exc}"
            entry["trace"] = traceback.format_exc(limit=3)
        info.append(entry)
    return detectors, info

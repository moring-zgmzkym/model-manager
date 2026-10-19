"""Generated files (images, audio) and user uploads."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .paths import sub_dir
from .schemas import new_id

_lock = threading.Lock()
EXT_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/flac": ".flac",
               "audio/webm": ".webm", "audio/aac": ".aac",
               "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "image/gif": ".gif"}


def outputs_dir() -> Path:
    return sub_dir("outputs")


def uploads_dir() -> Path:
    return sub_dir("uploads")


def _index_path() -> Path:
    return outputs_dir() / "index.json"


def list_outputs(limit: int = 500, kind: Optional[str] = None, model_id: Optional[str] = None) -> list[dict[str, Any]]:
    p = _index_path()
    items: list[dict[str, Any]] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if kind:
        items = [i for i in items if i.get("kind") == kind]
    if model_id:
        items = [i for i in items if i.get("model_id") == model_id]
    return sorted(items, key=lambda i: i.get("created_at", 0), reverse=True)[:limit]


def _write_index(items: list[dict[str, Any]]) -> None:
    _index_path().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def save_output(data: bytes, *, kind: str, model_id: str, model_name: str, capability: str,
                ext: str = "", mime: str = "", prompt: str = "", meta: Optional[dict] = None) -> dict[str, Any]:
    ext = ext or EXT_BY_MIME.get(mime, "") or (mimetypes.guess_extension(mime or "") or ".bin")
    day = time.strftime("%Y-%m-%d")
    folder = outputs_dir() / day
    folder.mkdir(parents=True, exist_ok=True)
    oid = new_id()
    fname = f"{time.strftime('%H%M%S')}_{oid}{ext}"
    (folder / fname).write_bytes(data)
    entry = {
        "id": oid, "kind": kind, "model_id": model_id, "model_name": model_name, "capability": capability,
        "prompt": prompt[:2000], "file": f"{day}/{fname}", "url": f"/api/files/outputs/{day}/{fname}",
        "size": len(data), "created_at": time.time(), "meta": meta or {},
    }
    with _lock:
        p = _index_path()
        items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        items.append(entry)
        _write_index(items[-5000:])
    return entry


def delete_output(oid: str) -> bool:
    with _lock:
        items = list_outputs(limit=100000)
        keep = [i for i in items if i["id"] != oid]
        gone = [i for i in items if i["id"] == oid]
        for i in gone:
            try:
                (outputs_dir() / i["file"]).unlink(missing_ok=True)
            except Exception:
                pass
        _write_index(sorted(keep, key=lambda i: i.get("created_at", 0)))
        return bool(gone)


def save_upload(data: bytes, filename: str) -> dict[str, Any]:
    uid = new_id()
    safe = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", filename or "file")[-80:]
    path = uploads_dir() / f"{uid}_{safe}"
    path.write_bytes(data)
    return {"id": uid, "name": filename, "path": str(path), "url": f"/api/files/uploads/{path.name}", "size": len(data)}


def resolve_upload(ref: str) -> Optional[Path]:
    """Accept an upload id, an /api/files/uploads URL or an absolute local path."""
    if not ref:
        return None
    if ref.startswith("/api/files/uploads/"):
        p = uploads_dir() / ref.rsplit("/", 1)[-1]
        return p if p.exists() else None
    p = Path(ref)
    if p.is_absolute() and p.exists():
        return p
    for cand in uploads_dir().glob(f"{ref}_*"):
        return cand
    return None


def safe_child(base: Path, rel: str) -> Optional[Path]:
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None

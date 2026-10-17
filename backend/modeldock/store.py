"""JSON-file persistence for models and settings."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .paths import data_dir
from .schemas import ModelConfig, Settings


class Store:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (data_dir() / "config.json")
        self._lock = threading.RLock()
        self.models: dict[str, ModelConfig] = {}
        self.settings = Settings()
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.settings = Settings(**raw.get("settings", {}))
            self.models = {}
            for item in raw.get("models", []):
                model = ModelConfig(**item)
                self.models[model.id] = model

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "settings": self.settings.model_dump(),
                "models": [m.model_dump() for m in self.models.values()],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    # models -----------------------------------------------------------------
    def list_models(self) -> list[ModelConfig]:
        return sorted(self.models.values(), key=lambda m: m.created_at)

    def get(self, model_id: str) -> Optional[ModelConfig]:
        return self.models.get(model_id)

    def upsert(self, model: ModelConfig) -> ModelConfig:
        with self._lock:
            model.updated_at = time.time()
            self.models[model.id] = model
            self.save()
            return model

    def delete(self, model_id: str) -> bool:
        with self._lock:
            removed = self.models.pop(model_id, None) is not None
            if removed:
                self.save()
            return removed

    def used_ports(self) -> set[int]:
        return {m.launch.port for m in self.models.values() if m.launch.port}

    def update_settings(self, settings: Settings) -> Settings:
        with self._lock:
            self.settings = settings
            self.save()
            return settings

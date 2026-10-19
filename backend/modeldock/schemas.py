"""Pydantic models shared by the API and the store."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


def new_id() -> str:
    return uuid.uuid4().hex[:10]


CapabilitySource = Literal["manifest", "files", "runtime", "user", "plugin"]


class Capability(BaseModel):
    """One thing a model can do, plus how ModelDock calls it.

    ``binding`` describes the wire protocol, e.g. ``{"protocol": "openai_chat"}``
    or ``{"protocol": "gpt_sovits"}``. See ``catalog.PROTOCOLS``.
    """

    type: str
    enabled: bool = True
    source: CapabilitySource = "files"
    confidence: float = 0.6
    evidence: list[str] = Field(default_factory=list)
    binding: dict[str, Any] = Field(default_factory=dict)
    label: Optional[str] = None
    # Sub-features of one capability (e.g. text-to-video / first-last-frame).
    # Each item: {"id", "label", "description", "fields", "defaults", "workflow"}.
    actions: list[dict[str, Any]] = Field(default_factory=list)
    # Set when the user edited this capability by hand; rescans leave it alone.
    locked: bool = False

    @model_validator(mode="after")
    def _default_binding(self) -> "Capability":
        if not self.binding.get("protocol"):
            from .catalog import default_binding
            self.binding = {**default_binding(self.type), **self.binding}
        return self


class LaunchConfig(BaseModel):
    command: str = ""
    stop_command: str = ""
    cwd: str = ""
    # Interpreter substituted for {python}; empty = the global default from settings.
    python: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    host: str = "127.0.0.1"
    port: Optional[int] = None
    health_path: str = ""
    startup_timeout: int = 600
    # When true the manager treats a server already listening on the port as "running".
    adopt_external: bool = True


class ModelConfig(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    path: str
    adapter: str = "custom"
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    color: str = ""
    launch: LaunchConfig = Field(default_factory=LaunchConfig)
    api_base: str = ""  # overrides http://host:port when set
    capabilities: list[Capability] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)  # e.g. {"model": "qwen3.5"}
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def base_url(self) -> str:
        if self.api_base:
            return self.api_base.rstrip("/")
        host = self.launch.host or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = self.launch.port or 80
        return f"http://{host}:{port}"


class Settings(BaseModel):
    language: str = "zh"
    theme: Literal["light", "dark", "system"] = "system"
    port_range_start: int = 8100
    default_python: str = ""
    model_roots: list[str] = Field(default_factory=list)
    stop_models_on_exit: bool = True


class DetectRequest(BaseModel):
    path: str


class DetectResult(BaseModel):
    path: str
    exists: bool
    name: str
    adapter: str
    adapter_label: str
    description: str = ""
    capabilities: list[Capability] = Field(default_factory=list)
    launch: LaunchConfig = Field(default_factory=LaunchConfig)
    scripts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


class ProbeResult(BaseModel):
    reachable: bool
    base_url: str
    kinds: list[str] = Field(default_factory=list)
    served_models: list[str] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

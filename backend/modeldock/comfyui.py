"""ComfyUI HTTP API client.

ComfyUI has no OpenAI-compatible layer: a *prompt graph* (the API-format
workflow) is POSTed to /prompt, progress is polled from /history and finished
files are downloaded from /view.  This module turns a capability binding into
that flow:

    binding = {
        "protocol": "comfyui",
        "base_url": "http://127.0.0.1:8189",      # optional, model.base_url() otherwise
        "workflow": { "1": {"class_type": ..., "inputs": {...}}, ... },
        "defaults": {"width": 864},               # optional
        "timeout": 3600,                          # optional, seconds
    }

Placeholders use double braces ({{prompt}}).  A node carrying
"_if": "first_frame" is removed when that value is empty, and every input
referencing it is removed too - that is how the optional first/last frame
nodes of MiniMax-H3 disappear for text-to-video.
"""

from __future__ import annotations

import asyncio
import copy
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from .outputs import resolve_upload, save_output
from .schemas import Capability, ModelConfig

PLACEHOLDER = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")
OUTPUT_KINDS = {
    "images": ("image", ".png"),
    "gifs": ("video", ".gif"),
    "videos": ("video", ".mp4"),
    "audio": ("audio", ".wav"),
}
REQUEST_TIMEOUT = httpx.Timeout(connect=10, read=120, write=300, pool=10)


class ComfyError(Exception):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def base_of(model: ModelConfig, cap: Optional[Capability]) -> str:
    binding = (cap.binding if cap else {}) or {}
    return str(binding.get("base_url") or binding.get("url") or model.base_url()).rstrip("/")


def pick_action(cap: Capability, action_id: str = "") -> dict[str, Any]:
    actions = cap.actions or []
    if action_id:
        for action in actions:
            if action.get("id") == action_id:
                return action
        raise ComfyError(f"未知的子功能：{action_id}", 400)
    if actions:
        return actions[0]
    return {}


def _substitute(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = PLACEHOLDER.fullmatch(value.strip())
        if match:
            return values.get(match.group(1), "")
        return PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), value)
    if isinstance(value, dict):
        return {key: _substitute(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, values) for item in value]
    return value


def _drop_node(graph: dict[str, Any], node_id: str) -> None:
    graph.pop(node_id, None)
    for node in graph.values():
        inputs = node.get("inputs") or {}
        for key in list(inputs):
            value = inputs[key]
            if isinstance(value, list) and value and str(value[0]) == node_id:
                del inputs[key]
            elif isinstance(value, list):
                kept = [item for item in value
                        if not (isinstance(item, list) and item and str(item[0]) == node_id)]
                if len(kept) != len(value):
                    inputs[key] = kept


def build_graph(template: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a workflow, substitute placeholders, drop empty optional nodes."""
    graph = copy.deepcopy(template or {})
    for node_id in list(graph):
        node = graph[node_id]
        if not isinstance(node, dict):
            continue
        condition = node.pop("_if", None)
        if condition is not None and values.get(condition) in (None, "", [], {}):
            _drop_node(graph, node_id)
            continue
        node["inputs"] = _substitute(node.get("inputs") or {}, values)
    return graph


def coerce_values(fields: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for field in fields:
        name = field.get("name")
        if not name or name not in out or out[name] in (None, ""):
            continue
        if field.get("type") == "number":
            try:
                raw = str(out[name]).strip()
                out[name] = float(raw) if "." in raw else int(raw)
            except (TypeError, ValueError):
                out[name] = field.get("default", 0)
    return out


def action_fields(action: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in action.get("fields") or [] if isinstance(f, dict) and f.get("name")]


def graph_for(cap: Capability, action: dict[str, Any]) -> dict[str, Any]:
    template = action.get("workflow") or (cap.binding or {}).get("workflow") or {}
    nodes = action.get("nodes") or {}
    if nodes:
        template = {**template, **nodes}
    if not template:
        raise ComfyError("该能力没有配置 ComfyUI 工作流", 400)
    return template


async def _upload(client: httpx.AsyncClient, base: str, data: bytes, filename: str) -> str:
    files = {"image": (filename or "input.png", data, "image/png")}
    try:
        r = await client.post(base + "/upload/image", files=files, data={"overwrite": "true"})
    except httpx.HTTPError as exc:
        raise ComfyError(f"无法连接 ComfyUI {base}：{exc.__class__.__name__}") from exc
    if r.status_code >= 400:
        raise ComfyError(f"上传图片失败（HTTP {r.status_code}）：{r.text[:300]}")
    try:
        payload = r.json()
    except Exception:
        return filename
    name = str(payload.get("name") or filename)
    subfolder = str(payload.get("subfolder") or "")
    return f"{subfolder}/{name}" if subfolder else name


async def _fetch(client: httpx.AsyncClient, base: str, item: dict[str, Any]) -> bytes:
    params = {"filename": item.get("filename", ""), "subfolder": item.get("subfolder", ""),
              "type": item.get("type", "output")}
    try:
        r = await client.get(base + "/view", params=params)
    except httpx.HTTPError as exc:
        raise ComfyError(f"下载结果失败：{exc.__class__.__name__}") from exc
    if r.status_code >= 400:
        raise ComfyError(f"下载结果失败（HTTP {r.status_code}）：{r.text[:200]}")
    return r.content


def _error_from(payload: dict[str, Any]) -> str:
    err = payload.get("error") or {}
    if isinstance(err, dict):
        message = err.get("message") or err.get("type") or ""
        details = err.get("details") or ""
    else:
        message, details = str(err), ""
    nodes = payload.get("node_errors") or {}
    pieces = []
    for node_id, info in list(nodes.items())[:3]:
        if isinstance(info, dict):
            for item in info.get("errors") or []:
                pieces.append(f"节点 {node_id}: {item.get('message') or item.get('type')}")
    joined = "；".join([p for p in [message, str(details)[:200], *pieces] if p])
    return joined or "ComfyUI 拒绝了该工作流"


async def run(model: ModelConfig, cap: Capability, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit one action's workflow and return the saved outputs."""
    started = time.time()
    base = base_of(model, cap)
    action = pick_action(cap, str(payload.get("action") or ""))
    fields = action_fields(action)
    binding = cap.binding or {}

    values: dict[str, Any] = {}
    values.update(binding.get("defaults") or {})
    values.update(action.get("defaults") or {})
    values.update({k: v for k, v in payload.items() if k != "action"})
    values = coerce_values(fields, values)

    if "seed" in values and values["seed"] in (None, "", -1, "-1"):
        values["seed"] = random.randint(1, 2**31 - 1)

    prompt_text = str(values.get("prompt") or values.get("text") or "")
    if not prompt_text.strip() and any(f.get("required") for f in fields):
        raise ComfyError("请填写提示词", 400)

    image_fields = [f["name"] for f in fields if f.get("type") == "image"]
    timeout = float(binding.get("timeout") or action.get("timeout") or 3600)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        for name in image_fields:
            ref = values.get(name)
            if not ref:
                values[name] = ""
                continue
            path = resolve_upload(str(ref))
            if not path:
                raise ComfyError(f"找不到图片：{name}", 400)
            values[name] = await _upload(client, base, path.read_bytes(), path.name)

        graph = build_graph(graph_for(cap, action), values)
        body = {"prompt": graph, "client_id": uuid.uuid4().hex}
        try:
            r = await client.post(base + "/prompt", json=body)
        except httpx.HTTPError as exc:
            raise ComfyError(f"无法连接 ComfyUI {base}：{exc.__class__.__name__}。请确认服务已启动。") from exc
        if r.status_code >= 400:
            try:
                detail = _error_from(r.json())
            except Exception:
                detail = r.text[:400]
            raise ComfyError(f"工作流被拒绝（HTTP {r.status_code}）：{detail}")
        prompt_id = str(r.json().get("prompt_id") or "")
        if not prompt_id:
            raise ComfyError("ComfyUI 未返回 prompt_id")

        deadline = time.time() + timeout
        record: dict[str, Any] = {}
        while time.time() < deadline:
            await asyncio.sleep(1.5)
            try:
                h = await client.get(base + f"/history/{prompt_id}")
            except httpx.HTTPError:
                continue
            if h.status_code >= 400:
                continue
            try:
                data = h.json() or {}
            except Exception:
                continue
            record = data.get(prompt_id) or {}
            status = record.get("status") or {}
            if status.get("completed"):
                break
            if status.get("status_str") == "error":
                raise ComfyError("工作流执行失败：" + _error_from(status))
        else:
            raise ComfyError(f"等待结果超时（{int(timeout)} 秒）")

        status = record.get("status") or {}
        if status.get("status_str") == "error":
            raise ComfyError("工作流执行失败：" + _error_from(status))

        outputs = []
        for node_output in (record.get("outputs") or {}).values():
            if not isinstance(node_output, dict):
                continue
            for key, (kind, fallback_ext) in OUTPUT_KINDS.items():
                for item in node_output.get(key) or []:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    data = await _fetch(client, base, item)
                    ext = Path(str(item.get("filename"))).suffix or fallback_ext
                    outputs.append(save_output(
                        data, kind=kind, model_id=model.id, model_name=model.name,
                        capability=cap.type, ext=ext, prompt=prompt_text,
                        meta={"action": action.get("id") or "", "prompt_id": prompt_id,
                              "filename": item.get("filename")},
                    ))
        if not outputs:
            raise ComfyError("工作流已完成，但没有产生图片或视频")
        return {"outputs": outputs, "elapsed": round(time.time() - started, 2),
                "prompt_id": prompt_id, "action": action.get("id") or ""}


async def interrupt(model: ModelConfig, cap: Capability) -> dict[str, Any]:
    base = base_of(model, cap)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        try:
            r = await client.post(base + "/interrupt")
        except httpx.HTTPError as exc:
            raise ComfyError(f"无法连接 ComfyUI {base}：{exc.__class__.__name__}") from exc
        return {"ok": r.status_code < 400, "status": r.status_code}

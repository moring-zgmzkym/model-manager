"""FastAPI application: REST + WebSocket API and the built frontend."""

from __future__ import annotations

import os
import string
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import psutil
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, invoke, outputs, system
from .catalog import catalog_payload
from .detect import detect, merge_caps
from .paths import FRONTEND_DIST, PROJECT_DIR, data_dir
from .plugins import load_plugins
from .probe import probe
from .process import ProcessManager, Service, ServiceSpec, port_open
from .schemas import Capability, DetectRequest, ModelConfig, Settings

store_ref: dict[str, Any] = {}


def get_store():
    return store_ref["store"]


def python_default() -> str:
    s = get_store().settings
    return s.default_python or sys.executable


pm = ProcessManager(python_default)
plugin_state: dict[str, Any] = {"detectors": [], "info": []}


def reload_plugins() -> None:
    plugin_state["detectors"], plugin_state["info"] = load_plugins()


# ---------------------------------------------------------------- services
def main_spec(m: ModelConfig) -> ServiceSpec:
    lc = m.launch
    return ServiceSpec(key=m.id, model_id=m.id, name=m.name, command=lc.command, cwd=lc.cwd, env=lc.env,
                       host=lc.host, port=lc.port, health_path=lc.health_path, startup_timeout=lc.startup_timeout,
                       stop_command=lc.stop_command, adopt_external=lc.adopt_external, python=lc.python)


def sub_specs(m: ModelConfig) -> dict[str, ServiceSpec]:
    out = {}
    for c in m.capabilities:
        b = c.binding
        if c.enabled and b.get("command"):
            key = f"{m.id}:{c.type}"
            out[c.type] = ServiceSpec(key=key, model_id=m.id, name=f"{m.name} · {c.label or c.type}",
                                      command=b["command"], cwd=b.get("cwd") or m.launch.cwd, env=m.launch.env,
                                      host="127.0.0.1", port=b.get("port"), health_path=b.get("health_path", ""),
                                      startup_timeout=m.launch.startup_timeout, python=m.launch.python)
    return out


def sync_services(m: ModelConfig) -> None:
    pm.register(main_spec(m))
    for spec in sub_specs(m).values():
        pm.register(spec)


def model_view(m: ModelConfig) -> dict[str, Any]:
    svc = pm.get(m.id)
    subs = {}
    for ctype in sub_specs(m):
        s = pm.get(f"{m.id}:{ctype}")
        subs[ctype] = s.snapshot() if s else {"status": "stopped"}
    return {**m.model_dump(), "base_url": m.base_url(),
            "runtime": svc.snapshot() if svc else {"status": "stopped", "key": m.id},
            "services": subs}


def free_port(start: int) -> int:
    used = get_store().used_ports()
    port = start
    while port in used or port_open("127.0.0.1", port, 0.1):
        port += 1
    return port


def merge_runtime(m: ModelConfig, found: list[Capability]) -> bool:
    """Add capabilities discovered at runtime without overriding locked (user-edited) ones."""
    locked = {c.type for c in m.capabilities if c.locked}
    fresh = [c for c in found if c.type not in locked]
    before = [(c.type, c.enabled, c.confidence) for c in m.capabilities]
    merged = merge_caps(m.capabilities, fresh)
    for c in merged:  # keep the user's lock flags
        orig = next((o for o in m.capabilities if o.type == c.type), None)
        if orig and orig.locked:
            c.locked, c.enabled, c.binding = True, orig.enabled, orig.binding
    m.capabilities = merged
    return before != [(c.type, c.enabled, c.confidence) for c in m.capabilities]


async def on_running(svc: Service) -> None:
    m = get_store().get(svc.spec.model_id)
    if not m or svc.spec.key != m.id:
        return
    try:
        res = await probe(m.base_url())
    except Exception:
        return
    if res.reachable and res.capabilities and merge_runtime(m, res.capabilities):
        get_store().upsert(m)
        pm._emit({"type": "model", "id": m.id})
    if res.served_models and not m.defaults.get("model"):
        m.defaults["model"] = res.served_models[0]
        get_store().upsert(m)


pm.on_running = on_running


# ---------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .store import Store
    if "store" not in store_ref:
        store_ref["store"] = Store()
    reload_plugins()
    for m in get_store().list_models():
        sync_services(m)
    pm.start_monitor()
    psutil.cpu_percent(interval=None)
    yield
    if get_store().settings.stop_models_on_exit:
        await pm.stop_all(managed_only=True)
    await pm.shutdown()


app = FastAPI(title="ModelDock", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(invoke.InvokeError)
async def _invoke_error(_, exc: invoke.InvokeError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


def must(model_id: str) -> ModelConfig:
    m = get_store().get(model_id)
    if not m:
        raise HTTPException(404, "模型不存在")
    return m


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "data_dir": str(data_dir())}


@app.get("/api/catalog")
def catalog():
    return {**catalog_payload(), "plugins": plugin_state["info"]}


@app.post("/api/plugins/reload")
def plugins_reload():
    reload_plugins()
    return plugin_state["info"]


@app.get("/api/system")
def system_stats():
    s = system.stats()
    s["running_models"] = sum(1 for m in get_store().list_models() if (pm.get(m.id) and pm.get(m.id).status == "running"))
    return s


@app.get("/api/settings")
def get_settings():
    return {**get_store().settings.model_dump(), "data_dir": str(data_dir()), "python_executable": sys.executable,
            "platform": sys.platform}


@app.put("/api/settings")
def put_settings(settings: Settings):
    return get_store().update_settings(settings)


# ---------------------------------------------------------------- models
@app.get("/api/models")
def list_models():
    return [model_view(m) for m in get_store().list_models()]


@app.get("/api/models/{model_id}")
def get_model(model_id: str):
    return model_view(must(model_id))


def needs_port(m: ModelConfig) -> bool:
    return "{port}" in m.launch.command or not m.launch.command


@app.post("/api/models")
def create_model(model: ModelConfig):
    store = get_store()
    if model.id in store.models:
        raise HTTPException(409, "ID 已存在")
    if not model.launch.port and model.launch.command and needs_port(model):
        model.launch.port = free_port(store.settings.port_range_start)
    store.upsert(model)
    sync_services(model)
    return model_view(model)


@app.put("/api/models/{model_id}")
def update_model(model_id: str, model: ModelConfig):
    old = must(model_id)
    model.id = model_id
    model.created_at = old.created_at
    if not model.launch.port and model.launch.command and needs_port(model):
        model.launch.port = free_port(get_store().settings.port_range_start)
    get_store().upsert(model)
    sync_services(model)
    return model_view(model)


@app.delete("/api/models/{model_id}")
async def delete_model(model_id: str):
    must(model_id)
    for key in [k for k in list(pm.services) if k == model_id or k.startswith(model_id + ":")]:
        svc = pm.get(key)
        if svc and svc.proc:
            await pm.stop(key)
    pm.forget(model_id)
    get_store().delete(model_id)
    return {"ok": True}


@app.post("/api/detect")
def detect_path(req: DetectRequest):
    return detect(req.path, get_store().settings, plugin_state["detectors"])


@app.post("/api/detect-batch")
def detect_batch(req: DetectRequest):
    """Scan every sub-folder of a directory and return the ones that look like models."""
    root = Path(req.path.strip().strip('"')).expanduser()
    if not root.is_dir():
        raise HTTPException(400, "不是文件夹")
    known = {str(Path(m.path)) for m in get_store().list_models()}
    found = []
    for child in sorted(root.iterdir(), key=lambda c: c.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        res = detect(str(child), get_store().settings, plugin_state["detectors"])
        if res.adapter != "custom" or res.capabilities:
            found.append({**res.model_dump(), "already_added": str(child) in known})
    return found


@app.post("/api/models/{model_id}/rescan")
async def rescan(model_id: str):
    m = must(model_id)
    notes: list[str] = []
    if not m.path.startswith("http"):
        res = detect(m.path, get_store().settings, plugin_state["detectors"])
        merge_runtime(m, res.capabilities)
        notes += res.notes
    svc = pm.get(m.id)
    if svc and svc.status == "running" or m.adapter == "openai_remote":
        pr = await probe(m.base_url())
        merge_runtime(m, pr.capabilities)
        notes += pr.notes
        if pr.served_models and not m.defaults.get("model"):
            m.defaults["model"] = pr.served_models[0]
    get_store().upsert(m)
    return {"model": model_view(m), "notes": notes}


@app.post("/api/probe")
async def probe_url(body: dict = Body(...)):
    return await probe(body.get("url", ""))


@app.post("/api/models/{model_id}/start")
async def start_model(model_id: str):
    m = must(model_id)
    if m.adapter == "openai_remote" and not m.launch.command:
        raise HTTPException(400, "这是外部服务，ModelDock 不负责启动")
    svc = await pm.start(main_spec(m))
    return svc.snapshot()


@app.post("/api/models/{model_id}/stop")
async def stop_model(model_id: str):
    must(model_id)
    svc = await pm.stop(model_id)
    return svc.snapshot() if svc else {"status": "stopped"}


@app.post("/api/models/{model_id}/restart")
async def restart_model(model_id: str):
    m = must(model_id)
    await pm.stop(model_id)
    return (await pm.start(main_spec(m))).snapshot()


@app.post("/api/models/{model_id}/services/{ctype}/{action}")
async def service_action(model_id: str, ctype: str, action: str):
    m = must(model_id)
    spec = sub_specs(m).get(ctype)
    if not spec:
        raise HTTPException(404, "该能力没有独立服务")
    if action == "start":
        return (await pm.start(spec)).snapshot()
    if action == "stop":
        svc = await pm.stop(spec.key)
        return svc.snapshot() if svc else {"status": "stopped"}
    raise HTTPException(400, "未知操作")


@app.get("/api/logs/{key}")
def get_logs(key: str, limit: int = 1000):
    svc = pm.get(key)
    return list(svc.logs)[-limit:] if svc else []


@app.delete("/api/logs/{key}")
def clear_logs(key: str):
    svc = pm.get(key)
    if svc:
        svc.logs.clear()
    return {"ok": True}


@app.get("/api/services")
def services():
    return [s.snapshot() | {"name": s.spec.name} for s in pm.services.values()]


# ---------------------------------------------------------------- filesystem helper
@app.get("/api/fs")
def browse(path: str = ""):
    """Tiny directory browser so users can pick model folders without typing paths."""
    if not path:
        if os.name == "nt":
            roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        else:
            roots = ["/", str(Path.home())]
        return {"path": "", "parent": None, "entries": [{"name": r, "path": r, "dir": True} for r in roots]}
    p = Path(path).expanduser()
    if not p.is_dir():
        raise HTTPException(400, "不是文件夹")
    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                entries.append({"name": child.name, "path": str(child), "dir": child.is_dir()})
            except OSError:
                continue
            if len(entries) > 500:
                break
    except PermissionError:
        raise HTTPException(403, "没有权限读取该文件夹")
    parent = str(p.parent) if p.parent != p else ""
    return {"path": str(p), "parent": parent, "entries": entries}


@app.post("/api/open-folder")
def open_folder(body: dict = Body(...)):
    target = body.get("path") or str(data_dir())
    if body.get("outputs"):
        target = str(outputs.outputs_dir())
    if not os.path.exists(target):
        raise HTTPException(404, "路径不存在")
    try:
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        raise HTTPException(500, f"无法打开：{exc}")
    return {"ok": True}


# ---------------------------------------------------------------- invoke
@app.post("/api/models/{model_id}/omni/chat")
async def omni_chat(model_id: str, payload: dict = Body(...)):
    m = must(model_id)
    cap = next((c for c in m.capabilities
                if c.enabled and c.type == "chat" and c.binding.get("protocol") == "omni"), None)
    if not cap:
        raise HTTPException(400, "该模型没有启用 Omni 对话")
    return StreamingResponse(invoke.omni_chat_stream(m, cap, payload), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/models/{model_id}/omni/voices")
async def omni_voices(model_id: str):
    return await invoke.omni_voices(must(model_id))


@app.post("/api/models/{model_id}/chat")
async def chat(model_id: str, payload: dict = Body(...)):
    m = must(model_id)
    return StreamingResponse(invoke.chat_stream(m, payload), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/models/{model_id}/image")
async def image(model_id: str, payload: dict = Body(...)):
    return await invoke.image_generate(must(model_id), payload)


@app.post("/api/models/{model_id}/video")
async def video(model_id: str, payload: dict = Body(...)):
    return await invoke.video_generate(must(model_id), payload)


@app.post("/api/models/{model_id}/video/stop")
async def video_stop(model_id: str):
    return await invoke.video_interrupt(must(model_id))


@app.post("/api/models/{model_id}/tts")
async def tts(model_id: str, payload: dict = Body(...)):
    return await invoke.tts(must(model_id), payload)


@app.post("/api/models/{model_id}/asr")
async def asr(model_id: str, payload: dict = Body(...)):
    return await invoke.transcribe(must(model_id), payload.get("file", ""), payload.get("language", ""))


@app.post("/api/models/{model_id}/embed")
async def embed(model_id: str, payload: dict = Body(...)):
    return await invoke.embed(must(model_id), payload)


@app.post("/api/models/{model_id}/custom/{index}")
async def custom(model_id: str, index: int, payload: dict = Body(...)):
    m = must(model_id)
    customs = [c for c in m.capabilities if c.binding.get("protocol") == "custom_http"]
    if index >= len(customs):
        raise HTTPException(404, "接口不存在")
    return await invoke.custom_call(m, customs[index], payload)


@app.get("/api/models/{model_id}/sovits/weights")
def sovits_weights(model_id: str):
    return invoke.sovits_weights(must(model_id))


@app.post("/api/models/{model_id}/sovits/weights")
async def sovits_set(model_id: str, body: dict = Body(...)):
    return await invoke.sovits_set_weights(must(model_id), body.get("gpt", ""), body.get("sovits", ""))


# ---------------------------------------------------------------- files
@app.post("/api/uploads")
async def upload(file: UploadFile = File(...), name: Optional[str] = Form(None)):
    data = await file.read()
    return outputs.save_upload(data, name or file.filename or "file")


@app.get("/api/outputs")
def list_outputs(kind: Optional[str] = None, model_id: Optional[str] = None, limit: int = 500):
    return outputs.list_outputs(limit=limit, kind=kind, model_id=model_id)


@app.delete("/api/outputs/{oid}")
def delete_output(oid: str):
    return {"ok": outputs.delete_output(oid)}


@app.get("/api/files/{area}/{rel:path}")
def serve_file(area: str, rel: str):
    base = {"outputs": outputs.outputs_dir(), "uploads": outputs.uploads_dir()}.get(area)
    if not base:
        raise HTTPException(404)
    target = outputs.safe_child(base, rel)
    if not target:
        raise HTTPException(404)
    return FileResponse(target)


# ---------------------------------------------------------------- live events
@app.websocket("/api/events")
async def events(ws: WebSocket):
    await ws.accept()
    q = pm.subscribe()
    try:
        await ws.send_json({"type": "hello", "services": [s.snapshot() for s in pm.services.values()]})
        while True:
            event = await q.get()
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        pm.unsubscribe(q)


# ---------------------------------------------------------------- frontend
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full:path}")
    def spa(full: str):
        target = outputs.safe_child(FRONTEND_DIST, full) if full else None
        return FileResponse(target or FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def no_frontend():
        return JSONResponse({"message": "前端尚未构建：请在 frontend 目录运行 npm install && npm run build，或使用 start 脚本。",
                             "project": str(PROJECT_DIR)})

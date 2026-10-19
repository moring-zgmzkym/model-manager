"""End to end: register a model whose launch command is the bundled mock
server, start it through the API, call every capability, then stop it."""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import free_port
from modeldock import invoke


@pytest.fixture
def client(data_dir):
    import importlib
    import modeldock.app as appmod
    appmod.store_ref.clear()
    appmod.pm.services.clear()
    with TestClient(appmod.app) as c:
        yield c


def wait_status(client, mid, want, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/models/{mid}").json()["runtime"]["status"]
        if st == want:
            return st
        if st == "error":
            raise AssertionError(client.get(f"/api/logs/{mid}").json())
        time.sleep(0.3)
    raise AssertionError(f"timeout waiting for {want}, last={st}")


def test_full_lifecycle(client, tmp_path):
    port = free_port()
    model = {
        "name": "Mock Omni", "path": str(tmp_path), "adapter": "custom",
        "launch": {"command": '{python} "{servers}/mock_server.py" --kind all --host {host} --port {port}',
                   "port": port, "health_path": "/v1/models"},
        "capabilities": [{"type": t, "source": "user"} for t in
                         ("chat", "vision", "speech_output", "image_generation", "tts", "asr", "embedding")],
    }
    m = client.post("/api/models", json=model).json()
    mid = m["id"]
    assert m["runtime"]["status"] == "stopped"

    client.post(f"/api/models/{mid}/start")
    wait_status(client, mid, "running")
    logs = client.get(f"/api/logs/{mid}").json()
    assert any("Mock all server" in l["line"] for l in logs)

    # chat (streaming SSE, with speech output)
    with client.stream("POST", f"/api/models/{mid}/chat",
                       json={"messages": [{"role": "user", "content": "你好"}], "speech": True}) as r:
        events = [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "你好" in text
    assert any(e["type"] == "reasoning" for e in events)
    audio = next(e for e in events if e["type"] == "audio")
    assert client.get(audio["url"]).content[:4] == b"RIFF"
    assert events[-1]["type"] == "done"

    # image generation
    res = client.post(f"/api/models/{mid}/image", json={"prompt": "a cat", "size": "512x512", "n": 2}).json()
    assert len(res["outputs"]) == 2
    assert client.get(res["outputs"][0]["url"]).content[:4] == b"\x89PNG"

    # tts
    res = client.post(f"/api/models/{mid}/tts", json={"text": "测试"}).json()
    assert res["outputs"][0]["url"].endswith(".wav")

    # asr via upload
    up = client.post("/api/uploads", files={"file": ("a.wav", b"RIFF" + b"0" * 4000, "audio/wav")}).json()
    res = client.post(f"/api/models/{mid}/asr", json={"file": up["id"]}).json()
    assert "模拟识别" in res["text"]

    # embeddings
    res = client.post(f"/api/models/{mid}/embed", json={"inputs": ["猫", "狗", "猫"]}).json()
    assert res["similarity"][0][2] == pytest.approx(1.0)

    # outputs library
    outs = client.get("/api/outputs").json()
    assert {o["kind"] for o in outs} == {"image", "audio"}

    # runtime probe merged extra info without losing user caps
    rescan = client.post(f"/api/models/{mid}/rescan").json()["model"]
    caps = {c["type"]: c for c in rescan["capabilities"]}
    assert {"chat", "tts", "image_generation"} <= set(caps)
    assert all(caps[t]["enabled"] for t in ("chat", "tts", "image_generation"))  # user caps stay on

    client.post(f"/api/models/{mid}/stop")
    wait_status(client, mid, "stopped")
    from modeldock.process import port_open
    assert not port_open("127.0.0.1", port)


def test_gpt_sovits_flow(client, tmp_path):
    port = free_port()
    m = client.post("/api/models", json={
        "name": "SoVITS", "path": str(tmp_path), "adapter": "gpt_sovits",
        "launch": {"command": '{python} "{servers}/mock_server.py" --kind sovits --port {port}', "port": port},
        "capabilities": [{"type": "voice_clone", "binding": {"protocol": "gpt_sovits", "api": "v2"}}],
    }).json()
    client.post(f"/api/models/{m['id']}/start")
    wait_status(client, m["id"], "running")
    up = client.post("/api/uploads", files={"file": ("ref.wav", b"RIFF0000", "audio/wav")}).json()
    res = client.post(f"/api/models/{m['id']}/tts",
                      json={"text": "你好世界", "ref_audio": up["id"], "prompt_text": "参考", "text_lang": "zh"}).json()
    assert res["outputs"][0]["capability"] == "voice_clone"
    # missing reference audio gives a friendly error
    r = client.post(f"/api/models/{m['id']}/tts", json={"text": "x"})
    assert r.status_code == 400 and "参考音频" in r.json()["detail"]
    client.post(f"/api/models/{m['id']}/stop")


def test_failed_start_reports_error(client, tmp_path):
    m = client.post("/api/models", json={
        "name": "Broken", "path": str(tmp_path),
        "launch": {"command": '{python} -c "import sys; print(\'boom: missing weights\'); sys.exit(3)"', "port": free_port()},
    }).json()
    client.post(f"/api/models/{m['id']}/start")
    deadline = time.time() + 15
    while time.time() < deadline:
        rt = client.get(f"/api/models/{m['id']}").json()["runtime"]
        if rt["status"] == "error":
            break
        time.sleep(0.3)
    assert rt["status"] == "error" and "boom" in rt["message"]


def test_detect_endpoint_and_fs(client, models_root):
    res = client.post("/api/detect", json={"path": str(models_root / "qwen3.5")}).json()
    assert res["adapter"] == "hf_llm"
    listing = client.get("/api/fs", params={"path": str(models_root)}).json()
    assert {e["name"] for e in listing["entries"]} >= {"qwen3.5", "MiniMind-O"}


def test_detect_batch(client, models_root):
    res = client.post("/api/detect-batch", json={"path": str(models_root)}).json()
    names = {r["name"]: r["adapter"] for r in res}
    assert names == {"MiniMax-H3": "hf_llm", "MiniMind-O": "script_project", "qwen3.5": "hf_llm",
                     "qwen-image-2.1": "diffusers", "GPT-SoVITS-v2pro-20250604": "gpt_sovits"}


def test_probe_generic_server_is_conservative(client, tmp_path):
    """A server exposing every OpenAI route must not auto-enable all of them."""
    port = free_port()
    m = client.post("/api/models", json={
        "name": "Generic", "path": str(tmp_path),
        "launch": {"command": '{python} "{servers}/mock_server.py" --kind all --port {port}', "port": port,
                   "health_path": "/v1/models"},
    }).json()
    client.post(f"/api/models/{m['id']}/start")
    wait_status(client, m["id"], "running")
    time.sleep(1.5)  # on_running probe
    caps = {c["type"]: c for c in client.get(f"/api/models/{m['id']}").json()["capabilities"]}
    assert caps["chat"]["enabled"]
    assert "embedding" in caps and not caps["embedding"]["enabled"]
    client.post(f"/api/models/{m['id']}/stop")


def test_probe_single_route_server(client, tmp_path):
    port = free_port()
    m = client.post("/api/models", json={
        "name": "Painter", "path": str(tmp_path),
        "launch": {"command": '{python} "{servers}/mock_server.py" --kind image --port {port}', "port": port,
                   "health_path": "/health"},
    }).json()
    client.post(f"/api/models/{m['id']}/start")
    wait_status(client, m["id"], "running")
    time.sleep(1.5)
    caps = {c["type"]: c for c in client.get(f"/api/models/{m['id']}").json()["capabilities"]}
    assert caps["image_generation"]["enabled"] and caps["image_edit"]["enabled"]
    assert caps["image_generation"]["source"] == "runtime"
    client.post(f"/api/models/{m['id']}/stop")


def test_chat_job_keeps_generating_after_the_viewer_disconnects(client, tmp_path, monkeypatch):
    async def fake(model, payload):
        for i in range(3):
            yield invoke.sse({"type": "delta", "text": str(i)})
            await asyncio.sleep(0.1)
        yield invoke.sse({"type": "done", "elapsed": 0.3, "usage": {"completion_tokens": 3}})

    monkeypatch.setattr(invoke, "chat_stream", fake)
    m = client.post("/api/models", json={
        "name": "Slow Chat", "path": str(tmp_path),
        "capabilities": [{"type": "chat", "binding": {"protocol": "openai_chat"}}],
    }).json()
    jid = "api-job-" + uuid.uuid4().hex
    with client.stream("POST", f"/api/models/{m['id']}/chat",
                       json={"messages": [{"role": "user", "content": "hi"}], "request_id": jid}) as r:
        first = next(r.iter_lines())
        assert first.startswith("data:")

    snap = {}
    deadline = time.time() + 5
    while time.time() < deadline:
        snap = client.get(f"/api/chat/jobs/{jid}").json()
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] == "done"
    assert snap["content"] == "012"

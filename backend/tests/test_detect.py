import json

from conftest import write
from modeldock.detect import cap, detect, merge_caps


def caps(res, enabled_only=True):
    return {c.type for c in res.capabilities if c.enabled or not enabled_only}


def test_minimax_is_text_llm(models_root):
    res = detect(str(models_root / "MiniMax-H3"))
    assert res.adapter == "hf_llm"
    assert caps(res) == {"chat"}
    assert "{port}" in res.launch.command


def test_minimind_o_is_omni(models_root):
    res = detect(str(models_root / "MiniMind-O"))
    assert {"chat", "vision", "audio_input", "speech_output"} <= caps(res)
    assert "serve_openai_api.py" in res.launch.command
    assert res.launch.port == 8998
    webui = next(c for c in res.capabilities if c.type == "webui")
    assert "streamlit" in webui.binding["command"]
    # evidence is explained to the user
    speech = next(c for c in res.capabilities if c.type == "speech_output")
    assert any("README" in e for e in speech.evidence)


def test_qwen35_has_vision(models_root):
    res = detect(str(models_root / "qwen3.5"))
    assert res.adapter == "hf_llm"
    assert {"chat", "vision"} <= caps(res)
    assert "speech_output" not in caps(res)


def test_qwen_image_is_diffusers(models_root):
    res = detect(str(models_root / "qwen-image-2.1"))
    assert res.adapter == "diffusers"
    assert caps(res) == {"image_generation"}
    assert "diffusers_server.py" in res.launch.command


def test_gpt_sovits(models_root):
    res = detect(str(models_root / "GPT-SoVITS-v2pro-20250604"))
    assert res.adapter == "gpt_sovits"
    assert {"tts", "voice_clone", "webui"} <= caps(res)
    assert "api_v2.py" in res.launch.command and "runtime" in res.launch.command
    assert res.launch.port == 9880
    tts = next(c for c in res.capabilities if c.type == "tts")
    assert tts.binding["protocol"] == "gpt_sovits"


def test_manifest_overrides(models_root):
    mo = models_root / "MiniMax-H3"
    (mo / "modeldock.json").write_text(json.dumps({
        "name": "MiniMax H3 (manifest)", "launch": {"command": "python serve.py --port {port}", "port": 9001},
        "capabilities": ["chat", {"type": "embedding"}],
    }), encoding="utf-8")
    res = detect(str(mo))
    assert res.name == "MiniMax H3 (manifest)"
    assert res.launch.port == 9001
    emb = next(c for c in res.capabilities if c.type == "embedding")
    assert emb.source == "manifest" and emb.enabled


def test_single_weak_hint_stays_disabled(tmp_path):
    d = tmp_path / "plain-llm"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"architectures": ["LlamaForCausalLM"]}))
    (d / "model.safetensors").write_bytes(b"0")
    (d / "README.md").write_text("You can pair this model with a tts engine.")
    res = detect(str(d))
    assert "tts" in caps(res, enabled_only=False)
    assert "tts" not in caps(res)


def test_merge_caps_preserves_actions():
    base = [cap("video_generation", 0.9, "files", actions=[{"id": "t2v"}, {"id": "flf2v"}])]
    runtime = [cap("video_generation", 0.7, "runtime probe", source="runtime")]
    merged = merge_caps(base, runtime)
    assert [a["id"] for a in merged[0].actions] == ["t2v", "flf2v"]


def test_plugin_detector(models_root):
    def plugin(root, scan):
        if root.name == "qwen3.5":
            return {"capabilities": [{"type": "embedding", "evidence": ["plugin says so"]}], "notes": ["hi"]}
    res = detect(str(models_root / "qwen3.5"), plugin_detectors=[plugin])
    assert "embedding" in caps(res) and "hi" in res.notes


def test_url_and_missing(tmp_path):
    assert detect("http://127.0.0.1:11434").adapter == "openai_remote"
    assert detect(str(tmp_path / "nope")).exists is False


def test_comfyui_video_detection(tmp_path):
    root = tmp_path / "MiniMax-H3"
    write(root / "start-comfyui.bat", r'.\python_embeded\python.exe -s ComfyUI\main.py --port 8189')
    write(root / "stop-comfyui.bat", "echo stop")
    write(root / "ComfyUI" / "ComfyUI" / "main.py", "")
    models = root / "ComfyUI" / "ComfyUI" / "models"
    write(models / "unet" / "minimax_h3_fl2va_pruned-Q4_K.gguf", binary=True)
    write(models / "text_encoders" / "qwen3vl_32b_minimax_h3-Q2_K_M.gguf", binary=True)
    write(models / "vae" / "minimax_h3_video_vae_fp16.safetensors", binary=True)
    write(models / "vae" / "minimax_h3_audio_vae_fp32.safetensors", binary=True)
    write(models / "loras" / "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", binary=True)

    res = detect(str(root))
    assert res.adapter == "comfyui"
    assert res.launch.port == 8189
    assert res.launch.health_path == "/system_stats"
    assert "start-comfyui.bat" in res.launch.command
    cap = next(c for c in res.capabilities if c.type == "video_generation")
    assert cap.enabled and cap.binding["protocol"] == "comfyui"
    assert [a["id"] for a in cap.actions] == ["t2v", "i2v", "flf2v"]
    fields = {f["name"] for f in cap.actions[2]["fields"]}
    assert {"first_frame", "last_frame"} <= fields
    assert "MiniMaxH3ImageToVideo" in str(cap.binding["workflow"])


def test_comfyui_image_detection(tmp_path):
    root = tmp_path / "qwen-image-2.1"
    write(root / "start-comfyui.bat", r'.\python_embeded\python.exe -s ComfyUI\main.py')
    write(root / "ComfyUI" / "ComfyUI" / "main.py", "")
    models = root / "ComfyUI" / "ComfyUI" / "models"
    write(models / "diffusion_models" / "qwen-image-2.1-Q5_K_M.gguf", binary=True)
    write(models / "text_encoders" / "qwen3vl_8b_w4a8.safetensors", binary=True)
    write(models / "vae" / "qwen_image_2.1_vae_bf16.safetensors", binary=True)

    res = detect(str(root))
    assert res.adapter == "comfyui"
    assert res.launch.port == 8188
    cap = next(c for c in res.capabilities if c.type == "image_generation")
    assert cap.enabled and cap.binding["protocol"] == "comfyui"
    assert [a["id"] for a in cap.actions] == ["t2i", "edit"]
    edit = cap.actions[1]
    assert edit["workflow"]["4"]["inputs"]["images"] == {"image_1": ["20", 0]}
    assert edit["workflow"]["5"]["inputs"]["latent_image"] == ["4", 2]
    edit_cap = next(c for c in res.capabilities if c.type == "image_edit")
    assert edit_cap.enabled


def test_minimind_omni_detection(tmp_path):
    root = tmp_path / "MiniMind-O"
    write(root / "repo" / "scripts" / "web_demo_omni.py", "print('demo')")
    write(root / "repo" / "scripts" / "minimind-3o" / "config.json", {})
    write(root / "repo" / "scripts" / "minimind-3o" / "pytorch_model.bin", binary=True)

    res = detect(str(root))
    assert res.adapter == "omni"
    assert res.launch.health_path == "/health"
    assert "omni_server.py" in res.launch.command
    assert "{port}" in res.launch.command
    chat = next(c for c in res.capabilities if c.type == "chat")
    assert chat.enabled and chat.binding["protocol"] == "omni"
    enabled = {c.type for c in res.capabilities if c.enabled}
    assert {"chat", "vision", "audio_input", "speech_output"} <= enabled


def test_ollama_detection(tmp_path):
    root = tmp_path / "qwen3.5"
    write(root / "Modelfile", "FROM qwen3.5:9b\nPARAMETER num_ctx 8192\n")
    write(root / "start-ollama.bat", "set OLLAMA_HOST=0.0.0.0:11434\ntimeout /t 1\n")
    (root / "ollama").mkdir()

    res = detect(str(root))
    assert res.adapter == "ollama"
    assert res.launch.port == 11434
    assert res.launch.health_path == "/v1/models"
    assert res.defaults["model"] == "qwen3.5:9b"
    chat = next(c for c in res.capabilities if c.type == "chat")
    assert chat.enabled and chat.binding["protocol"] == "openai_chat"


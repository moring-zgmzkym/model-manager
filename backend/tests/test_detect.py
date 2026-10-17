import json

from modeldock.detect import detect


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


def test_plugin_detector(models_root):
    def plugin(root, scan):
        if root.name == "qwen3.5":
            return {"capabilities": [{"type": "embedding", "evidence": ["plugin says so"]}], "notes": ["hi"]}
    res = detect(str(models_root / "qwen3.5"), plugin_detectors=[plugin])
    assert "embedding" in caps(res) and "hi" in res.notes


def test_url_and_missing(tmp_path):
    assert detect("http://127.0.0.1:11434").adapter == "openai_remote"
    assert detect(str(tmp_path / "nope")).exists is False

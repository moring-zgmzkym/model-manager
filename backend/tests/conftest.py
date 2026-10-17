import json
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELDOCK_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def write(p: Path, content="", binary=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        p.write_bytes(b"\0" * 16)
    elif isinstance(content, (dict, list)):
        p.write_text(json.dumps(content), encoding="utf-8")
    else:
        p.write_text(content, encoding="utf-8")


@pytest.fixture
def models_root(tmp_path):
    """Folders shaped like the user's five models."""
    root = tmp_path / "models"
    # MiniMax-H3: large HF causal LM
    mm = root / "MiniMax-H3"
    write(mm / "config.json", {"architectures": ["MiniMaxM2ForCausalLM"], "model_type": "minimax_m2", "num_experts": 256})
    write(mm / "tokenizer_config.json", {"chat_template": "{% for m in messages %}{{m.content}}{% endfor %}"})
    write(mm / "model-00001-of-00002.safetensors", binary=True)
    write(mm / "README.md", "# MiniMax-H3\nA strong reasoning and coding language model with tool use.")
    # MiniMind-O: GitHub-style project with OpenAI server script
    mo = root / "MiniMind-O"
    write(mo / "README.md", "# MiniMind-O\n极简全模态模型：支持语音输入、语音输出与图像理解（视觉）。")
    write(mo / "out" / "omni_512.pth", binary=True)
    write(mo / "scripts" / "serve_openai_api.py",
          "import argparse\nfrom fastapi import FastAPI\napp = FastAPI()\n"
          "@app.post('/v1/chat/completions')\ndef chat(req):\n    image_url = None\n    input_audio = None\n"
          "    modalities = ['text','audio']\n\n"
          "if __name__ == '__main__':\n    import uvicorn\n    uvicorn.run(app, host='0.0.0.0', port=8998)\n")
    write(mo / "scripts" / "web_demo.py", "import streamlit as st\nst.title('MiniMind-O')\n")
    write(mo / "requirements.txt", "torch\n")
    # qwen3.5: native VLM
    qw = root / "qwen3.5"
    write(qw / "config.json", {"architectures": ["Qwen3_5ForConditionalGeneration"], "model_type": "qwen3_5",
                               "vision_config": {"depth": 27}, "text_config": {"hidden_size": 4096}, "image_token_id": 151655})
    write(qw / "preprocessor_config.json", {"image_processor_type": "Qwen2VLImageProcessor", "image_mean": [0.5]})
    write(qw / "tokenizer_config.json", {"chat_template": "x"})
    write(qw / "model.safetensors", binary=True)
    # qwen-image-2.1: diffusers pipeline
    qi = root / "qwen-image-2.1"
    write(qi / "model_index.json", {"_class_name": "QwenImagePipeline", "_diffusers_version": "0.36.0"})
    write(qi / "transformer" / "diffusion_pytorch_model.safetensors", binary=True)
    write(qi / "vae" / "config.json", {"_class_name": "AutoencoderKLQwenImage"})
    # GPT-SoVITS integrated package
    gs = root / "GPT-SoVITS-v2pro-20250604"
    write(gs / "api_v2.py", "# GPT-SoVITS api\n@APP.post('/tts')\nref_audio_path = ''\n")
    write(gs / "webui.py", "import gradio as gr\n")
    write(gs / "go-webui.bat", "runtime\\python.exe webui.py zh_CN\n")
    write(gs / "GPT_SoVITS" / "configs" / "tts_infer.yaml", "custom: {}\n")
    write(gs / "GPT_weights_v2Pro" / "alice-e15.ckpt", binary=True)
    write(gs / "SoVITS_weights_v2Pro" / "alice_e8_s200.pth", binary=True)
    write(gs / "runtime" / "python.exe", binary=True)
    return root

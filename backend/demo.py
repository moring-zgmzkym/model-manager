#!/usr/bin/env python3
"""Create a self-contained demo: fake model folders shaped like real ones,
detected by ModelDock, but launched with the bundled mock server so every
feature works without a GPU.

    python backend/demo.py            # writes into ./demo-data, then run:
    MODELDOCK_DATA=demo-data python run.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def write(p: Path, content: object = "", binary: bool = False) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        p.write_bytes(b"\0" * 64)
    elif isinstance(content, (dict, list)):
        p.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    else:
        p.write_text(str(content), encoding="utf-8")


def make_folders(base: Path) -> None:
    mm = base / "MiniMax-H3"
    write(mm / "config.json", {"architectures": ["MiniMaxM2ForCausalLM"], "model_type": "minimax_m2"})
    write(mm / "tokenizer_config.json", {"chat_template": "{{ messages }}"})
    write(mm / "model-00001-of-00002.safetensors", binary=True)
    mo = base / "MiniMind-O"
    write(mo / "README.md", "# MiniMind-O\n极简全模态模型：支持语音输入、语音输出与图像理解（视觉）。")
    write(mo / "out" / "omni_512.pth", binary=True)
    write(mo / "scripts" / "serve_openai_api.py",
          "@app.post('/v1/chat/completions')\n# image_url input_audio modalities\nuvicorn.run(app, port=8998)\n")
    qw = base / "qwen3.5"
    write(qw / "config.json", {"architectures": ["Qwen3_5ForConditionalGeneration"], "vision_config": {}, "text_config": {}})
    write(qw / "preprocessor_config.json", {"image_processor_type": "Qwen2VLImageProcessor"})
    write(qw / "tokenizer_config.json", {"chat_template": "x"})
    write(qw / "model.safetensors", binary=True)
    qi = base / "qwen-image-2.1"
    write(qi / "model_index.json", {"_class_name": "QwenImagePipeline"})
    write(qi / "transformer" / "diffusion_pytorch_model.safetensors", binary=True)
    gs = base / "GPT-SoVITS-v2pro-20250604"
    write(gs / "api_v2.py", "@APP.post('/tts')\nref_audio_path\n")
    write(gs / "webui.py", "import gradio as gr\n")
    write(gs / "GPT_SoVITS" / "configs" / "tts_infer.yaml", "custom: {}\n")
    write(gs / "GPT_weights_v2Pro" / "alice-e15.ckpt", binary=True)
    write(gs / "SoVITS_weights_v2Pro" / "alice_e8_s200.pth", binary=True)


MOCK_KIND = {"MiniMax-H3": "llm", "MiniMind-O": "omni", "qwen3.5": "llm", "qwen-image-2.1": "image",
             "GPT-SoVITS-v2pro-20250604": "sovits"}
DESCRIPTIONS = {"MiniMax-H3": "推理与编程语言模型", "MiniMind-O": "极简全模态：看图、听、说",
                "qwen3.5": "原生多模态语言模型", "qwen-image-2.1": "文生图模型",
                "GPT-SoVITS-v2pro-20250604": "少样本语音合成与声音克隆"}


def main() -> None:
    data = Path(os.environ.get("MODELDOCK_DATA") or ROOT / "demo-data").resolve()
    os.environ["MODELDOCK_DATA"] = str(data)
    from modeldock.detect import detect
    from modeldock.schemas import ModelConfig
    from modeldock.store import Store

    folders = data / "models"
    make_folders(folders)
    store = Store()
    store.models.clear()
    port = 8301
    for name, kind in MOCK_KIND.items():
        res = detect(str(folders / name))
        model = ModelConfig(name=res.name, path=res.path, adapter=res.adapter, description=DESCRIPTIONS[name],
                            capabilities=res.capabilities, launch=res.launch, defaults=res.defaults)
        delay = 4 if kind in ("image", "omni") else 2
        model.launch.command = (f'{{python}} "{{servers}}/mock_server.py" --kind {kind} --name "{name}" '
                                f"--host {{host}} --port {{port}} --delay {delay}")
        model.launch.port = port
        model.launch.health_path = "/health" if kind == "image" else ("/docs" if kind == "sovits" else "/v1/models")
        model.launch.stop_command = ""
        for c in model.capabilities:
            if c.type == "webui" and c.binding.get("command"):
                c.binding.update(command=f'{{python}} "{{servers}}/mock_server.py" --kind webui --port {port + 50}',
                                 port=port + 50, url=f"http://127.0.0.1:{port + 50}")
        port += 1
        store.models[model.id] = model
        print(f"  {res.name:<28} {res.adapter_label:<16} {[c.type for c in res.capabilities if c.enabled]}")
    store.save()
    print(f"\nDemo written to {data}\nRun:  MODELDOCK_DATA={data} python run.py")


if __name__ == "__main__":
    main()

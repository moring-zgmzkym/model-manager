#!/usr/bin/env python3
"""Headless HTTP server for MiniMind-O.

ModelDock starts this script with the project's own virtualenv (the one that
already has torch / transformers / funasr / gradio installed).  It reuses the
loading and inference helpers of repo/scripts/web_demo_omni.py instead of
running the Gradio UI, and exposes three endpoints:

    GET  /health   -> readiness + loaded voices
    GET  /voices   -> built-in and cloned speaker names
    POST /chat     -> server-sent events: delta / asr / audio / status / error / done

Request body for /chat:
    {"text": str, "image_b64": str|null, "audio_b64": str|null,
     "voice": "default", "history": [{"role","content"}],
     "temperature": 0.7, "max_tokens": 512}

Audio replies are returned as base64 WAV (24 kHz mono), so ModelDock can save
them into the output library like any other generated file.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import os
import sys
import time
import traceback
import wave
from pathlib import Path
from types import SimpleNamespace

parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True, help="MiniMind-O repo directory")
parser.add_argument("--model", default="", help="HF model folder (default: first under repo/scripts)")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8890)
parser.add_argument("--device", default="auto")
args = parser.parse_args()

REPO = Path(args.repo).resolve()
SCRIPTS = REPO / "scripts"
if not (SCRIPTS / "web_demo_omni.py").exists():
    raise SystemExit(f"web_demo_omni.py not found under {SCRIPTS}")

for path in (str(REPO), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)
os.chdir(SCRIPTS)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from PIL import Image  # noqa: E402

spec = importlib.util.spec_from_file_location("web_demo_omni", str(SCRIPTS / "web_demo_omni.py"))
if spec is None or spec.loader is None:
    raise SystemExit("cannot import web_demo_omni.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

device = args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
demo.args = SimpleNamespace(
    load_from=str(SCRIPTS),
    audio_encoder=str(REPO / "model" / "SenseVoiceSmall"),
    vision_model=str(REPO / "model" / "siglip2-base-p32-256-ve"),
    mimi_path=str(REPO / "model" / "mimi"),
    device=device,
    asr_device="auto",
    mimi_device="auto",
    open_thinking=0,
    top_p=0.85,
    port=args.port,
)
demo.device = device
asr_device = demo.resolve_aux_device("auto", "cpu" if demo.get_device_type(device) == "mps" else device)
mimi_device = demo.resolve_aux_device("auto", device)

print(f"[omni] python={sys.version.split()[0]} device={device} repo={REPO}", flush=True)
print(f"[omni] loading model ...", flush=True)
started = time.time()

model_path = Path(args.model) if args.model else None
if model_path is None:
    found = demo.scan_hf_models(str(SCRIPTS))
    if not found:
        raise SystemExit(f"no HF model folder under {SCRIPTS}")
    model_path = Path(found[sorted(found)[0]])
print(demo.load_hf_model(str(model_path)), flush=True)

try:
    from transformers import MimiModel

    demo.mimi_model = MimiModel.from_pretrained(str(REPO / "model" / "mimi")).eval().to(mimi_device)
    if demo.get_device_type(mimi_device) != "cpu":
        demo.mimi_model = demo.mimi_model.half()
    print(f"[omni] mimi loaded on {mimi_device}", flush=True)
except Exception as exc:  # pragma: no cover - optional dependency
    demo.mimi_model = None
    print(f"[omni] mimi unavailable: {exc}", flush=True)

try:
    from funasr import AutoModel

    demo.asr_model = AutoModel(model=str(REPO / "model" / "SenseVoiceSmall"), trust_remote_code=True,
                               device=asr_device, disable_update=True)
    print(f"[omni] asr loaded on {asr_device}", flush=True)
except Exception as exc:  # pragma: no cover - optional dependency
    demo.asr_model = None
    print(f"[omni] asr unavailable: {exc}", flush=True)

demo.load_voices()
print(f"[omni] voices: {sorted(demo.voices_data) or 'none'} ({time.time() - started:.1f}s)", flush=True)

app = FastAPI(title="MiniMind-O Omni")


def sse(event: dict) -> bytes:
    return ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")


def wav_base64(samples: np.ndarray, rate: int) -> str:
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes(pcm.tobytes())
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_audio(raw: str):
    if not raw:
        return None
    payload = raw.split(",", 1)[-1]
    data, rate = sf.read(io.BytesIO(base64.b64decode(payload)), dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    return int(rate), np.ascontiguousarray(data, dtype=np.float32)


def decode_image(raw: str):
    if not raw:
        return None
    payload = raw.split(",", 1)[-1]
    return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")


@app.get("/health")
def health():
    return JSONResponse({
        "ok": True,
        "model": model_path.name,
        "device": device,
        "audio": demo.mimi_model is not None,
        "asr": demo.asr_model is not None,
        "voices": sorted(demo.voices_data),
    })


@app.get("/voices")
def voices():
    return {
        "voices": ["default", *sorted(demo.voices_data)],
        "builtin": sorted(demo.builtin_voices),
        "clone": sorted(demo.clone_voices),
    }


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()

    def stream():
        try:
            text = str(body.get("text") or "")
            history = body.get("history") or []
            audio = decode_audio(body.get("audio_b64") or "")
            image = decode_image(body.get("image_b64") or "")
            voice = str(body.get("voice") or "default")
            for chunk, audio_out, asr in demo.chat_stream(
                text, audio_input=audio, image_input=image, voice_name=voice,
                history=history, temperature=float(body.get("temperature") or 0.7),
                max_tokens=int(body.get("max_tokens") or 512),
            ):
                if chunk:
                    yield sse({"type": "delta", "text": chunk})
                if audio_out == "loading_audio":
                    yield sse({"type": "status", "message": "正在生成语音…"})
                elif isinstance(audio_out, tuple) and len(audio_out) == 2:
                    rate, samples = audio_out
                    yield sse({"type": "audio", "data": wav_base64(samples, rate), "sample_rate": int(rate)})
                if asr is not None:
                    yield sse({"type": "asr", "text": asr})
            yield sse({"type": "done"})
        except Exception as exc:
            traceback.print_exc()
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    print(f"[omni] serving on http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

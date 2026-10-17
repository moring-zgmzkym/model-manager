#!/usr/bin/env python3
"""Minimal OpenAI-compatible chat server for a local Transformers model.

Used by ModelDock when vLLM / SGLang aren't available. Supports streaming,
chat templates and (best effort) images for vision-language models that ship
an ``AutoProcessor``. Requires: torch, transformers, fastapi, uvicorn,
(pillow for images).

    python hf_chat_server.py --model D:/models/qwen3.5 --port 8101
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True)
ap.add_argument("--name", default="")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8101)
ap.add_argument("--dtype", default="auto")
ap.add_argument("--device-map", default="auto")
ap.add_argument("--no-trust-remote-code", action="store_true")
args = ap.parse_args()

import torch  # noqa: E402
from transformers import AutoConfig, AutoTokenizer, TextIteratorStreamer  # noqa: E402

TRUST = not args.no_trust_remote_code
NAME = args.name or args.model.rstrip("/\\").split("/")[-1].split("\\")[-1]
print(f"Loading {args.model} …", flush=True)
config = AutoConfig.from_pretrained(args.model, trust_remote_code=TRUST)
is_vlm = bool(getattr(config, "vision_config", None))
processor = None
if is_vlm:
    try:
        from transformers import AutoProcessor
        processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=TRUST)
    except Exception as exc:  # pragma: no cover
        print(f"No processor ({exc}); images disabled", flush=True)
        is_vlm = False

model = None
load_errors = []
loaders = ["AutoModelForImageTextToText", "AutoModelForVision2Seq"] if is_vlm else []
loaders += ["AutoModelForCausalLM"]
import transformers  # noqa: E402

for loader in loaders:
    cls = getattr(transformers, loader, None)
    if cls is None:
        continue
    try:
        model = cls.from_pretrained(args.model, torch_dtype=args.dtype, device_map=args.device_map, trust_remote_code=TRUST)
        break
    except Exception as exc:
        load_errors.append(f"{loader}: {exc}")
if model is None:
    raise SystemExit("无法加载模型：\n" + "\n".join(load_errors))
model.eval()
tokenizer = processor.tokenizer if processor is not None else AutoTokenizer.from_pretrained(args.model, trust_remote_code=TRUST)
print(f"Model ready on {model.device}", flush=True)

app = FastAPI()
lock = threading.Lock()


def load_image(url: str):
    from PIL import Image
    if url.startswith("data:"):
        url = url.split(",", 1)[1]
        return Image.open(io.BytesIO(base64.b64decode(url))).convert("RGB")
    return Image.open(url).convert("RGB")


def prepare(messages: list[dict]):
    images = []
    norm = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for p in content:
                if p.get("type") == "image_url" and is_vlm:
                    images.append(load_image(p["image_url"]["url"]))
                    parts.append({"type": "image"})
                elif p.get("type") == "text":
                    parts.append({"type": "text", "text": p["text"]})
            norm.append({"role": m["role"], "content": parts if is_vlm else " ".join(x.get("text", "") for x in parts)})
        else:
            norm.append({"role": m["role"], "content": [{"type": "text", "text": content}] if is_vlm else content})
    if is_vlm and processor is not None:
        text = processor.apply_chat_template(norm, add_generation_prompt=True, tokenize=False)
        inputs = processor(text=[text], images=images or None, return_tensors="pt")
    else:
        text = tokenizer.apply_chat_template(norm, add_generation_prompt=True, tokenize=False)
        inputs = tokenizer([text], return_tensors="pt")
    return inputs.to(model.device)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": NAME, "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    inputs = prepare(body["messages"])
    gen_kwargs = dict(max_new_tokens=int(body.get("max_tokens") or 1024), do_sample=float(body.get("temperature", 0.7)) > 0,
                      temperature=max(float(body.get("temperature", 0.7)), 1e-3), top_p=float(body.get("top_p", 0.9)))
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    if not body.get("stream"):
        with lock, torch.inference_mode():
            out = model.generate(**inputs, **gen_kwargs)
        text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": NAME,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}

    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)

    def run():
        with lock, torch.inference_mode():
            model.generate(**inputs, **gen_kwargs, streamer=streamer)

    threading.Thread(target=run, daemon=True).start()

    def gen():
        for piece in streamer:
            if piece:
                yield "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "model": NAME,
                                             "choices": [{"index": 0, "delta": {"content": piece}}]}, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host=args.host, port=args.port)

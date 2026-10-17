#!/usr/bin/env python3
"""Minimal OpenAI-images-compatible server for a local Diffusers pipeline.

    python diffusers_server.py --model D:/models/qwen-image-2.1 --port 8102

Endpoints: GET /health, POST /v1/images/generations, POST /v1/images/edits.
Requires: torch, diffusers, transformers, accelerate, pillow, fastapi, uvicorn,
python-multipart.
"""

from __future__ import annotations

import argparse
import base64
import io
import threading
import time

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True)
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8102)
ap.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
ap.add_argument("--cpu-offload", action="store_true", help="enable model CPU offload to save VRAM")
args = ap.parse_args()

import torch  # noqa: E402
from diffusers import DiffusionPipeline  # noqa: E402
from PIL import Image  # noqa: E402

dtype = getattr(torch, args.dtype)
print(f"Loading pipeline from {args.model} …", flush=True)
pipe = DiffusionPipeline.from_pretrained(args.model, torch_dtype=dtype)
if args.cpu_offload:
    pipe.enable_model_cpu_offload()
elif torch.cuda.is_available():
    pipe.to("cuda")
elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
    pipe.to("mps")
print(f"Pipeline ready: {pipe.__class__.__name__}", flush=True)

app = FastAPI()
lock = threading.Lock()
device = "cuda" if torch.cuda.is_available() else "cpu"


def run(prompt: str, size: str, n: int, extra: dict, image=None) -> list[str]:
    w, h = (int(x) for x in size.lower().split("x"))
    kwargs = {"prompt": prompt, "num_images_per_prompt": n}
    if image is None:
        kwargs.update(width=w, height=h)
    else:
        kwargs["image"] = image
    for key in ("negative_prompt", "num_inference_steps", "guidance_scale", "true_cfg_scale"):
        if extra.get(key) not in (None, ""):
            kwargs[key] = type({"negative_prompt": "", "num_inference_steps": 1}.get(key, 1.0))(extra[key])
    seed = extra.get("seed")
    if seed not in (None, "", -1, "-1"):
        kwargs["generator"] = torch.Generator(device=device).manual_seed(int(seed))
    with lock, torch.inference_mode():
        images = pipe(**kwargs).images
    out = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(base64.b64encode(buf.getvalue()).decode())
    return out


@app.get("/health")
def health():
    return {"ok": True, "pipeline": pipe.__class__.__name__}


@app.post("/v1/images/generations")
async def generations(req: Request):
    body = await req.json()
    imgs = run(body.get("prompt", ""), body.get("size", "1024x1024"), int(body.get("n", 1)), body)
    return {"created": int(time.time()), "data": [{"b64_json": b} for b in imgs]}


@app.post("/v1/images/edits")
async def edits(req: Request, image: UploadFile = File(...), prompt: str = Form(""), size: str = Form("1024x1024"),
                n: int = Form(1)):
    form = await req.form()
    src = Image.open(io.BytesIO(await image.read())).convert("RGB")
    extra = {k: form.get(k) for k in ("negative_prompt", "num_inference_steps", "guidance_scale", "true_cfg_scale", "seed")}
    imgs = run(prompt, size, n, extra, image=src)
    return {"created": int(time.time()), "data": [{"b64_json": b} for b in imgs]}


if __name__ == "__main__":
    uvicorn.run(app, host=args.host, port=args.port)

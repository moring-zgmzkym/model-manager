#!/usr/bin/env python3
"""A fake model server for trying ModelDock without a GPU.

Implements just enough of the OpenAI API (chat with streaming, vision echo,
speech output, images, speech, transcription, embeddings) and GPT-SoVITS'
``/tts`` to exercise every workbench panel.

    python mock_server.py --port 8100 --kind omni|llm|image|sovits|all
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import struct
import time
import wave
import zlib

import uvicorn
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response, StreamingResponse

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8100)
ap.add_argument("--kind", default="all")
ap.add_argument("--name", default="mock-model")
ap.add_argument("--delay", type=float, default=0.0, help="simulated model load time (seconds)")
args, _ = ap.parse_known_args()
KIND = args.kind

app = FastAPI(title=f"mock {KIND}")


def tone(seconds: float = 1.2, freq: float = 440.0, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            env = min(1.0, i / 2000, (seconds * rate - i) / 2000)
            frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def png(width: int, height: int, seed: str) -> bytes:
    h = hashlib.sha256(seed.encode()).digest()
    c1, c2 = (h[0], h[1], h[2]), (h[3], h[4], h[5])
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            t = (x + y) / (width + height)
            rows += bytes(int(c1[k] * (1 - t) + c2[k] * t) for k in range(3))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")


if KIND in ("all", "llm", "omni", "embed"):
    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": args.name, "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        last = body["messages"][-1]
        content = last.get("content")
        seen = []
        if isinstance(content, list):
            seen = [p["type"] for p in content if p.get("type") != "text"]
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        reply = f"（模拟回复）你说的是：「{content}」。"
        if seen:
            reply += f" 我还收到了：{', '.join(seen)}。"
        reply += " 这是 ModelDock 的演示服务，接入真实模型后这里会显示模型的回答。"
        want_audio = "audio" in (body.get("modalities") or [])
        if not body.get("stream"):
            msg = {"role": "assistant", "content": reply}
            if want_audio:
                msg["audio"] = {"data": base64.b64encode(tone()).decode(), "transcript": reply}
            return {"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": len(reply)}}

        async def gen():
            yield f"data: {json.dumps({'choices': [{'delta': {'reasoning_content': '先理解问题，再组织回答。'}}]})}\n\n"
            for ch in reply:
                yield f"data: {json.dumps({'choices': [{'delta': {'content': ch}}]}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.012)
            if want_audio:
                pcm = tone()[44:]
                for i in range(0, len(pcm), 9600):
                    yield f"data: {json.dumps({'choices': [{'delta': {'audio': {'data': base64.b64encode(pcm[i:i + 9600]).decode()}}}]})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/v1/embeddings")
    async def embeddings(req: Request):
        body = await req.json()
        inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
        data = []
        for i, text in enumerate(inputs):
            vec = [0.0] * 64
            for tok in str(text).lower():
                vec[hash(tok) % 64] += 1.0
            data.append({"index": i, "embedding": vec, "object": "embedding"})
        return {"data": data, "model": args.name}

if KIND in ("all", "omni"):
    @app.post("/v1/audio/speech")
    async def speech(req: Request):
        return Response(tone(1.5), media_type="audio/wav")

    @app.post("/v1/audio/transcriptions")
    async def transcribe(file: UploadFile = File(...), model: str = Form("")):
        data = await file.read()
        return {"text": f"（模拟识别）收到 {len(data) // 1024} KB 的音频。"}

if KIND in ("all", "image"):
    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/v1/images/generations")
    async def images(req: Request):
        body = await req.json()
        w, h = (int(x) for x in str(body.get("size", "512x512")).split("x"))
        scale = 256 / max(w, h)
        await asyncio.sleep(0.6)
        return {"created": int(time.time()), "data": [
            {"b64_json": base64.b64encode(png(int(w * scale), int(h * scale), body.get("prompt", "") + str(i) + str(body.get("seed")))).decode()}
            for i in range(int(body.get("n", 1)))]}

    @app.post("/v1/images/edits")
    async def edits(prompt: str = Form(""), image: UploadFile = File(...)):
        return {"data": [{"b64_json": base64.b64encode(png(256, 256, prompt + "edit")).decode()}]}

if KIND in ("all", "sovits"):
    @app.post("/tts")
    async def sovits_tts(req: Request):
        body = await req.json()
        return Response(tone(1.0 + len(body.get("text", "")) * 0.05, 330), media_type="audio/wav")

    @app.get("/set_gpt_weights")
    def set_gpt(weights_path: str):
        return "success"

    @app.get("/set_sovits_weights")
    def set_sovits(weights_path: str):
        return "success"

    @app.post("/set_refer_audio")
    def set_ref():
        return "success"

if KIND == "webui":
    @app.get("/", response_class=HTMLResponse)
    def home():
        return "<html><body style='font-family:sans-serif;padding:40px'><h2>Mock WebUI</h2><p>这是一个模拟的模型自带界面。</p></body></html>"


if __name__ == "__main__":
    if args.delay:
        print(f"Loading model weights… ({args.delay:.0f}s)", flush=True)
        for i in range(10):
            time.sleep(args.delay / 10)
            print(f"Loading checkpoint shards: {(i + 1) * 10}%", flush=True)
    print(f"Mock {KIND} server on http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")

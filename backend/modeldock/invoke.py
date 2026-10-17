"""Call a running model on the user's behalf.

The browser never talks to model servers directly: ModelDock proxies every
call so there are no CORS issues, uploads can be turned into local paths
(GPT-SoVITS needs a file path for reference audio) and results are saved to
the output library.
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import time
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx

from .outputs import resolve_upload, save_output
from .schemas import Capability, ModelConfig

TIMEOUT = httpx.Timeout(connect=10, read=900, write=120, pool=10)


class InvokeError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, trust_env=False)


def find_cap(model: ModelConfig, types: tuple[str, ...]) -> Optional[Capability]:
    for t in types:
        for c in model.capabilities:
            if c.type == t and c.enabled:
                return c
    return None


def base_of(model: ModelConfig, cap: Optional[Capability]) -> str:
    if cap and cap.binding.get("base_url"):
        return str(cap.binding["base_url"]).rstrip("/")
    return model.base_url()


def served_name(model: ModelConfig, payload: dict[str, Any]) -> str:
    return payload.get("model") or model.defaults.get("model") or model.name


def headers_for(model: ModelConfig, cap: Optional[Capability] = None) -> dict[str, str]:
    key = (cap.binding.get("api_key") if cap else None) or model.defaults.get("api_key")
    return {"Authorization": f"Bearer {key}"} if key else {}


def data_url_to_bytes(url: str) -> tuple[bytes, str]:
    m = re.match(r"data:([^;]+);base64,(.*)$", url, re.S)
    if not m:
        raise InvokeError("无效的数据 URL")
    return base64.b64decode(m.group(2)), m.group(1)


def pcm16_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def sniff_audio(data: bytes) -> tuple[bytes, str]:
    if data[:4] == b"RIFF":
        return data, ".wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return data, ".mp3"
    if data[:4] == b"OggS":
        return data, ".ogg"
    if data[:4] == b"fLaC":
        return data, ".flac"
    return pcm16_to_wav(data), ".wav"


async def raise_for(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        try:
            body = (await r.aread()).decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        raise InvokeError(f"{what}失败（HTTP {r.status_code}）：{body[:400]}", 502)


# ======================================================================= chat
def build_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if payload.get("system"):
        out.append({"role": "system", "content": payload["system"]})
    for m in payload.get("messages", []):
        images, audio = m.get("images") or [], m.get("audio")
        text = m.get("content") or ""
        if m.get("role") == "assistant" or (not images and not audio):
            out.append({"role": m.get("role", "user"), "content": text})
            continue
        parts: list[dict[str, Any]] = []
        for img in images:
            parts.append({"type": "image_url", "image_url": {"url": img}})
        if audio:
            data, mime = data_url_to_bytes(audio)
            fmt = "mp3" if "mpeg" in mime or "mp3" in mime else "wav"
            parts.append({"type": "input_audio", "input_audio": {"data": base64.b64encode(data).decode(), "format": fmt}})
        if text:
            parts.append({"type": "text", "text": text})
        out.append({"role": "user", "content": parts})
    return out


def sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


async def chat_stream(model: ModelConfig, payload: dict[str, Any]) -> AsyncIterator[bytes]:
    cap = find_cap(model, ("chat", "vision", "audio_input", "speech_output"))
    if not cap:
        yield sse({"type": "error", "message": "该模型没有启用对话能力"})
        return
    protocol = cap.binding.get("protocol", "openai_chat")
    if protocol == "custom_http":
        try:
            res = await custom_call(model, cap, {"prompt": (payload.get("messages") or [{}])[-1].get("content", ""), **payload})
            yield sse({"type": "delta", "text": res.get("text") or json.dumps(res.get("json"), ensure_ascii=False)})
        except InvokeError as exc:
            yield sse({"type": "error", "message": str(exc)})
        yield sse({"type": "done"})
        return

    base = base_of(model, cap)
    path = cap.binding.get("path", "/v1/chat/completions")
    want_speech = bool(payload.get("speech"))
    body: dict[str, Any] = {"model": served_name(model, payload), "messages": build_messages(payload), "stream": True}
    for k in ("temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty", "seed"):
        if payload.get(k) is not None:
            body[k] = payload[k]
    if want_speech:
        body["modalities"] = ["text", "audio"]
        body["audio"] = {"voice": payload.get("voice") or cap.binding.get("voice") or "Chelsie", "format": "wav"}
    if payload.get("extra") and isinstance(payload["extra"], dict):
        body.update(payload["extra"])

    started = time.time()
    text_acc, audio_b64, transcript = [], [], []
    audio_once: Optional[str] = None
    usage: dict[str, Any] = {}
    try:
        async with client() as c:
            for attempt in range(2):
                async with c.stream("POST", base + path, json=body, headers=headers_for(model, cap)) as r:
                    if r.status_code == 400 and want_speech and attempt == 0:
                        body.pop("modalities", None)
                        body.pop("audio", None)
                        yield sse({"type": "notice", "message": "服务不支持语音输出参数，已改为纯文本回复"})
                        continue
                    await raise_for(r, "对话请求")
                    ctype = r.headers.get("content-type", "")
                    if "text/event-stream" not in ctype:
                        data = json.loads(await r.aread())
                        msg = (data.get("choices") or [{}])[0].get("message", {})
                        if msg.get("reasoning_content"):
                            yield sse({"type": "reasoning", "text": msg["reasoning_content"]})
                        text_acc.append(msg.get("content") or "")
                        yield sse({"type": "delta", "text": msg.get("content") or ""})
                        if (msg.get("audio") or {}).get("data"):
                            audio_once = msg["audio"]["data"]
                        usage = data.get("usage") or {}
                    else:
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            try:
                                chunk = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if chunk.get("usage"):
                                usage = chunk["usage"]
                            for choice in chunk.get("choices") or []:
                                delta = choice.get("delta") or {}
                                rc = delta.get("reasoning_content") or delta.get("reasoning")
                                if rc:
                                    yield sse({"type": "reasoning", "text": rc})
                                if delta.get("content"):
                                    text_acc.append(delta["content"])
                                    yield sse({"type": "delta", "text": delta["content"]})
                                au = delta.get("audio") or {}
                                if au.get("data"):
                                    audio_b64.append(au["data"])
                                if au.get("transcript"):
                                    transcript.append(au["transcript"])
                                    yield sse({"type": "delta", "text": au["transcript"]})
                break
    except InvokeError as exc:
        yield sse({"type": "error", "message": str(exc)})
        return
    except httpx.HTTPError as exc:
        yield sse({"type": "error", "message": f"无法连接模型服务 {base}：{exc.__class__.__name__}。请确认模型已启动。"})
        return

    raw_audio = base64.b64decode(audio_once) if audio_once else b"".join(base64.b64decode(a) for a in audio_b64)
    if raw_audio:
        data, ext = sniff_audio(raw_audio)
        entry = save_output(data, kind="audio", model_id=model.id, model_name=model.name, capability="speech_output",
                            ext=ext, prompt="".join(text_acc + transcript))
        yield sse({"type": "audio", "url": entry["url"], "output": entry})
    yield sse({"type": "done", "elapsed": round(time.time() - started, 2), "usage": usage})


# ======================================================================= images
async def fetch_image_bytes(c: httpx.AsyncClient, base: str, item: dict[str, Any]) -> bytes:
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    url = item.get("url") or ""
    if url.startswith("data:"):
        return data_url_to_bytes(url)[0]
    if url:
        r = await c.get(url if url.startswith("http") else base + url)
        await raise_for(r, "下载图片")
        return r.content
    raise InvokeError("服务返回中没有图片")


async def image_generate(model: ModelConfig, payload: dict[str, Any]) -> dict[str, Any]:
    edit_src = payload.get("image")
    cap = find_cap(model, ("image_edit", "image_generation") if edit_src else ("image_generation", "image_edit"))
    if not cap:
        raise InvokeError("该模型没有启用图像生成能力")
    if cap.binding.get("protocol") == "custom_http":
        return await custom_call(model, cap, payload)
    base = base_of(model, cap)
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise InvokeError("请填写提示词")
    params = {k: payload[k] for k in ("negative_prompt", "steps", "num_inference_steps", "guidance_scale",
                                       "true_cfg_scale", "seed") if payload.get(k) not in (None, "")}
    if "steps" in params:
        params["num_inference_steps"] = params.pop("steps")
    n = int(payload.get("n") or 1)
    size = payload.get("size") or "1024x1024"
    started = time.time()
    async with client() as c:
        try:
            if edit_src:
                path = resolve_upload(edit_src)
                if not path:
                    raise InvokeError("找不到要编辑的图片，请重新上传")
                form = {"prompt": prompt, "n": str(n), "size": size, "response_format": "b64_json",
                        "model": served_name(model, payload), **{k: str(v) for k, v in params.items()}}
                r = await c.post(base + cap.binding.get("edit_path", "/v1/images/edits"), data=form,
                                 files={"image": (path.name, path.read_bytes(), "image/png")}, headers=headers_for(model, cap))
            else:
                body = {"model": served_name(model, payload), "prompt": prompt, "n": n, "size": size,
                        "response_format": "b64_json", **params}
                r = await c.post(base + cap.binding.get("path", "/v1/images/generations"), json=body,
                                 headers=headers_for(model, cap))
        except httpx.HTTPError as exc:
            raise InvokeError(f"无法连接模型服务 {base}：{exc.__class__.__name__}。请确认模型已启动。", 502)
        await raise_for(r, "图像生成")
        items = r.json().get("data") or []
        outputs = []
        for item in items:
            data = await fetch_image_bytes(c, base, item)
            outputs.append(save_output(data, kind="image", model_id=model.id, model_name=model.name,
                                       capability=cap.type, ext=".png", prompt=prompt,
                                       meta={"size": size, **params, "revised_prompt": item.get("revised_prompt")}))
    return {"outputs": outputs, "elapsed": round(time.time() - started, 2)}


# ======================================================================= speech
SOVITS_LANG = {"auto": "auto", "zh": "zh", "en": "en", "ja": "ja", "ko": "ko", "yue": "yue",
               "all_zh": "all_zh", "all_ja": "all_ja", "all_ko": "all_ko", "all_yue": "all_yue"}


async def tts(model: ModelConfig, payload: dict[str, Any]) -> dict[str, Any]:
    want_clone = bool(payload.get("ref_audio"))
    cap = find_cap(model, ("voice_clone", "tts") if want_clone else ("tts", "voice_clone"))
    if not cap:
        raise InvokeError("该模型没有启用语音合成能力")
    text = (payload.get("text") or "").strip()
    if not text:
        raise InvokeError("请输入要朗读的文字")
    protocol = cap.binding.get("protocol", "openai_speech")
    if protocol == "custom_http":
        return await custom_call(model, cap, payload)
    base = base_of(model, cap)
    started = time.time()
    async with client() as c:
        try:
            if protocol == "gpt_sovits":
                ref = resolve_upload(payload.get("ref_audio") or "")
                if not ref:
                    raise InvokeError("GPT-SoVITS 需要一段 3~10 秒的参考音频，请先上传或选择音色")
                lang = SOVITS_LANG.get(payload.get("text_lang") or "zh", "zh")
                plang = SOVITS_LANG.get(payload.get("prompt_lang") or "zh", "zh")
                if cap.binding.get("api") == "v1":
                    body = {"refer_wav_path": str(ref), "prompt_text": payload.get("prompt_text", ""),
                            "prompt_language": plang, "text": text, "text_language": lang,
                            "speed": payload.get("speed", 1.0)}
                    r = await c.post(base + "/", json=body)
                else:
                    body = {
                        "text": text, "text_lang": lang, "ref_audio_path": str(ref),
                        "prompt_text": payload.get("prompt_text", ""), "prompt_lang": plang,
                        "top_k": payload.get("top_k", 15), "top_p": payload.get("top_p", 1.0),
                        "temperature": payload.get("temperature", 1.0),
                        "text_split_method": payload.get("text_split_method", "cut5"),
                        "batch_size": payload.get("batch_size", 1), "speed_factor": payload.get("speed", 1.0),
                        "media_type": "wav", "streaming_mode": False,
                        "repetition_penalty": payload.get("repetition_penalty", 1.35),
                        "seed": payload.get("seed", -1), "parallel_infer": True,
                    }
                    aux = [str(p) for p in (resolve_upload(a) for a in payload.get("aux_ref_audio") or []) if p]
                    if aux:
                        body["aux_ref_audio_paths"] = aux
                    r = await c.post(base + "/tts", json=body)
            else:
                body = {"model": served_name(model, payload), "input": text,
                        "voice": payload.get("voice") or "alloy", "response_format": "wav",
                        "speed": payload.get("speed", 1.0)}
                if payload.get("instructions"):
                    body["instructions"] = payload["instructions"]
                r = await c.post(base + cap.binding.get("path", "/v1/audio/speech"), json=body, headers=headers_for(model, cap))
        except httpx.HTTPError as exc:
            raise InvokeError(f"无法连接模型服务 {base}：{exc.__class__.__name__}。请确认模型已启动。", 502)
        await raise_for(r, "语音合成")
        data, ext = sniff_audio(r.content)
    entry = save_output(data, kind="audio", model_id=model.id, model_name=model.name, capability=cap.type,
                        ext=ext, prompt=text, meta={"voice": payload.get("voice_name") or payload.get("voice")})
    return {"outputs": [entry], "elapsed": round(time.time() - started, 2)}


async def transcribe(model: ModelConfig, file_ref: str, language: str = "") -> dict[str, Any]:
    cap = find_cap(model, ("asr",))
    if not cap:
        raise InvokeError("该模型没有启用语音识别能力")
    path = resolve_upload(file_ref)
    if not path:
        raise InvokeError("找不到音频文件")
    if cap.binding.get("protocol") == "custom_http":
        return await custom_call(model, cap, {"file": file_ref, "language": language})
    base = base_of(model, cap)
    started = time.time()
    form = {"model": served_name(model, {}), "response_format": "json"}
    if language:
        form["language"] = language
    async with client() as c:
        try:
            r = await c.post(base + cap.binding.get("path", "/v1/audio/transcriptions"), data=form,
                             files={"file": (path.name, path.read_bytes(), "audio/wav")}, headers=headers_for(model, cap))
        except httpx.HTTPError as exc:
            raise InvokeError(f"无法连接模型服务 {base}：{exc.__class__.__name__}", 502)
        await raise_for(r, "语音识别")
    try:
        text = r.json().get("text", "")
    except Exception:
        text = r.text
    return {"text": text, "elapsed": round(time.time() - started, 2)}


# ======================================================================= embeddings
async def embed(model: ModelConfig, payload: dict[str, Any]) -> dict[str, Any]:
    cap = find_cap(model, ("embedding",))
    if not cap:
        raise InvokeError("该模型没有启用向量能力")
    inputs = [s for s in (payload.get("inputs") or []) if str(s).strip()]
    if not inputs:
        raise InvokeError("请至少输入一段文本")
    base = base_of(model, cap)
    started = time.time()
    async with client() as c:
        try:
            r = await c.post(base + cap.binding.get("path", "/v1/embeddings"),
                             json={"model": served_name(model, payload), "input": inputs}, headers=headers_for(model, cap))
        except httpx.HTTPError as exc:
            raise InvokeError(f"无法连接模型服务 {base}：{exc.__class__.__name__}", 502)
        await raise_for(r, "向量计算")
    vecs = [d["embedding"] for d in sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))]

    def cos(a: list[float], b: list[float]) -> float:
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
        return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0

    sim = [[round(cos(a, b), 4) for b in vecs] for a in vecs]
    return {"dim": len(vecs[0]) if vecs else 0, "similarity": sim,
            "preview": [v[:8] for v in vecs], "elapsed": round(time.time() - started, 2)}


# ======================================================================= GPT-SoVITS weights
def sovits_weights(model: ModelConfig) -> dict[str, list[str]]:
    root = Path(model.launch.cwd or model.path)
    gpt, sovits = [], []
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and d.name.lower().startswith("gpt_weights"):
                gpt += [str(p) for p in d.glob("*.ckpt")]
            if d.is_dir() and d.name.lower().startswith("sovits_weights"):
                sovits += [str(p) for p in d.glob("*.pth")]
        pre = root / "GPT_SoVITS" / "pretrained_models"
        if pre.is_dir():
            gpt += [str(p) for p in pre.rglob("*.ckpt")]
            sovits += [str(p) for p in pre.rglob("*.pth") if "s2" in p.name.lower() or "sovits" in p.name.lower()]
    return {"gpt": sorted(set(gpt)), "sovits": sorted(set(sovits))}


async def sovits_set_weights(model: ModelConfig, gpt: str = "", sovits: str = "") -> dict[str, Any]:
    cap = find_cap(model, ("voice_clone", "tts"))
    base = base_of(model, cap)
    done = []
    async with client() as c:
        try:
            if gpt:
                r = await c.get(base + "/set_gpt_weights", params={"weights_path": gpt})
                await raise_for(r, "切换 GPT 权重")
                done.append("gpt")
            if sovits:
                r = await c.get(base + "/set_sovits_weights", params={"weights_path": sovits})
                await raise_for(r, "切换 SoVITS 权重")
                done.append("sovits")
        except httpx.HTTPError as exc:
            raise InvokeError(f"无法连接模型服务：{exc.__class__.__name__}", 502)
    return {"ok": True, "switched": done}


# ======================================================================= custom HTTP
def _render(obj: Any, values: dict[str, Any]) -> Any:
    if isinstance(obj, str):
        m = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", obj)
        if m:
            return values.get(m.group(1))
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda mm: str(values.get(mm.group(1), "")), obj)
    if isinstance(obj, dict):
        return {k: _render(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render(v, values) for v in obj]
    return obj


def _pluck(data: Any, path: str) -> Any:
    for part in [p for p in re.split(r"\.|\[(\d+)\]", path) if p]:
        if isinstance(data, list) and part.isdigit():
            data = data[int(part)] if int(part) < len(data) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


async def custom_call(model: ModelConfig, cap: Capability, payload: dict[str, Any]) -> dict[str, Any]:
    b = cap.binding
    values = dict(payload)
    for f in b.get("fields") or []:
        if f.get("type") == "file" and values.get(f["name"]):
            p = resolve_upload(values[f["name"]])
            if p:
                values[f["name"]] = base64.b64encode(p.read_bytes()).decode() if f.get("as") == "base64" else str(p)
    url = b.get("url") or (base_of(model, cap) + b.get("path", "/"))
    method = b.get("method", "POST").upper()
    body = _render(b.get("body"), values) if b.get("body") is not None else None
    query = _render(b.get("query"), values) if b.get("query") else None
    started = time.time()
    async with client() as c:
        try:
            if b.get("content_type") == "form":
                r = await c.request(method, url, data=body, params=query, headers=headers_for(model, cap))
            else:
                r = await c.request(method, url, json=body if method != "GET" else None, params=query,
                                    headers=headers_for(model, cap))
        except httpx.HTTPError as exc:
            raise InvokeError(f"无法连接 {url}：{exc.__class__.__name__}", 502)
        await raise_for(r, "接口调用")
    ctype = r.headers.get("content-type", "")
    res: dict[str, Any] = {"elapsed": round(time.time() - started, 2), "status": r.status_code}
    prompt = str(values.get("prompt") or values.get("text") or "")
    if ctype.startswith(("audio/", "image/")):
        kind = "audio" if ctype.startswith("audio/") else "image"
        res["outputs"] = [save_output(r.content, kind=kind, model_id=model.id, model_name=model.name,
                                      capability=cap.type, mime=ctype.split(";")[0], prompt=prompt)]
        return res
    try:
        data = r.json()
    except Exception:
        res["text"] = r.text
        return res
    extract = b.get("extract") or {}
    if extract.get("text"):
        res["text"] = _pluck(data, extract["text"])
    for key, kind, ext in (("image_b64", "image", ".png"), ("audio_b64", "audio", ".wav")):
        if extract.get(key):
            val = _pluck(data, extract[key])
            if isinstance(val, str):
                res.setdefault("outputs", []).append(save_output(
                    base64.b64decode(val.split(",", 1)[-1]), kind=kind, model_id=model.id, model_name=model.name,
                    capability=cap.type, ext=ext, prompt=prompt))
    res["json"] = data
    return res

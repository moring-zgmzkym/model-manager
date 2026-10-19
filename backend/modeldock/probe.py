"""Ask a running model server what it can do.

Looks at ``/v1/models`` (OpenAI compatible servers: vLLM, llama.cpp, Ollama,
LM Studio, SGLang...), ``/openapi.json`` (FastAPI servers such as
GPT-SoVITS api_v2) and Gradio's ``/config``.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .detect import cap, merge_caps
from .schemas import Capability, ProbeResult

ROUTE_CAPS: list[tuple[str, str, dict[str, Any]]] = [
    (r"/v1/chat/completions$", "chat", {"protocol": "openai_chat"}),
    (r"/v1/images/generations$", "image_generation", {"protocol": "openai_images"}),
    (r"/v1/images/edits$", "image_edit", {"protocol": "openai_images"}),
    (r"/v1/audio/speech$", "tts", {"protocol": "openai_speech"}),
    (r"/v1/audio/transcriptions$", "asr", {"protocol": "openai_transcription"}),
    (r"/v1/embeddings$", "embedding", {"protocol": "openai_embeddings"}),
]
VISION_NAME = re.compile(r"vl|vision|llava|omni|pixtral|gemma-?3|minicpm-?v|qwen\d(\.\d)?-?(vl|omni)", re.I)
AUDIO_NAME = re.compile(r"omni|audio|voice", re.I)


async def _ollama_show(client: httpx.AsyncClient, base: str, model_name: str,
                     caps: list[Capability], res: ProbeResult) -> None:
    """Ask Ollama what a model can do (vision / thinking / tools)."""
    if not model_name:
        return
    try:
        shown = await client.post(base + "/api/show", json={"model": model_name})
    except Exception:
        return
    if shown.status_code != 200:
        return
    try:
        info = shown.json()
    except Exception:
        return
    abilities = [str(item).lower() for item in info.get("capabilities") or []]
    if not abilities:
        return
    res.kinds.append("ollama")
    # The generic /v1/models probe adds an OpenAI chat capability; Ollama has a better native path.
    for item in caps:
        if item.type == "chat" and item.binding.get("protocol") == "openai_chat":
            item.binding = {**item.binding, "protocol": "ollama_chat"}
    if "vision" in abilities:
        caps.append(cap("vision", 0.95, f"Ollama 报告 {model_name} 支持视觉", source="runtime",
                        binding={"protocol": "ollama_chat"}))
    if "embedding" in abilities:
        caps.append(cap("embedding", 0.9, f"Ollama 报告 {model_name} 支持向量", source="runtime"))
    if "thinking" in abilities:
        res.notes.append(f"{model_name} 支持思考模式。")
    if "tools" in abilities:
        res.notes.append(f"{model_name} 支持工具调用。")


async def probe(base_url: str, timeout: float = 4.0, model_name: str = "") -> ProbeResult:
    base = base_url.rstrip("/")
    res = ProbeResult(reachable=False, base_url=base)
    caps: list[Capability] = []
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        async def get(path: str) -> httpx.Response | None:
            try:
                return await client.get(base + path)
            except Exception:
                return None

        r = await get("/v1/models")
        if r is not None:
            res.reachable = True
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                models = [str(m.get("id")) for m in data.get("data", []) if isinstance(m, dict)]
            except Exception:
                models = []
            if models or "data" in (r.text or ""):
                res.kinds.append("openai")
                res.served_models = models
                caps.append(cap("chat", 0.75, "服务实现了 /v1/models（OpenAI 兼容）", source="runtime"))
                for m in models:
                    if VISION_NAME.search(m):
                        caps.append(cap("vision", 0.55, f"服务中的模型名 {m} 暗示视觉能力", source="runtime"))
                    if AUDIO_NAME.search(m):
                        caps.append(cap("audio_input", 0.45, f"服务中的模型名 {m} 暗示音频能力", source="runtime"))
                    if re.search(r"embed|bge|(^|[-_])e5|gte", m, re.I):
                        caps.append(cap("embedding", 0.6, f"服务中的模型名 {m}", source="runtime"))
                await _ollama_show(client, base, model_name or (models[0] if models else ""), caps, res)

        r = await get("/openapi.json")
        if r is not None:
            res.reachable = True
        if r is not None and r.status_code == 200:
            try:
                paths = list((r.json().get("paths") or {}).keys())
            except Exception:
                paths = []
            if paths:
                res.kinds.append("openapi")
            hits = [(ctype, binding, next((p for p in paths if re.search(pattern, p)), None))
                    for pattern, ctype, binding in ROUTE_CAPS]
            hits = [h for h in hits if h[2]]
            # Generic OpenAI servers (vLLM, SGLang, llama.cpp…) list every route whether or not the loaded
            # model supports it. When several are present, only a lone route is strong evidence.
            group = {"chat": "chat", "image_generation": "image", "image_edit": "image", "tts": "tts",
                     "asr": "asr", "embedding": "embedding"}
            multi = len({group[h[0]] for h in hits}) > 1
            for ctype, binding, hit in hits:
                if multi and ctype != "chat":
                    caps.append(cap(ctype, 0.35, f"接口文档包含 {hit}（通用服务可能未加载对应模型，默认不启用）",
                                    source="runtime", binding=binding))
                else:
                    caps.append(cap(ctype, 0.9, f"接口文档包含 {hit}", source="runtime", binding=binding))
            if "/tts" in paths:
                api = "v2" if "/set_gpt_weights" in paths or "/set_refer_audio" in paths else "v1"
                b = {"protocol": "gpt_sovits", "api": api}
                if not any(c.type == "tts" for c in caps):
                    caps.append(cap("tts", 0.9, "接口文档包含 /tts（GPT-SoVITS）", source="runtime", binding=b))
                caps.append(cap("voice_clone", 0.85, "GPT-SoVITS 接口支持参考音频", source="runtime", binding=b))
                res.kinds.append("gpt_sovits")
            if len(paths) and not caps:
                res.notes.append("发现以下接口，可用“自定义接口”调用：" + "、".join(paths[:12]))

        r = await get("/system_stats")
        if r is not None and r.status_code == 200 and "comfyui_version" in (r.text or "")[:20000]:
            res.reachable = True
            res.kinds.append("comfyui")
            caps.append(cap("webui", 0.9, "检测到 ComfyUI 服务", source="runtime",
                            binding={"protocol": "webui", "url": base}))

        r = await get("/config")
        if r is not None and r.status_code == 200 and "components" in (r.text or "")[:200000]:
            res.reachable = True
            res.kinds.append("gradio")
            caps.append(cap("webui", 0.9, "检测到 Gradio 界面", source="runtime",
                            binding={"protocol": "webui", "url": base}))

        if not res.reachable:
            r = await get("/")
            if r is not None:
                res.reachable = True
                if "text/html" in r.headers.get("content-type", ""):
                    caps.append(cap("webui", 0.6, "根路径返回网页", source="runtime",
                                    binding={"protocol": "webui", "url": base}))

    res.capabilities = merge_caps([], caps)
    if not res.reachable:
        res.notes.append("无法连接，请确认模型已启动且端口正确。")
    return res

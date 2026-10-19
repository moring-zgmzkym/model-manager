"""Inspect a model folder and infer what it is, what it can do and how to start it.

Detection layers, from strongest to weakest evidence:

1. ``modeldock.json`` manifest written by the user or model author (confidence 1.0)
2. plugins in ``plugins/*.py`` (see plugins.py)
3. structural files: HF ``config.json``, diffusers ``model_index.json``,
   GGUF files, GPT-SoVITS layout, server scripts and their source code
4. hints: README keywords, folder names, component directory names

Hints from several weak sources are combined with a noisy-OR so that, e.g.,
a folder called ``MiniMind-O`` whose README mentions 语音输出 ends up with an
enabled ``speech_output`` capability, while a single stray keyword stays a
disabled suggestion the user can switch on.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .catalog import ADAPTERS, CAPABILITIES, default_binding
from .schemas import Capability, DetectResult, LaunchConfig, Settings

ENABLE_THRESHOLD = 0.5
SOURCE_RANK = {"files": 0, "runtime": 1, "plugin": 2, "user": 3, "manifest": 4}
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "site-packages", "runtime", "venv", ".venv",
    "env", ".cache", "logs", "log", "outputs", "output", ".idea", ".vscode", "dist", "build",
}
SCRIPT_EXT = {".bat", ".cmd", ".ps1", ".sh", ".command"}
MAX_ENTRIES = 5000
MAX_DEPTH = 3


# --------------------------------------------------------------------------- helpers
def cap(type_: str, confidence: float, evidence: str | Iterable[str], source: str = "files",
        binding: Optional[dict] = None, label: Optional[str] = None,
        actions: Optional[list[dict[str, Any]]] = None) -> Capability:
    ev = [evidence] if isinstance(evidence, str) else list(evidence)
    return Capability(
        type=type_, confidence=round(min(confidence, 1.0), 2), evidence=ev, source=source,  # type: ignore[arg-type]
        binding=binding or default_binding(type_), enabled=confidence >= ENABLE_THRESHOLD, label=label,
        actions=actions or [],
    )


def _cap_key(c: Capability) -> str:
    return f"custom:{c.label}" if c.type == "custom" else c.type


def merge_caps(base: list[Capability], extra: Iterable[Capability]) -> list[Capability]:
    """Merge capability lists: noisy-OR confidences, union evidence, keep strongest binding."""
    out: dict[str, Capability] = {_cap_key(c): c.model_copy(deep=True) for c in base}
    for c in extra:
        key = _cap_key(c)
        if key not in out:
            out[key] = c.model_copy(deep=True)
            continue
        cur = out[key]
        conf = 1 - (1 - cur.confidence) * (1 - c.confidence)
        if c.source in ("manifest", "user"):
            conf = max(conf, c.confidence)
        evidence = cur.evidence + [e for e in c.evidence if e not in cur.evidence]
        stronger = c if SOURCE_RANK.get(c.source, 0) > SOURCE_RANK.get(cur.source, 0) else cur
        binding = cur.binding
        rank_new, rank_cur = SOURCE_RANK.get(c.source, 0), SOURCE_RANK.get(cur.source, 0)
        cur_default = cur.binding.get("protocol") == default_binding(c.type)["protocol"]
        if c.binding and (rank_new > rank_cur or (rank_new == rank_cur and cur_default)):
            binding = {**cur.binding, **c.binding}
        out[key] = Capability(
            type=c.type, confidence=round(min(conf, 1.0), 2), evidence=evidence, source=stronger.source,
            binding=binding, label=c.label or cur.label,
            enabled=cur.enabled or c.enabled or conf >= ENABLE_THRESHOLD,
            # Runtime probes carry no actions; never drop the ones the file scan found.
            actions=c.actions or cur.actions or [],
        )
    return list(out.values())


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_text(path: Path, limit: int = 200_000) -> str:
    try:
        if path.stat().st_size > limit:
            with path.open("rb") as fh:
                return fh.read(limit).decode("utf-8", errors="ignore")
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


@dataclass
class Scan:
    root: Path
    files: list[Path] = field(default_factory=list)  # relative paths
    dirs: list[Path] = field(default_factory=list)

    def has(self, *names: str) -> Optional[Path]:
        lowered = {n.lower() for n in names}
        for f in self.files:
            if f.as_posix().lower() in lowered or f.name.lower() in lowered and len(f.parts) == 1:
                return f
        return None

    def top(self, name: str) -> Optional[Path]:
        p = self.root / name
        return p if p.exists() else None

    def find(self, pattern: str, max_depth: int = MAX_DEPTH) -> list[Path]:
        rx = re.compile(pattern, re.I)
        return [f for f in self.files if len(f.parts) <= max_depth + 1 and rx.search(f.name)]

    def find_dirs(self, pattern: str) -> list[Path]:
        rx = re.compile(pattern, re.I)
        return [d for d in self.dirs if rx.search(d.name)]


def scan(root: Path) -> Scan:
    s = Scan(root=root)
    if root.is_file():
        s.root = root.parent
        s.files = [Path(root.name)]
        return s
    count = 0
    for cur, dirnames, filenames in os.walk(root):
        rel = Path(cur).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith("."))
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        for d in dirnames:
            s.dirs.append(rel / d if depth else Path(d))
        for f in sorted(filenames):
            s.files.append(rel / f if depth else Path(f))
            count += 1
            if count >= MAX_ENTRIES:
                return s
    return s


def local_python(root: Path) -> Optional[str]:
    """Bundled interpreter shipped with integrated packages (e.g. runtime/python.exe)."""
    candidates = [
        "runtime/python.exe", "runtime/python", "runtime/bin/python",
        "venv/Scripts/python.exe", ".venv/Scripts/python.exe", "env/Scripts/python.exe",
        "venv/bin/python", ".venv/bin/python", "env/bin/python", "python/python.exe",
    ]
    for c in candidates:
        p = root / c
        if p.exists():
            return str(p)
    return None


def q(path: str) -> str:
    return f'"{path}"'


# --------------------------------------------------------------------------- hint tables
KEYWORDS: dict[str, list[str]] = {
    "vision": ["vision", "visual", "image understanding", "multimodal", "vlm", "image-text-to-text",
               "图像理解", "视觉", "看图", "图片理解", "多模态", "识图", "图像识别"],
    "audio_input": ["audio input", "speech input", "speech understanding", "audio understanding",
                    "语音输入", "语音理解", "音频理解", "听懂"],
    "speech_output": ["speech output", "voice output", "speech generation", "spoken response", "talker",
                      "语音输出", "语音回复", "语音生成", "说话"],
    "asr": ["speech recognition", "automatic speech recognition", "speech-to-text", "语音识别", "语音转文字"],
    "tts": ["text-to-speech", "text to speech", "tts", "语音合成", "文字转语音"],
    "image_generation": ["text-to-image", "text to image", "image generation", "文生图", "图像生成", "图片生成"],
    "image_edit": ["image editing", "image edit", "img2img", "inpaint", "图像编辑", "图片编辑", "改图"],
    "embedding": ["embedding model", "sentence embedding", "text embedding", "向量模型", "嵌入模型"],
}
OMNI_KEYWORDS = ["omni", "any-to-any", "全模态", "端到端语音"]
NAME_RULES: list[tuple[str, list[str]]] = [
    (r"omni|(^|[-_ ])o($|[-_ \d])", ["vision", "audio_input", "speech_output"]),
    (r"(^|[-_ ])(vl|vision|visual|v)($|[-_ \d])", ["vision"]),
    (r"tts|sovits|cosyvoice|fish-?speech|chattts|bark|f5", ["tts"]),
    (r"whisper|asr|sensevoice|paraformer|funasr", ["asr"]),
    (r"image|diffusion|sdxl|flux|(^|[-_ ])sd($|[-_ \d])|kolors|hunyuan-?dit", ["image_generation"]),
    (r"edit|inpaint|kontext", ["image_edit"]),
    (r"embed|bge|e5-|gte|m3e", ["embedding"]),
]
COMPONENT_DIR_RULES: list[tuple[str, str]] = [
    (r"vision|visual|siglip|clip|vit|image_encoder", "vision"),
    (r"whisper|audio_encoder|audio_tower|speech_encoder", "audio_input"),
    (r"talker|vocoder|hifigan|bigvgan|token2wav|cosyvoice|speech_decoder|tts", "speech_output"),
]
CONFIG_KEY_RULES: list[tuple[str, str]] = [
    (r"vision|visual|image_token|vit_|img_", "vision"),
    (r"audio|whisper|mel_|speech_encoder", "audio_input"),
    (r"talker|token2wav|vocoder|speech_gen|tts|code2wav", "speech_output"),
]


def readme_hints(sc: Scan) -> list[Capability]:
    files = [f for f in sc.files if len(f.parts) <= 2 and re.match(r"readme.*\.(md|txt|rst)$", f.name, re.I)]
    text = " ".join(read_text(sc.root / f, 120_000) for f in files[:4]).lower()
    if not text:
        return []
    out: list[Capability] = []
    for ctype, words in KEYWORDS.items():
        hits = [w for w in words if (re.search(rf"\b{re.escape(w)}\b", text) if w.isascii() else w in text)]
        if hits:
            out.append(cap(ctype, 0.3 + 0.08 * min(len(hits), 3), f"README 提到 “{hits[0]}”"))
    omni = [w for w in OMNI_KEYWORDS if w in text]
    if omni:
        for ctype in ("vision", "audio_input", "speech_output"):
            out.append(cap(ctype, 0.3, f"README 提到 “{omni[0]}”（全模态）"))
    return out


def name_hints(name: str) -> list[Capability]:
    out: list[Capability] = []
    lowered = name.lower()
    for pattern, ctypes in NAME_RULES:
        if re.search(pattern, lowered):
            for ctype in ctypes:
                out.append(cap(ctype, 0.4, f"名称 “{name}” 暗示此能力"))
    return out


def component_hints(sc: Scan) -> list[Capability]:
    out: list[Capability] = []
    names = {d.name for d in sc.dirs} | {f.stem for f in sc.files if f.suffix in (".safetensors", ".bin", ".pt", ".pth", ".onnx")}
    for pattern, ctype in COMPONENT_DIR_RULES:
        hits = sorted(n for n in names if re.search(pattern, n, re.I))
        if hits:
            out.append(cap(ctype, 0.5, f"包含组件 “{hits[0]}”"))
    return out


def config_key_hints(cfg: dict[str, Any], label: str = "config.json") -> list[Capability]:
    keys: list[str] = []

    def walk(obj: Any, depth: int) -> None:
        if depth > 2 or not isinstance(obj, dict):
            return
        for k, v in obj.items():
            keys.append(str(k))
            walk(v, depth + 1)

    walk(cfg, 0)
    out: list[Capability] = []
    for pattern, ctype in CONFIG_KEY_RULES:
        hits = [k for k in keys if re.search(pattern, k, re.I)]
        if hits:
            out.append(cap(ctype, 0.6, f"{label} 含字段 “{hits[0]}”"))
    return out


# --------------------------------------------------------------------------- source sniffing
PORT_PATTERNS = [
    r"server_port\s*=\s*(\d{4,5})",
    r"--port[\"']?\s*,[^)]*?default\s*=\s*(\d{4,5})",
    r"(?:--port|-p)[ =](\d{4,5})",
    r"\bport\s*[=:]\s*(\d{4,5})",
]


def sniff_port(text: str) -> Optional[int]:
    for pattern in PORT_PATTERNS:
        m = re.search(pattern, text)
        if m:
            port = int(m.group(1))
            if 1024 <= port <= 65535:
                return port
    return None


@dataclass
class ServerScript:
    path: Path
    kind: str  # openai | gradio | streamlit | fastapi | gpt_sovits
    port: Optional[int]
    caps: list[str]


def sniff_server_scripts(sc: Scan) -> list[ServerScript]:
    py_files = [f for f in sc.files if f.suffix == ".py" and len(f.parts) <= 3]
    py_files.sort(key=lambda f: (len(f.parts), not re.search(r"serve|server|api|app|web|demo", f.name, re.I)))
    found: list[ServerScript] = []
    for f in py_files[:80]:
        text = read_text(sc.root / f, 150_000)
        if not text:
            continue
        caps: list[str] = []
        kind = ""
        if "/v1/chat/completions" in text:
            kind, caps = "openai", ["chat"]
            if re.search(r"image_url|pixel_values|images?\b", text):
                caps.append("vision")
            if "input_audio" in text:
                caps.append("audio_input")
            if re.search(r"modalities|\"audio\"\s*:", text):
                caps.append("speech_output")
        if "/v1/images/generations" in text:
            kind = kind or "openai"
            caps.append("image_generation")
        if "/v1/audio/speech" in text:
            kind = kind or "openai"
            caps.append("tts")
        if "/v1/audio/transcriptions" in text:
            kind = kind or "openai"
            caps.append("asr")
        if "/v1/embeddings" in text:
            kind = kind or "openai"
            caps.append("embedding")
        if re.search(r"[\"']/tts[\"']", text) and re.search(r"ref_audio_path|refer_wav_path", text):
            kind, caps = "gpt_sovits", ["tts", "voice_clone"]
        if not kind and re.search(r"^\s*import gradio|^\s*from gradio", text, re.M) and ".launch(" in text:
            kind = "gradio"
        if not kind and re.search(r"^\s*import streamlit", text, re.M):
            kind = "streamlit"
        if kind:
            port = sniff_port(text)
            if kind == "streamlit":
                port = port or 8501
            if kind == "gradio":
                port = port or 7860
            found.append(ServerScript(f, kind, port, list(dict.fromkeys(caps))))
    return found


def find_scripts(sc: Scan) -> list[Path]:
    out = [f for f in sc.files if len(f.parts) <= 2 and f.suffix.lower() in SCRIPT_EXT]
    out.sort(key=lambda f: (len(f.parts), not re.search(r"start|run|launch|serve|go-|webui|api", f.name, re.I), f.name))
    return out


def script_command(root: Path, rel: Path) -> str:
    p = root / rel
    if rel.suffix.lower() in (".bat", ".cmd"):
        return q(str(p))
    if rel.suffix.lower() == ".ps1":
        return f'powershell -ExecutionPolicy Bypass -File {q(str(p))}'
    return f"bash {q(str(p))}"


# ---------------------------------------------------------------- comfyui / ollama
COMFY_MODEL_DIRS = ("unet", "diffusion_models", "checkpoints", "text_encoders", "clip", "vae", "loras")


def find_comfy_dir(sc: Scan) -> Optional[Path]:
    for f in sc.files:
        if f.name.lower() == "main.py" and len(f.parts) >= 2 and f.parts[-2].lower() == "comfyui":
            return sc.root / f.parent
    return None


def comfy_models(comfy_dir: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    root = comfy_dir / "models"
    for name in COMFY_MODEL_DIRS:
        folder = root / name
        if not folder.is_dir():
            continue
        found[name] = [p for p in sorted(folder.iterdir())
                       if p.is_file() and p.suffix.lower() in (".gguf", ".safetensors", ".ckpt", ".pt", ".pth")]
    return found


def _matching(files: list[Path], *needles: str) -> list[Path]:
    out = list(files)
    for needle in needles:
        hits = [f for f in out if needle in f.name.lower()]
        if hits:
            return hits
    return []


def _one(files: list[Path], *needles: str) -> Optional[Path]:
    hits = _matching(files, *needles)
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_size if p.exists() else 0)


def _best_unet(files: list[Path]) -> Optional[Path]:
    rank = {"q2": 2, "q3": 3, "q4": 4, "q5": 5, "q6": 6, "q8": 8}
    hits = _matching(files, "minimax_h3", "fl2va", "qwen-image", "qwen_image") or list(files)
    if not hits:
        return None

    def score(p: Path) -> tuple[int, int]:
        m = re.search(r"q(\d)_k", p.name.lower())
        return (rank.get(f"q{m.group(1)}", 0) if m else 0,
                p.stat().st_size if p.exists() else 0)

    return max(hits, key=score)


def _num_field(name: str, label: str, default: Any, **extra: Any) -> dict[str, Any]:
    return {"name": name, "label": label, "type": "number", "default": default, **extra}


VIDEO_HINTS = re.compile(
    r"minimax_?h3|fl2v|wan\d|hunyuan_?video|hunyuanvideo|ltx_?video|cogvideo|mochi|cosmos|stable_?video|\bsvd\b|seedance",
    re.I,
)


def _video_presets() -> list[dict[str, Any]]:
    return [
        {"id": "fast", "label": "快速 · 2 秒",
         "values": {"width": 864, "height": 480, "length": 49, "steps": 8}},
        {"id": "local", "label": "本地最高 · 实测",
         "values": {"width": 1152, "height": 640, "length": 22, "steps": 8},
         "note": "RTX 4070 Laptop 8GB 实测稳定档；1344×768 已验证会 OOM，不要在本机直出"},
        {"id": "official", "label": "官方最高 · 15 秒",
         "values": {"width": 1344, "height": 768, "length": 360, "steps": 8},
         "note": "官方开放权重上限（约 1MP、24fps）；8GB 显存会 OOM，仅作参考"},
    ]


def _qwen_image_presets() -> list[dict[str, Any]]:
    return [
        {"id": "standard", "label": "1024 · 标准",
         "values": {"width": 1024, "height": 1024, "resolution": 1024, "steps": 25}},
        {"id": "local", "label": "本地最高 · 2048×1152",
         "values": {"width": 2048, "height": 1152, "resolution": 2048, "steps": 30},
         "note": "你的历史产物已证明这档可用；需以 start-comfyui-2k.bat 启动"},
        {"id": "official", "label": "官方最高 · 2048×2048",
         "values": {"width": 2048, "height": 2048, "resolution": 2048, "steps": 30},
         "note": "官方 2048 质量档；8GB 显存需 2K 启动脚本，可能很慢或 OOM"},
    ]


def _qwen_edit_presets() -> list[dict[str, Any]]:
    return [
        {"id": "standard", "label": "1024 · 标准", "values": {"resolution": 1024, "steps": 25}},
        {"id": "local", "label": "2048 · 本地最高", "values": {"resolution": 2048, "steps": 30},
         "note": "需以 start-comfyui-2k.bat 启动；编辑结果跟随参考图比例"},
    ]


def _minimax_workflow(models: dict[str, list[Path]]) -> Optional[dict[str, Any]]:
    unets = models.get("unet", []) + models.get("diffusion_models", [])
    unet = _best_unet(unets)
    clip = _one(models.get("text_encoders", []) + models.get("clip", []), "minimax_h3", "qwen3vl")
    vae_video = _one(models.get("vae", []), "video_vae", "minimax_h3_video")
    vae_audio = _one(models.get("vae", []), "audio_vae", "minimax_h3_audio")
    if not (unet and clip and vae_video and vae_audio):
        return None
    loras = _matching(models.get("loras", []), "8step") or _matching(models.get("loras", []), "fl2v") or models.get("loras", [])
    lora = max(loras, key=lambda p: p.stat().st_size) if loras else None
    model_source: list[Any] = ["1", 0]
    graph: dict[str, Any] = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet.name}},
        "2": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": clip.name, "type": "minimax"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_video.name}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae_audio.name}},
        "6": {"class_type": "MiniMaxH3ImageToVideo",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": "{{prompt}}",
                         "width": "{{width}}", "height": "{{height}}", "length": "{{length}}",
                         "first_frame": ["20", 0], "last_frame": ["21", 0]}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "8": {"class_type": "BasicScheduler",
              "inputs": {"model": model_source, "scheduler": "simple", "steps": "{{steps}}", "denoise": 1.0}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": "{{seed}}"}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": model_source, "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["9", 0], "guider": ["10", 0], "sampler": ["7", 0],
                          "sigmas": ["8", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo",
               "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24, "bit_depth": 8}},
        "15": {"class_type": "SaveVideo",
               "inputs": {"video": ["14", 0], "filename_prefix": "video/ModelDock", "format": "auto", "codec": "auto"}},
        "20": {"class_type": "LoadImage", "_if": "first_frame", "inputs": {"image": "{{first_frame}}"}},
        "21": {"class_type": "LoadImage", "_if": "last_frame", "inputs": {"image": "{{last_frame}}"}},
    }
    if lora is not None:
        graph["5"] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": ["1", 0], "lora_name": lora.name, "strength_model": 1.0}}
        model_source = ["5", 0]
        graph["8"]["inputs"]["model"] = model_source
        graph["10"]["inputs"]["model"] = model_source
    return graph


def _qwen_workflow(models: dict[str, list[Path]]) -> Optional[dict[str, Any]]:
    unets = models.get("unet", []) + models.get("diffusion_models", [])
    unet = _best_unet(unets)
    clip = _one(models.get("text_encoders", []) + models.get("clip", []), "qwen3vl", "qwen")
    vae = _one(models.get("vae", []), "qwen_image", "vae")
    if not (unet and clip and vae):
        return None
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet.name}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip.name, "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae.name}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": "{{width}}", "height": "{{height}}", "batch_size": 1}},
        "5": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": "{{prompt}}", "negative_prompt": "{{negative_prompt}}",
                         "resolution": "{{resolution}}", "images": []}},
        "6": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["5", 1],
                         "latent_image": ["4", 0], "seed": "{{seed}}", "steps": "{{steps}}",
                         "cfg": "{{cfg}}", "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "ModelDock/qwen"}},
    }


def _qwen_edit_workflow(models: dict[str, list[Path]]) -> Optional[dict[str, Any]]:
    """Image editing: reference images go through the autogrow inputs image_1..16."""
    base = _qwen_workflow(models)
    if not base:
        return None
    unet = base["1"]["inputs"]["unet_name"]
    clip = base["2"]["inputs"]["clip_name"]
    vae = base["3"]["inputs"]["vae_name"]
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": "{{prompt}}",
                         "negative_prompt": "{{negative_prompt}}", "resolution": "{{resolution}}",
                         "images": {"image_1": ["20", 0]}}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["4", 1],
                         "latent_image": ["4", 2], "seed": "{{seed}}", "steps": "{{steps}}",
                         "cfg": "{{cfg}}", "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "ModelDock/qwen-edit"}},
        "20": {"class_type": "LoadImage", "inputs": {"image": "{{image}}"}},
    }


def detect_comfyui(sc: Scan, root: Path) -> Optional[Draft]:
    comfy = find_comfy_dir(sc)
    if not comfy:
        return None
    models = comfy_models(comfy)
    names = " ".join(p.name.lower() for files in models.values() for p in files)
    if not names.strip():
        return None
    video_graph = _minimax_workflow(models) if ("minimax_h3" in names or "fl2va" in names) else None
    image_graph = _qwen_workflow(models) if ("qwen-image" in names or "qwen_image" in names) else None
    edit_graph = _qwen_edit_workflow(models) if image_graph else None
    is_video = bool(VIDEO_HINTS.search(names))
    if not video_graph and not image_graph and not is_video:
        return None

    scripts = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in (".bat", ".cmd", ".sh")] \
        if root.is_dir() else []
    starts = [p for p in scripts if re.search(r"start-comfyui", p.name, re.I)]
    starts.sort(key=lambda p: (p.stem.lower() != "start-comfyui", len(p.name)))
    start = starts[0] if starts else next(
        (p for p in scripts if re.search(r"start|run", p.name, re.I)
         and not re.search(r"stop|test|download", p.name, re.I)), None)
    stops = [p for p in scripts if re.search(r"stop-comfyui", p.name, re.I)]
    stops.sort(key=lambda p: (p.stem.lower() != "stop-comfyui", len(p.name)))
    stop = stops[0] if stops else None
    port = sniff_port(read_text(start, 20_000)) if start and start.suffix.lower() in SCRIPT_EXT else None
    port = port or 8188
    if start and start.suffix.lower() in SCRIPT_EXT:
        command = script_command(root, start.relative_to(root))
    else:
        command = f"{local_python(root) or '{python}'} -s {q(str(comfy / 'main.py'))}"

    d = Draft(adapter="comfyui", description="ComfyUI 工作流项目", use_hints=False)
    d.launch = LaunchConfig(command=command, cwd=str(root), port=port, health_path="/system_stats",
                            startup_timeout=900, keep_running_on_exit=True,
                            stop_command=script_command(root, stop.relative_to(root)) if stop else "")
    d.caps.append(cap("webui", 0.9, "发现 ComfyUI 网页界面",
                      binding={"protocol": "webui", "url": f"http://127.0.0.1:{port}"}))

    base_fields = [
        {"name": "prompt", "label": "提示词", "type": "textarea", "required": True},
        _num_field("width", "宽度", 864),
        _num_field("height", "高度", 480),
        _num_field("length", "帧数", 49, help="24fps，121 帧约 5 秒"),
        _num_field("steps", "采样步数", 8),
        _num_field("seed", "随机种子", -1, help="-1 表示每次随机"),
    ]
    if video_graph:
        actions = [
            {"id": "t2v", "label": "文生视频", "description": "只给文字描述生成视频",
             "fields": base_fields, "presets": _video_presets(),
             "defaults": {"width": 864, "height": 480, "length": 49}},
            {"id": "i2v", "label": "图生视频", "description": "以一张图作为首帧生成视频",
             "fields": base_fields + [{"name": "first_frame", "label": "首帧图片", "type": "image"}],
             "presets": _video_presets(),
             "defaults": {"width": 864, "height": 480, "length": 49}},
            {"id": "flf2v", "label": "首尾帧生视频", "description": "给定首帧与尾帧，生成中间的过渡视频",
             "fields": base_fields + [{"name": "first_frame", "label": "首帧图片", "type": "image"},
                                      {"name": "last_frame", "label": "尾帧图片", "type": "image"}],
             "presets": _video_presets(),
             "defaults": {"width": 864, "height": 480, "length": 121}},
        ]
        d.caps.append(cap("video_generation", 0.95, "发现 MiniMax-H3 视频权重与首尾帧节点",
                          binding={"protocol": "comfyui", "base_url": f"http://127.0.0.1:{port}",
                                   "workflow": video_graph, "timeout": 3600},
                          actions=actions))
    if image_graph:
        actions = [
            {"id": "t2i", "label": "文生图", "description": "按文字描述生成图片",
             "fields": [
                 {"name": "prompt", "label": "画面描述", "type": "textarea", "required": True},
                 {"name": "negative_prompt", "label": "反向提示词", "type": "textarea", "default": ""},
                 _num_field("width", "宽度", 1024),
                 _num_field("height", "高度", 1024),
                 _num_field("resolution", "编码分辨率", 1024),
                 _num_field("steps", "采样步数", 25),
                 _num_field("cfg", "CFG", 1.0),
                 _num_field("seed", "随机种子", -1),
             ],
             "presets": _qwen_image_presets()},
        ]
        if edit_graph:
            actions.append({
                "id": "edit", "label": "图像编辑 / 元素分离",
                "description": "上传一张图并按指令修改；也可以让它把人物、背景、文字等元素分别拆出来",
                "fields": [
                    {"name": "image", "label": "参考图片", "type": "image", "required": True},
                    {"name": "prompt", "label": "编辑指令", "type": "textarea", "required": True,
                     "placeholder": "例如：把人物、背景、文字分别拆成透明图层"},
                    {"name": "negative_prompt", "label": "反向提示词", "type": "textarea", "default": ""},
                    _num_field("resolution", "编码分辨率", 1024),
                    _num_field("steps", "采样步数", 25),
                    _num_field("cfg", "CFG", 1.0),
                    _num_field("seed", "随机种子", -1),
                ],
                "presets": _qwen_edit_presets(),
                "workflow": edit_graph,
            })
        d.caps.append(cap("image_generation", 0.9, "发现 Qwen-Image 权重",
                          binding={"protocol": "comfyui", "base_url": f"http://127.0.0.1:{port}",
                                   "workflow": image_graph, "timeout": 1800},
                          actions=actions))
        d.caps.append(cap("image_edit", 0.85, "Qwen-Image 2.1 支持参考图编辑",
                          binding={"protocol": "comfyui", "base_url": f"http://127.0.0.1:{port}",
                                   "workflow": edit_graph or image_graph, "timeout": 1800},
                          actions=[a for a in actions if a["id"] == "edit"] or actions[:1]))
        d.notes.append("2048×2048（2K）需要以 start-comfyui-2k.bat 启动；普通启动模式在 2K 下可能崩溃。")
    elif is_video and not video_graph:
        sample = next(iter(sorted(p.name for files in models.values() for p in files)), "")
        v = cap("video_generation", 0.7, f"发现视频权重 {sample}",
                binding={"protocol": "comfyui", "base_url": f"http://127.0.0.1:{port}"})
        v.enabled = False
        v.evidence.append("尚未内置该视频家族的工作流：可在能力 JSON 里补充 binding.workflow 后启用")
        d.caps.append(v)
        d.notes.append("识别到视频模型，但 ModelDock 还没有它的内置工作流；可以先配置工作流，或直接用原生界面。")
    d.notes.append(f"ComfyUI 目录：{comfy}")
    d.notes.append("工作台通过 ComfyUI 的 /prompt 接口提交工作流；首次生成需要加载模型，可能较慢。")
    return d


def detect_ollama(sc: Scan, root: Path) -> Optional[Draft]:
    modelfile = sc.has("Modelfile") or sc.has("modelfile")
    bat = next((p for p in root.iterdir() if p.is_file() and re.search(r"start-ollama", p.name, re.I)), None) \
        if root.is_dir() else None
    has_ollama = (root / "ollama").is_dir() or bat is not None
    if not (modelfile or has_ollama):
        return None

    served = ""
    num_ctx = 0
    if modelfile:
        text = read_text(root / modelfile, 20_000)
        m = re.search(r"^\s*FROM\s+(\S+)", text, re.M | re.I)
        if m:
            served = m.group(1)
        m = re.search(r"^\s*PARAMETER\s+num_ctx\s+(\d+)", text, re.M | re.I)
        if m:
            num_ctx = int(m.group(1))
    port = 11434
    command = ""
    if bat is not None:
        command = script_command(root, bat.relative_to(root))
        m = re.search(r"OLLAMA_HOST\s*=\s*[^\s:]*:(\d{2,5})", read_text(bat, 20_000), re.I)
        if m:
            port = int(m.group(1))
    elif local_python(root):
        command = f"{q(local_python(root))} -m ollama serve"
    else:
        command = "ollama serve"

    d = Draft(adapter="ollama", description="Ollama 本地模型服务", use_hints=False)
    d.launch = LaunchConfig(command=command, cwd=str(root), port=port, health_path="/v1/models",
                            startup_timeout=300)
    d.caps.append(cap("chat", 0.95, "检测到 Ollama 模型目录", binding={"protocol": "ollama_chat"}))
    if served and re.search(r"embed|bge|gte", served, re.I):
        d.caps.append(cap("embedding", 0.9, f"Ollama 模型 {served} 疑似向量模型",
                          binding={"protocol": "openai_embeddings"}))
    if served:
        d.defaults["model"] = served
    if num_ctx:
        d.defaults["num_ctx"] = num_ctx
    d.notes.append("ModelDock 对话走 Ollama 原生 /api/chat，每次请求显式携带 num_ctx。")
    if num_ctx:
        d.notes.append(f"上下文长度 {num_ctx} tokens（来自 Modelfile 的 num_ctx）。")
    if served:
        d.notes.append(f"模型名：{served}")
    return d



# --------------------------------------------------------------------------- adapters
@dataclass
class Draft:
    adapter: str = "custom"
    description: str = ""
    caps: list[Capability] = field(default_factory=list)
    launch: LaunchConfig = field(default_factory=LaunchConfig)
    notes: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    use_hints: bool = True
    # A project whose only interface is a Gradio/Streamlit page: no OpenAI-compatible API.
    gradio_only: bool = False


def detect_gpt_sovits(sc: Scan, root: Path) -> Optional[Draft]:
    api = sc.has("api_v2.py") or sc.has("api.py")
    if not (sc.top("GPT_SoVITS") or (api and sc.find_dirs(r"sovits"))):
        return None
    py = local_python(root)
    pyref = q(py) if py else "{python}"
    d = Draft(adapter="gpt_sovits", description="GPT-SoVITS 少样本语音合成 / 声音克隆", use_hints=False)
    api_name = "api_v2.py" if sc.has("api_v2.py") else "api.py"
    cfg = "GPT_SoVITS/configs/tts_infer.yaml"
    cmd = f"{pyref} {api_name} -a {{host}} -p {{port}}"
    if api_name == "api_v2.py" and (root / cfg).exists():
        cmd += f" -c {cfg}"
    d.launch = LaunchConfig(command=cmd, cwd=str(root), port=9880, health_path="/docs", startup_timeout=600)
    if py:
        d.notes.append(f"使用整合包自带的 Python：{py}")
    binding = {"protocol": "gpt_sovits", "api": "v2" if api_name == "api_v2.py" else "v1"}
    d.caps += [
        cap("tts", 0.95, f"发现 {api_name}（GPT-SoVITS 推理接口）", binding=binding),
        cap("voice_clone", 0.95, "GPT-SoVITS 支持参考音频零样本克隆", binding=binding),
    ]
    webui = sc.has("webui.py")
    if webui:
        bat = sc.has("go-webui.bat")
        ui_cmd = script_command(root, bat) if bat and os.name == "nt" else f"{pyref} webui.py zh_CN"
        d.caps.append(cap("webui", 0.8, "发现 webui.py（训练与推理界面）",
                          binding={"protocol": "webui", "url": "http://127.0.0.1:9874", "command": ui_cmd,
                                   "cwd": str(root), "port": 9874}))
    weights = sc.find_dirs(r"^(GPT|SoVITS)_weights")
    if weights:
        d.notes.append("可在工作台中切换已训练的音色权重：" + "、".join(w.name for w in weights[:4]))
    d.notes.append("推理约需 2~3GB 显存；与 ComfyUI 视频模型同时运行可能 CUDA OOM。")
    return d


def detect_diffusers(sc: Scan, root: Path, settings: Settings) -> Optional[Draft]:
    mi = sc.has("model_index.json")
    if not mi:
        return None
    info = read_json(root / mi)
    cls = str(info.get("_class_name", "DiffusionPipeline"))
    d = Draft(adapter="diffusers", description=f"Diffusers 管线 {cls}", use_hints=False)
    is_edit = re.search(r"edit|img2img|inpaint|kontext|instruct", cls, re.I)
    if is_edit:
        d.caps.append(cap("image_edit", 0.95, f"model_index.json: {cls}"))
        d.caps.append(cap("image_generation", 0.35, "编辑管线通常也可直接生成"))
    else:
        d.caps.append(cap("image_generation", 0.95, f"model_index.json: {cls}"))
    if re.search(r"video|wan|cogvideo|ltx|hunyuanvideo", cls, re.I):
        d.notes.append("这是视频管线，当前以图像接口调用，可能需要自定义接口。")
    model_dir = root / mi.parent if mi.parent != Path(".") else root
    d.launch = LaunchConfig(
        command=f'{{python}} "{{servers}}/diffusers_server.py" --model "{model_dir}" --host {{host}} --port {{port}}',
        cwd=str(root), health_path="/health", startup_timeout=900,
    )
    d.notes.append("将使用 ModelDock 内置的 diffusers 推理服务启动。")
    if not has_module("diffusers"):
        d.notes.append("当前 Python 环境未安装 diffusers/torch，请在设置中指定已安装依赖的 Python 解释器。")
    return d


def detect_gguf(sc: Scan, root: Path) -> Optional[Draft]:
    ggufs = [f for f in sc.files if f.suffix.lower() == ".gguf"]
    if not ggufs:
        return None
    mmproj = [f for f in ggufs if "mmproj" in f.name.lower()]
    mains = [f for f in ggufs if f not in mmproj]
    if not mains:
        return None
    main = max(mains, key=lambda f: (root / f).stat().st_size if (root / f).exists() else 0)
    d = Draft(adapter="gguf", description=f"GGUF 量化模型 {main.name}")
    if VIDEO_HINTS.search(main.name):
        v = cap("video_generation", 0.8, f"发现视频 GGUF 权重 {main.name}", binding={"protocol": "custom_http"})
        v.enabled = False
        d.caps.append(v)
        d.notes.append("这是视频 GGUF：内置聊天服务不能运行它，请配置 ComfyUI / llama.cpp 的视频工作流。")
        d.launch = LaunchConfig(cwd=str(root))
        return d
    else:
        d.caps.append(cap("chat", 0.9, f"发现 GGUF 权重 {main.name}"))
    extra = ""
    if mmproj:
        d.caps.append(cap("vision", 0.85, f"发现视觉投影 {mmproj[0].name}"))
        extra = f' --mmproj "{root / mmproj[0]}"'
    server = which("llama-server")
    if server:
        cmd = f'{q(server)} -m "{root / main}"{extra} --host {{host}} --port {{port}} -c 8192'
    elif has_module("llama_cpp"):
        cmd = f'{{python}} -m llama_cpp.server --model "{root / main}" --host {{host}} --port {{port}}'
    else:
        cmd = f'llama-server -m "{root / main}"{extra} --host {{host}} --port {{port}} -c 8192'
        d.notes.append("未在 PATH 中找到 llama-server，请安装 llama.cpp 或修改启动命令。")
    d.launch = LaunchConfig(command=cmd, cwd=str(root), health_path="/v1/models", startup_timeout=300)
    return d


def analyze_hf_config(cfg: dict[str, Any]) -> tuple[list[Capability], str]:
    archs = [str(a) for a in cfg.get("architectures") or []]
    mtype = str(cfg.get("model_type", ""))
    sig = " ".join(archs + [mtype]).lower()
    caps: list[Capability] = []
    label = archs[0] if archs else (mtype or "未知架构")
    if "whisper" in sig or re.search(r"forspeechseq2seq|forctc", sig):
        return [cap("asr", 0.9, f"架构 {label}")], label
    if re.search(r"(bert|roberta|xlm|e5|bge|gte|nomic)", sig) and not re.search(r"causallm|generation", sig):
        return [cap("embedding", 0.8, f"编码器架构 {label}")], label
    if re.search(r"causallm|conditionalgeneration|forimagetexttotext|lmhead|chat", sig) or cfg.get("text_config"):
        caps.append(cap("chat", 0.9, f"生成式架构 {label}"))
    omni = "omni" in sig or any(k in cfg for k in ("talker_config", "token2wav_config", "thinker_config"))
    if omni:
        caps.append(cap("chat", 0.9, f"全模态架构 {label}"))
    if cfg.get("vision_config") or re.search(r"vl|vision|llava|image", sig) or "image_token_id" in cfg \
            or (cfg.get("thinker_config") or {}).get("vision_config"):
        caps.append(cap("vision", 0.85, "config.json 含 vision_config / 视觉架构"))
    if cfg.get("audio_config") or "audio" in sig or "audio_token_id" in cfg \
            or (cfg.get("thinker_config") or {}).get("audio_config"):
        caps.append(cap("audio_input", 0.85, "config.json 含 audio_config"))
    if cfg.get("talker_config") or cfg.get("token2wav_config") or cfg.get("enable_audio_output"):
        caps.append(cap("speech_output", 0.85, "config.json 含 talker / token2wav 语音解码器"))
    if re.search(r"video|wan|hunyuan_?video|cogvideo|ltx|mochi|cosmos|stable_?video|\bsvd\b", sig):
        v = cap("video_generation", 0.72, f"视频架构 {label}", binding={"protocol": "custom_http"})
        v.enabled = False
        v.evidence.append("ModelDock 内置服务暂不支持视频推理，请配置启动命令与接口")
        caps.append(v)
    return caps, label


def detect_hf(sc: Scan, root: Path, settings: Settings) -> Optional[Draft]:
    cfg_rel = sc.has("config.json")
    if not cfg_rel:
        cands = [f for f in sc.files if f.name == "config.json" and len(f.parts) <= 3]
        cfg_rel = cands[0] if cands else None
    if not cfg_rel:
        return None
    cfg = read_json(root / cfg_rel)
    has_weights = any(f.suffix in (".safetensors", ".bin", ".pt", ".pth") for f in sc.files)
    if not cfg or not (cfg.get("architectures") or cfg.get("model_type")) or not has_weights:
        return None
    caps, label = analyze_hf_config(cfg)
    caps = merge_caps(caps, config_key_hints(cfg))
    model_dir = root / cfg_rel.parent if cfg_rel.parent != Path(".") else root
    for name in ("preprocessor_config.json", "processor_config.json", "video_preprocessor_config.json"):
        pre = model_dir / name
        if pre.exists():
            pc = read_json(pre)
            if pc.get("image_processor_type") or "image_mean" in pc or name.startswith("video"):
                caps = merge_caps(caps, [cap("vision", 0.6, f"发现 {name}（图像预处理）")])
            if "feature_extractor_type" in pc or "sampling_rate" in pc:
                caps = merge_caps(caps, [cap("audio_input", 0.6, f"发现 {name}（音频特征提取）")])
    tok = read_json(model_dir / "tokenizer_config.json")
    if tok.get("chat_template") or (model_dir / "chat_template.jinja").exists() or (model_dir / "chat_template.json").exists():
        caps = merge_caps(caps, [cap("chat", 0.6, "带有 chat_template")])
    if (model_dir / "modules.json").exists() or (model_dir / "sentence_bert_config.json").exists():
        caps = merge_caps(caps, [cap("embedding", 0.9, "sentence-transformers 结构")])
    types = {c.type for c in caps if c.confidence >= ENABLE_THRESHOLD}
    adapter = "hf_omni" if {"audio_input", "speech_output"} & types else "hf_llm"
    d = Draft(adapter=adapter, description=f"Transformers 模型 · {label}", caps=caps)
    d.defaults["model"] = root.name
    vllm = which("vllm")
    if adapter == "hf_llm" and "embedding" not in types and (vllm or has_module("vllm")):
        exe = q(vllm) if vllm else "{python} -m vllm.entrypoints.openai.api_server --model"
        lead = f"{exe} serve" if vllm else exe
        d.launch = LaunchConfig(
            command=f'{lead} "{model_dir}" --host {{host}} --port {{port}} --served-model-name "{root.name}" --trust-remote-code',
            cwd=str(root), health_path="/v1/models", startup_timeout=1200,
        )
        d.notes.append("检测到 vLLM，将使用 vLLM 启动 OpenAI 兼容服务。")
    else:
        d.launch = LaunchConfig(
            command=f'{{python}} "{{servers}}/hf_chat_server.py" --model "{model_dir}" --name "{root.name}" --host {{host}} --port {{port}}',
            cwd=str(root), health_path="/health", startup_timeout=1200,
        )
        d.notes.append("将使用 ModelDock 内置的 transformers 推理服务启动（也可改成 vLLM / SGLang / LMDeploy 命令）。")
        if adapter == "hf_omni":
            d.notes.append("全模态模型的语音输出依赖模型自带推理代码，若内置服务不支持，请改用模型官方启动脚本。")
    return d


def detect_minimind(sc: Scan, root: Path) -> Optional[Draft]:
    """MiniMind-O style repo: a Gradio demo plus a small HF model folder.

    We do not drive the Gradio UI.  ModelDock ships servers/omni_server.py, which
    imports the project's own demo helpers and exposes a small HTTP API, so the
    workbench can provide a native text/image/audio page.
    """
    demo_file = next((f for f in sc.files if f.name == "web_demo_omni.py"), None)
    if not demo_file:
        return None
    scripts_dir = root / demo_file.parent
    repo = scripts_dir.parent
    if not scripts_dir.is_dir():
        return None
    candidates = []
    for sub in sorted(scripts_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith((".", "_")):
            continue
        has_weights = (sub / "pytorch_model.bin").exists() or (sub / "model.safetensors").exists()
        if (sub / "config.json").exists() and has_weights:
            candidates.append(sub)
    if not candidates:
        return None
    model_dir = next((c for c in candidates if "minimind" in c.name.lower()), candidates[0])

    venv = root / ".venv" / "Scripts" / "python.exe"
    if not venv.exists():
        venv = root / ".venv" / "bin" / "python"
    pyref = q(str(venv)) if venv.exists() else "{python}"
    command = (f'{pyref} "{{servers}}/omni_server.py" --repo {q(str(repo))} '
               f'--model {q(str(model_dir))} --host {{host}} --port {{port}}')

    d = Draft(adapter="omni", description="MiniMind-O 全模态模型（文字 / 图片 / 语音）", use_hints=False)
    d.launch = LaunchConfig(command=command, cwd=str(root), python=str(venv) if venv.exists() else "",
                            health_path="/health", startup_timeout=900)
    binding = {"protocol": "omni"}
    d.caps += [
        cap("chat", 0.95, "发现 MiniMind-O 的 web_demo_omni.py 与模型权重", binding=binding),
        cap("vision", 0.9, "MiniMind-O 自带 SigLIP 视觉编码器", binding=binding),
        cap("audio_input", 0.9, "MiniMind-O 自带 SenseVoice 语音编码器", binding=binding),
        cap("speech_output", 0.9, "MiniMind-O 自带 Mimi 语音解码器", binding=binding),
    ]
    d.defaults["model"] = model_dir.name
    d.notes.append(f"使用项目自带解释器：{venv}" if venv.exists() else "未找到项目自带 .venv，请手动指定解释器")
    d.notes.append("由 ModelDock 的 omni_server.py 提供文字 / 图片 / 语音输入与语音回复，无需 Gradio 页面。")
    return d


def detect_script_project(sc: Scan, root: Path) -> Optional[Draft]:
    servers = sniff_server_scripts(sc)
    scripts = find_scripts(sc)
    if not servers and not scripts:
        return None
    py = local_python(root)
    pyref = q(py) if py else "{python}"
    d = Draft(adapter="script_project", description="包含启动脚本的模型项目")
    api = next((s for s in servers if s.kind in ("openai", "gpt_sovits", "fastapi")), None)
    ui = next((s for s in servers if s.kind in ("gradio", "streamlit")), None)
    start_script = next((s for s in scripts if re.search(r"start|run|launch|serve|api|webui", s.name, re.I)
                         and not re.search(r"stop|kill|train|test|bench|smoke|download", s.name, re.I)), None)
    stop_script = next((s for s in scripts if re.search(r"stop|kill|shutdown", s.name, re.I)), None)
    if api:
        d.launch = LaunchConfig(command=f"{pyref} {api.path.as_posix()}", cwd=str(root), port=api.port,
                                health_path="/v1/models" if api.kind == "openai" else "")
        if api.kind == "gpt_sovits":
            d.caps += [cap(c, 0.85, f"{api.path.as_posix()} 提供 /tts 接口", binding={"protocol": "gpt_sovits"}) for c in api.caps]
        else:
            d.caps += [cap(c, 0.85 if c == "chat" else 0.6, f"{api.path.as_posix()} 实现了对应接口") for c in api.caps]
        d.notes.append(f"发现 API 服务脚本 {api.path.as_posix()}" + (f"（端口 {api.port}）" if api.port else ""))
    elif start_script:
        text = read_text(root / start_script, 50_000)
        d.launch = LaunchConfig(command=script_command(root, start_script), cwd=str(root), port=sniff_port(text))
        d.notes.append(f"使用启动脚本 {start_script.as_posix()}")
    if stop_script:
        d.launch.stop_command = script_command(root, stop_script)
        d.notes.append(f"使用停止脚本 {stop_script.as_posix()}")
    if ui:
        ui_cmd = (f"{pyref} -m streamlit run {ui.path.as_posix()} --server.port {ui.port} --server.headless true"
                  if ui.kind == "streamlit" else f"{pyref} {ui.path.as_posix()}")
        binding: dict[str, Any] = {"protocol": "webui", "url": f"http://127.0.0.1:{ui.port}", "port": ui.port}
        if api:
            # API and UI are separate processes: the UI becomes a sub-service.
            binding.update(command=ui_cmd, cwd=str(root))
        else:
            # The UI *is* the main service; reuse the main service so we never start it twice.
            if not d.launch.command:
                d.launch = LaunchConfig(command=ui_cmd, cwd=str(root), port=ui.port)
                d.adapter = "gradio_app"
            d.launch.port = d.launch.port or ui.port
            d.gradio_only = True
        d.caps.append(cap("webui", 0.8, f"发现 {ui.kind} 界面 {ui.path.as_posix()}", binding=binding))
    if py:
        d.notes.append(f"使用项目自带的 Python：{py}")
    return d


def apply_manifest(root: Path, d: Draft) -> tuple[Draft, Optional[str], list[str]]:
    mf = root / "modeldock.json"
    if not mf.exists():
        return d, None, []
    data = read_json(mf)
    if not data:
        return d, None, ["modeldock.json 无法解析，已忽略"]
    name = data.get("name")
    d.description = data.get("description", d.description)
    d.adapter = data.get("adapter", d.adapter if d.adapter != "custom" else "script_project")
    if isinstance(data.get("launch"), dict):
        d.launch = LaunchConfig(**{**d.launch.model_dump(), "cwd": str(root), **data["launch"]})
    manifest_caps = []
    for item in data.get("capabilities", []):
        if isinstance(item, str):
            manifest_caps.append(cap(item, 1.0, "modeldock.json 声明", source="manifest"))
        elif isinstance(item, dict) and item.get("type"):
            manifest_caps.append(cap(item["type"], 1.0, "modeldock.json 声明", source="manifest",
                                     binding=item.get("binding") or default_binding(item["type"]), label=item.get("label")))
    if data.get("capabilities_only"):
        d.caps = manifest_caps
    else:
        d.caps = merge_caps(d.caps, manifest_caps)
    d.defaults.update(data.get("defaults", {}))
    d.use_hints = d.use_hints and not data.get("capabilities_only")
    return d, name, ["已读取 modeldock.json 清单"]


def prettify(name: str) -> str:
    return re.sub(r"[_]+", " ", name).strip() or name


# --------------------------------------------------------------------------- entry point
def detect(path_str: str, settings: Optional[Settings] = None, plugin_detectors: Optional[list] = None) -> DetectResult:
    settings = settings or Settings()
    raw = path_str.strip().strip('"')
    if re.match(r"^https?://", raw):
        return DetectResult(
            path=raw, exists=True, name=re.sub(r"^https?://", "", raw).split("/")[0], adapter="openai_remote",
            adapter_label=ADAPTERS["openai_remote"], description="连接到已在运行的模型服务",
            launch=LaunchConfig(), notes=["这是一个远程/已运行的服务地址，ModelDock 不负责启动它；保存后可点击“探测能力”。"],
        )
    root = Path(os.path.expanduser(raw))
    if not root.exists():
        return DetectResult(path=raw, exists=False, name=prettify(Path(raw).name or raw), adapter="custom",
                            adapter_label=ADAPTERS["custom"], notes=["路径不存在，请检查后重试。"])
    sc = scan(root)
    base = root if root.is_dir() else root.parent
    name = root.stem if root.is_file() else root.name

    draft = (detect_gpt_sovits(sc, base) or detect_diffusers(sc, base, settings) or detect_gguf(sc, base)
             or detect_comfyui(sc, base) or detect_ollama(sc, base) or detect_minimind(sc, base) or None)
    if draft is None:
        hf = detect_hf(sc, base, settings)
        proj = detect_script_project(sc, base)
        if hf and proj:
            # Project with its own server script (e.g. MiniMind) beats the generic runner.
            if proj.launch.command:
                hf.launch = proj.launch
                hf.notes = [n for n in hf.notes if "内置" not in n and "vLLM" not in n] + proj.notes
            hf.caps = merge_caps(hf.caps, proj.caps)
            draft = hf
        else:
            draft = hf or proj
    draft = draft or Draft(adapter="custom", notes=["未能识别模型类型，请手动填写启动命令与能力。"])

    if draft.use_hints:
        hints = name_hints(name) + readme_hints(sc) + component_hints(sc)
        draft.caps = merge_caps(draft.caps, hints)
    notes = list(draft.notes)

    for det in plugin_detectors or []:
        try:
            res = det(base, sc)
        except Exception as exc:  # pragma: no cover - plugin bugs must not break detection
            notes.append(f"插件 {getattr(det, '__module__', det)} 出错：{exc}")
            continue
        if not res:
            continue
        draft.adapter = res.get("adapter", draft.adapter)
        draft.description = res.get("description", draft.description)
        if res.get("launch"):
            draft.launch = LaunchConfig(**{**draft.launch.model_dump(), **res["launch"]})
        plugin_caps = [Capability(**{"source": "plugin", "confidence": 0.9, "binding": default_binding(c["type"]), **c})
                       for c in res.get("capabilities", [])]
        draft.caps = merge_caps(draft.caps, plugin_caps)
        notes += res.get("notes", [])

    draft, manifest_name, manifest_notes = apply_manifest(base, draft)
    notes = manifest_notes + notes

    if draft.gradio_only:
        # A Gradio page has no OpenAI-compatible endpoint: never advertise chat that cannot work.
        for c in draft.caps:
            if c.type in ("chat", "vision", "audio_input", "speech_output", "embedding") \
                    and c.binding.get("protocol", "").startswith("openai") \
                    and c.source not in ("manifest", "user"):
                c.enabled = False
                c.evidence = c.evidence + ["该项目只有 Gradio 界面，没有 OpenAI 兼容接口"]
        notes.append("该项目只有 Gradio / Streamlit 界面：请使用“原生界面”面板，"
                     "或在能力里手动配置 API 地址后再启用对话。")

    if not draft.launch.cwd:
        draft.launch.cwd = str(base)
    order = list(CAPABILITIES)
    caps = sorted(draft.caps, key=lambda c: (order.index(c.type) if c.type in order else 99, -c.confidence))
    return DetectResult(
        path=str(root), exists=True, name=manifest_name or prettify(name), adapter=draft.adapter,
        adapter_label=ADAPTERS.get(draft.adapter, draft.adapter), description=draft.description,
        capabilities=caps, launch=draft.launch,
        scripts=[s.as_posix() for s in find_scripts(sc)][:12], notes=notes, defaults=draft.defaults,
    )

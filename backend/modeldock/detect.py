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
        binding: Optional[dict] = None, label: Optional[str] = None) -> Capability:
    ev = [evidence] if isinstance(evidence, str) else list(evidence)
    return Capability(
        type=type_, confidence=round(min(confidence, 1.0), 2), evidence=ev, source=source,  # type: ignore[arg-type]
        binding=binding or default_binding(type_), enabled=confidence >= ENABLE_THRESHOLD, label=label,
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
    d.launch = LaunchConfig(command=cmd, cwd=str(root), port=9880, health_path="/docs", startup_timeout=300)
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
    start_script = next((s for s in scripts if re.search(r"start|run|launch|serve|api", s.name, re.I)
                         and not re.search(r"stop|kill|train", s.name, re.I)), None)
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
        if api or start_script:
            binding.update(command=ui_cmd, cwd=str(root))
        else:
            d.launch = LaunchConfig(command=ui_cmd, cwd=str(root), port=ui.port)
            d.adapter = "gradio_app"
            binding["url"] = ""
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
             or None)
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

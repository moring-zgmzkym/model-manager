"""Example ModelDock plugin — rename to `my_plugin.py` (no leading underscore) to enable.

A plugin teaches ModelDock to recognise a new kind of model folder. ``detect``
receives the folder path and a ``scan`` object (``scan.files`` / ``scan.dirs``
are relative paths, ``scan.has("name")`` finds a top-level file). Return
``None`` when the folder isn't yours.
"""

NAME = "示例：CosyVoice 语音合成"
DESCRIPTION = "识别 CosyVoice 项目并用其 FastAPI 服务启动"


def detect(root, scan):
    if not (root / "cosyvoice").is_dir():
        return None
    return {
        "adapter": "script_project",
        "description": "CosyVoice 语音合成",
        "launch": {
            "command": "{python} runtime/python/fastapi/server.py --port {port} --model_dir pretrained_models/CosyVoice2-0.5B",
            "health_path": "/docs",
        },
        "capabilities": [
            {"type": "tts", "evidence": ["发现 cosyvoice 目录"],
             "binding": {"protocol": "custom_http", "method": "GET", "path": "/inference_sft",
                         "query": {"tts_text": "{{text}}", "spk_id": "{{voice}}"},
                         "fields": [{"name": "text", "label": "文字", "type": "textarea"},
                                    {"name": "voice", "label": "音色", "default": "中文女"}]}},
        ],
        "notes": ["由 CosyVoice 示例插件识别"],
    }

"""The vocabulary of capabilities and wire protocols.

A *capability* is what the user sees ("语音合成"), a *protocol* is how
ModelDock talks to the running model ("gpt_sovits"). The frontend picks a
workbench panel by capability ``type``; the backend picks a handler by
``binding.protocol``.
"""

from __future__ import annotations

from typing import Any

CAPABILITIES: dict[str, dict[str, Any]] = {
    "chat": {"label": "文本对话", "group": "language", "panel": "chat", "protocol": "openai_chat",
             "desc": "多轮对话、写作、问答与推理"},
    "vision": {"label": "图像理解", "group": "language", "panel": "chat", "protocol": "openai_chat",
               "desc": "上传图片，让模型描述、识别或回答问题"},
    "audio_input": {"label": "语音输入", "group": "language", "panel": "chat", "protocol": "openai_chat",
                    "desc": "在对话中直接发送语音"},
    "speech_output": {"label": "语音输出", "group": "language", "panel": "chat", "protocol": "openai_chat",
                      "desc": "模型以语音回答"},
    "asr": {"label": "语音识别", "group": "audio", "panel": "asr", "protocol": "openai_transcription",
            "desc": "把录音或音频文件转成文字"},
    "tts": {"label": "语音合成", "group": "audio", "panel": "tts", "protocol": "openai_speech",
            "desc": "把文字读出来"},
    "voice_clone": {"label": "声音克隆", "group": "audio", "panel": "tts", "protocol": "gpt_sovits",
                    "desc": "提供一段参考音频，用该音色朗读"},
    "image_generation": {"label": "文生图", "group": "image", "panel": "image", "protocol": "openai_images",
                         "desc": "根据描述生成图片"},
    "image_edit": {"label": "图像编辑", "group": "image", "panel": "image", "protocol": "openai_images",
                   "desc": "根据指令修改已有图片"},
    "embedding": {"label": "文本向量", "group": "language", "panel": "embedding", "protocol": "openai_embeddings",
                  "desc": "计算文本向量与相似度"},
    "webui": {"label": "原生界面", "group": "tools", "panel": "webui", "protocol": "webui",
              "desc": "在 ModelDock 内打开模型自带的网页界面"},
    "custom": {"label": "自定义接口", "group": "tools", "panel": "custom", "protocol": "custom_http",
               "desc": "按模板调用任意 HTTP 接口"},
}

PROTOCOLS: dict[str, str] = {
    "openai_chat": "OpenAI 兼容 /v1/chat/completions",
    "openai_images": "OpenAI 兼容 /v1/images/generations",
    "openai_speech": "OpenAI 兼容 /v1/audio/speech",
    "openai_transcription": "OpenAI 兼容 /v1/audio/transcriptions",
    "openai_embeddings": "OpenAI 兼容 /v1/embeddings",
    "gpt_sovits": "GPT-SoVITS api_v2 /tts",
    "webui": "嵌入网页界面",
    "custom_http": "自定义 HTTP 模板",
}

ADAPTERS: dict[str, str] = {
    "hf_llm": "Transformers 语言模型",
    "hf_omni": "全模态模型",
    "gguf": "GGUF / llama.cpp",
    "diffusers": "Diffusers 图像模型",
    "gpt_sovits": "GPT-SoVITS 语音",
    "gradio_app": "Gradio / WebUI 项目",
    "script_project": "脚本项目",
    "openai_remote": "已运行的 API 服务",
    "custom": "自定义",
}


def default_binding(cap_type: str) -> dict[str, Any]:
    info = CAPABILITIES.get(cap_type)
    return {"protocol": info["protocol"]} if info else {"protocol": "custom_http"}


def catalog_payload() -> dict[str, Any]:
    return {"capabilities": CAPABILITIES, "protocols": PROTOCOLS, "adapters": ADAPTERS}

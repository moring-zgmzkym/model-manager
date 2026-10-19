import type { Capability, Model } from "./types";

/** Workbench panels, in display order. A panel shows when any of its capabilities is enabled. */
export const PANELS: { id: string; label: string; caps: string[]; hint: string }[] = [
  { id: "chat", label: "对话", caps: ["chat", "vision", "audio_input", "speech_output"], hint: "聊天、看图、语音对话" },
  { id: "image", label: "图像", caps: ["image_generation", "image_edit"], hint: "文生图与图像编辑" },
  { id: "video", label: "视频", caps: ["video_generation"], hint: "文生视频、首尾帧过渡" },
  { id: "tts", label: "语音合成", caps: ["tts", "voice_clone"], hint: "朗读文字、克隆音色" },
  { id: "asr", label: "语音识别", caps: ["asr"], hint: "音频转文字" },
  { id: "embedding", label: "向量", caps: ["embedding"], hint: "文本相似度" },
  { id: "webui", label: "原生界面", caps: ["webui"], hint: "模型自带的网页" },
  { id: "custom", label: "自定义", caps: ["custom"], hint: "自定义 HTTP 接口" },
];

export function enabledCaps(m?: Model): Capability[] {
  return (m?.capabilities || []).filter((c) => c.enabled);
}

export function has(m: Model | undefined, type: string): boolean {
  return enabledCaps(m).some((c) => c.type === type);
}

export function panelsFor(m?: Model) {
  const types = new Set(enabledCaps(m).map((c) => c.type));
  return PANELS.filter((p) => p.caps.some((c) => types.has(c)));
}

/** Task shortcuts on the overview page. */
export const TASKS: { id: string; label: string; desc: string; caps: string[]; panel: string }[] = [
  { id: "chat", label: "聊天与写作", desc: "问答、写作、编程、推理", caps: ["chat"], panel: "chat" },
  { id: "vision", label: "看图说话", desc: "上传图片让模型理解", caps: ["vision"], panel: "chat" },
  { id: "image", label: "生成图片", desc: "用一句话描述画面", caps: ["image_generation", "image_edit"], panel: "image" },
  { id: "video", label: "生成视频", desc: "文生视频、首尾帧过渡", caps: ["video_generation"], panel: "video" },
  { id: "voice", label: "朗读与配音", desc: "文字转语音、声音克隆", caps: ["tts", "voice_clone"], panel: "tts" },
];

export function confidenceLabel(c: Capability): string {
  if (c.source === "manifest") return "清单声明";
  if (c.source === "user") return "手动添加";
  if (c.source === "runtime") return "运行时确认";
  if (c.confidence >= 0.85) return "高可信";
  if (c.confidence >= 0.5) return "推测";
  return "可能支持";
}

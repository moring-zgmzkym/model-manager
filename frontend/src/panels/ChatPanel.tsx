import { ArrowUp, Brain, ImagePlus, Mic, SlidersHorizontal, Square, Trash2, Volume2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Markdown, splitThink } from "../components/Markdown";
import { Field, Slider } from "../components/ui";
import { streamSSE } from "../lib/api";
import { blobToDataUrl, useRecorder } from "../lib/audio";
import { has } from "../lib/capabilities";
import { useStore } from "../lib/store";
import type { Model } from "../lib/types";

interface Msg {
  role: "user" | "assistant";
  content: string;
  images?: string[];
  audio?: string; // data URL of user voice input
  reasoning?: string;
  audioUrl?: string; // spoken reply
  meta?: string;
  error?: string;
}

interface Params {
  system: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
}

const DEFAULT_PARAMS: Params = { system: "", temperature: 0.7, top_p: 0.9, max_tokens: 2048 };

function loadChat(id: string): Msg[] {
  try {
    return JSON.parse(localStorage.getItem(`md-chat-${id}`) || "[]");
  } catch {
    return [];
  }
}

export function ChatPanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast } = useStore();
  const [msgs, setMsgs] = useState<Msg[]>(() => loadChat(model.id));
  const [input, setInput] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [voice, setVoice] = useState<string | null>(null);
  const [speech, setSpeech] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showParams, setShowParams] = useState(false);
  const [params, setParams] = useState<Params>(() => ({ ...DEFAULT_PARAMS, ...JSON.parse(localStorage.getItem(`md-chat-params-${model.id}`) || "{}") }));
  const abort = useRef<AbortController | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const rec = useRecorder();

  const canVision = has(model, "vision");
  const canAudioIn = has(model, "audio_input");
  const canSpeech = has(model, "speech_output");

  useEffect(() => {
    // don't persist large data URLs
    const slim = msgs.slice(-60).map((m) => ({ ...m, images: m.images?.length ? [] : undefined, audio: undefined }));
    localStorage.setItem(`md-chat-${model.id}`, JSON.stringify(slim));
  }, [msgs, model.id]);
  useEffect(() => localStorage.setItem(`md-chat-params-${model.id}`, JSON.stringify(params)), [params, model.id]);
  useEffect(() => {
    scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: busy ? "auto" : "smooth" });
  }, [msgs, busy]);
  useEffect(() => {
    const ta = taRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 220)}px`;
    }
  }, [input]);

  const addImages = async (files: FileList | File[]) => {
    const urls = await Promise.all([...files].filter((f) => f.type.startsWith("image/")).map(blobToDataUrl));
    setImages((prev) => [...prev, ...urls].slice(0, 6));
  };

  const send = async (text = input) => {
    if (busy || (!text.trim() && !images.length && !voice)) return;
    if (!ready) {
      toast("请先启动模型", "error");
      return;
    }
    const user: Msg = { role: "user", content: text.trim(), images: images.length ? images : undefined, audio: voice || undefined };
    const history = [...msgs, user];
    setMsgs([...history, { role: "assistant", content: "" }]);
    setInput("");
    setImages([]);
    setVoice(null);
    setBusy(true);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const patch = (fn: (m: Msg) => Msg) => setMsgs((list) => [...list.slice(0, -1), fn(list[list.length - 1])]);
    const t0 = performance.now();
    let chars = 0;
    try {
      await streamSSE(`/api/models/${model.id}/chat`, {
        messages: history.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content, images: m.images, audio: m.audio })),
        system: params.system || undefined,
        temperature: params.temperature,
        top_p: params.top_p,
        max_tokens: params.max_tokens,
        speech: speech && canSpeech,
      }, (ev) => {
        if (ev.type === "delta") {
          chars += ev.text.length;
          patch((m) => ({ ...m, content: m.content + ev.text }));
        } else if (ev.type === "reasoning") patch((m) => ({ ...m, reasoning: (m.reasoning || "") + ev.text }));
        else if (ev.type === "audio") patch((m) => ({ ...m, audioUrl: ev.url }));
        else if (ev.type === "notice") toast(ev.message, "info");
        else if (ev.type === "error") patch((m) => ({ ...m, error: ev.message }));
        else if (ev.type === "done") {
          const secs = (performance.now() - t0) / 1000;
          const tokens = ev.usage?.completion_tokens;
          patch((m) => ({ ...m, meta: `${secs.toFixed(1)} 秒${tokens ? ` · ${tokens} tokens · ${(tokens / secs).toFixed(1)} tok/s` : chars ? ` · ${chars} 字` : ""}` }));
        }
      }, ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") patch((m) => ({ ...m, error: e.message }));
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  const toggleRecord = async () => {
    if (rec.recording) {
      const wav = await rec.stop();
      if (wav) setVoice(await blobToDataUrl(wav));
    } else {
      try {
        await rec.start();
      } catch {
        toast("无法使用麦克风，请检查浏览器权限", "error");
      }
    }
  };

  const suggestions = [
    canVision ? "上传一张图片，描述画面里有什么" : "用三句话介绍一下你自己",
    "帮我写一封简洁得体的请假邮件",
    "解释一下 Transformer 的注意力机制",
    canSpeech ? "用语音给我讲一个小故事" : "把这段话翻译成英文：今天天气很好",
  ];

  return (
    <div className="chat" onDragOver={(e) => canVision && e.preventDefault()} onDrop={(e) => { if (canVision) { e.preventDefault(); addImages(e.dataTransfer.files); } }}>
      <div className="chat-scroll" ref={scroll}>
        {msgs.length === 0 ? (
          <div className="chat-hero">
            <div className="task-icon" style={{ margin: "0 auto", width: 48, height: 48, borderRadius: 14 }}><Brain size={22} /></div>
            <h2>和 {model.name} 对话</h2>
            <div className="muted">
              {[canVision && "支持看图", canAudioIn && "支持语音输入", canSpeech && "可以用语音回答"].filter(Boolean).join(" · ") || "文本对话"}
            </div>
            <div className="suggestions">
              {suggestions.map((s) => <button key={s} className="suggestion" onClick={() => (s.startsWith("上传") ? fileRef.current?.click() : send(s))}>{s}</button>)}
            </div>
          </div>
        ) : (
          <div className="chat-inner">
            {msgs.map((m, i) => {
              const last = i === msgs.length - 1;
              if (m.role === "user") {
                return (
                  <div key={i} className="msg user">
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", maxWidth: "78%" }}>
                      {m.images && m.images.length > 0 && <div className="msg-images">{m.images.map((src, j) => <img key={j} src={src} alt="" />)}</div>}
                      {m.audio && <audio controls src={m.audio} style={{ height: 34, marginBottom: 6 }} />}
                      {m.content && <div className="bubble">{m.content}</div>}
                    </div>
                  </div>
                );
              }
              const { think, answer, open } = splitThink(m.content);
              const reasoning = (m.reasoning || "") + (think ? (m.reasoning ? "\n" : "") + think : "");
              return (
                <div key={i} className="msg assistant">
                  <div className="bubble">
                    {reasoning && (
                      <details className="reasoning" open={busy && last && (open || !answer)}>
                        <summary><Brain size={13} /> 思考过程</summary>
                        <div>{reasoning}</div>
                      </details>
                    )}
                    {answer ? <Markdown text={answer} /> : !m.error && busy && last ? <span className="caret" /> : null}
                    {busy && last && answer && <span className="caret" style={{ marginLeft: 2 }} />}
                    {m.audioUrl && <audio controls autoPlay src={m.audioUrl} style={{ marginTop: 12, height: 36, width: "100%", maxWidth: 420 }} />}
                    {m.error && <div className="banner error" style={{ marginTop: 8 }}>{m.error}</div>}
                    {m.meta && <div className="msg-meta">{m.meta}</div>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="composer-wrap">
        <div className="composer">
          {(images.length > 0 || voice) && (
            <div className="attach-row">
              {images.map((src, i) => (
                <div className="attach" key={i}>
                  <img src={src} alt="" />
                  <button className="x" onClick={() => setImages(images.filter((_, j) => j !== i))}><X size={11} /></button>
                </div>
              ))}
              {voice && (
                <div className="attach-audio"><Mic size={14} /> 语音已录制 <button className="btn ghost icon sm" onClick={() => setVoice(null)}><X size={13} /></button></div>
              )}
            </div>
          )}
          <textarea
            ref={taRef}
            rows={1}
            value={input}
            placeholder={ready ? `给 ${model.name} 发消息…` : "启动模型后即可开始对话"}
            onChange={(e) => setInput(e.target.value)}
            onPaste={(e) => canVision && e.clipboardData.files.length && addImages(e.clipboardData.files)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="composer-bar">
            {canVision && (
              <button className="toggle-chip" onClick={() => fileRef.current?.click()} title="添加图片（也可以粘贴或拖入）">
                <ImagePlus size={15} /> 图片
              </button>
            )}
            {canAudioIn && (
              <button className={`toggle-chip ${rec.recording ? "recording" : ""}`} onClick={toggleRecord}>
                {rec.recording ? <><span className="wave"><i /><i style={{ animationDelay: ".15s" }} /><i style={{ animationDelay: ".3s" }} /></span> {rec.seconds}s 停止</> : <><Mic size={15} /> 语音</>}
              </button>
            )}
            {canSpeech && (
              <button className={`toggle-chip ${speech ? "on" : ""}`} onClick={() => setSpeech(!speech)} title="让模型用语音回答">
                <Volume2 size={15} /> 语音回答
              </button>
            )}
            <button className={`toggle-chip ${showParams ? "on" : ""}`} onClick={() => setShowParams(!showParams)}>
              <SlidersHorizontal size={15} /> 参数
            </button>
            {msgs.length > 0 && (
              <button className="toggle-chip" onClick={() => { if (!busy) setMsgs([]); }} title="清空对话"><Trash2 size={15} /></button>
            )}
            <span className="spacer" />
            {busy ? (
              <button className="btn dark icon" style={{ borderRadius: 12 }} onClick={() => abort.current?.abort()}><Square size={14} fill="currentColor" /></button>
            ) : (
              <button className="btn primary icon" style={{ borderRadius: 12 }} disabled={!input.trim() && !images.length && !voice} onClick={() => send()}>
                <ArrowUp size={17} />
              </button>
            )}
          </div>
        </div>
        <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => e.target.files && addImages(e.target.files)} />
      </div>

      {showParams && (
        <div className="drawer">
          <div className="row" style={{ marginBottom: 24 }}>
            <strong style={{ flex: 1, fontSize: 15 }}>对话参数</strong>
            <button className="btn ghost icon sm" onClick={() => setShowParams(false)}><X size={15} /></button>
          </div>
          <div className="stack" style={{ gap: 24 }}>
            <Field label="系统提示词" hint="设定模型的角色和回答风格">
              <textarea className="textarea" rows={5} value={params.system} placeholder="你是一个乐于助人的助手。" onChange={(e) => setParams({ ...params, system: e.target.value })} />
            </Field>
            <Slider label="温度（创造性）" value={params.temperature} min={0} max={2} step={0.05} onChange={(v) => setParams({ ...params, temperature: v })} />
            <Slider label="Top P" value={params.top_p} min={0.05} max={1} step={0.05} onChange={(v) => setParams({ ...params, top_p: v })} />
            <Slider label="最大回复长度" value={params.max_tokens} min={128} max={32768} step={128} onChange={(v) => setParams({ ...params, max_tokens: v })} />
            <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => setParams(DEFAULT_PARAMS)}>恢复默认</button>
          </div>
        </div>
      )}
    </div>
  );
}

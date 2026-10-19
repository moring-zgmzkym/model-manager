import { AudioLines, Download, ImagePlus, Loader2, Mic, Send, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "../components/Markdown";
import { api, streamSSE } from "../lib/api";
import { blobToDataUrl, useRecorder } from "../lib/audio";
import { useStore } from "../lib/store";
import type { Model } from "../lib/types";

interface Msg {
  role: "user" | "assistant";
  content: string;
  image?: string;
  audioIn?: string;
  asr?: string;
  audioOut?: string;
  error?: string;
  meta?: string;
}

const SUGGESTIONS = [
  "用三句话介绍一下你自己",
  "把这段话改写得更适合短剧旁白：雨夜，主角推开旧书店的门",
  "我发一张图，你帮我描述画面并给三个镜头建议",
];

function loadMsgs(id: string): Msg[] {
  try {
    return JSON.parse(localStorage.getItem("md-omni-" + id) || "[]");
  } catch {
    return [];
  }
}

function loadSession(id: string): Msg[] {
  try {
    const raw = sessionStorage.getItem("md-omni-session-" + id);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function loadDraft(id: string): { text?: string; image?: string; audioIn?: string; voice?: string } {
  try {
    return JSON.parse(sessionStorage.getItem("md-omni-draft-" + id) || "{}");
  } catch {
    return {};
  }
}

export function OmniPanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast } = useStore();
  const initialDraft = loadDraft(model.id);
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    const session = loadSession(model.id);
    return session.length ? session : loadMsgs(model.id);
  });
  const [text, setText] = useState(initialDraft.text || "");
  const [image, setImage] = useState<string>(initialDraft.image || "");
  const [audioIn, setAudioIn] = useState<string>(initialDraft.audioIn || "");
  const [voice, setVoice] = useState(initialDraft.voice || "default");
  const [voices, setVoices] = useState<string[]>(["default"]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const abort = useRef<AbortController | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLInputElement>(null);
  const rec = useRecorder();

  useEffect(() => {
    const slim = msgs.slice(-40).map((m) => ({ ...m, image: undefined, audioIn: undefined, audioOut: undefined }));
    localStorage.setItem("md-omni-" + model.id, JSON.stringify(slim));
    try {
      sessionStorage.setItem("md-omni-session-" + model.id, JSON.stringify(msgs.slice(-20)));
    } catch {
      try {
        const textOnly = msgs.slice(-20).map((m) => ({ ...m, image: undefined, audioIn: undefined, audioOut: undefined }));
        sessionStorage.setItem("md-omni-session-" + model.id, JSON.stringify(textOnly));
      } catch {
        /* ignore quota errors */
      }
    }
  }, [msgs, model.id]);

  useEffect(() => {
    try {
      sessionStorage.setItem("md-omni-draft-" + model.id, JSON.stringify({ text, image, audioIn, voice }));
    } catch {
      /* ignore quota errors */
    }
  }, [text, image, audioIn, voice, model.id]);

  useEffect(() => {
    scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: busy ? "auto" : "smooth" });
  }, [msgs, busy]);

  useEffect(() => {
    if (!ready) return;
    api.get<{ voices: string[] }>("/api/models/" + model.id + "/omni/voices")
      .then((r) => setVoices(r.voices.length ? r.voices : ["default"]))
      .catch(() => {});
  }, [model.id, ready]);

  const addImage = async (file?: File) => {
    if (!file || !file.type.startsWith("image/")) return;
    setImage(await blobToDataUrl(file));
  };

  const addAudio = async (file?: File) => {
    if (!file) return;
    setAudioIn(await blobToDataUrl(file));
  };

  const toggleRecord = async () => {
    if (rec.recording) {
      const wav = await rec.stop();
      if (wav) setAudioIn(await blobToDataUrl(wav));
    } else {
      try {
        await rec.start();
      } catch {
        toast("无法使用麦克风，请检查浏览器权限", "error");
      }
    }
  };

  const send = async (message = text) => {
    if (busy) return;
    if (!message.trim() && !image && !audioIn) return;
    if (!ready) return toast("请先启动模型", "error");
    const user: Msg = {
      role: "user",
      content: message.trim(),
      image: image || undefined,
      audioIn: audioIn || undefined,
    };
    const history = msgs.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content }));
    setMsgs([...msgs, user, { role: "assistant", content: "" }]);
    setText("");
    setImage("");
    setAudioIn("");
    setBusy(true);
    setNote("");
    const ctrl = new AbortController();
    abort.current = ctrl;
    const patch = (fn: (m: Msg) => Msg) => setMsgs((list) => [...list.slice(0, -1), fn(list[list.length - 1])]);
    const started = performance.now();
    try {
      await streamSSE("/api/models/" + model.id + "/omni/chat", {
        text: message.trim(),
        image: user.image,
        audio: user.audioIn,
        voice,
        history,
        temperature: 0.7,
        max_tokens: 512,
      }, (ev) => {
        if (ev.type === "delta") patch((m) => ({ ...m, content: m.content + ev.text }));
        else if (ev.type === "asr") patch((m) => ({ ...m, asr: ev.text }));
        else if (ev.type === "audio") patch((m) => ({ ...m, audioOut: "data:audio/wav;base64," + ev.data }));
        else if (ev.type === "status") setNote(ev.message || "");
        else if (ev.type === "error") patch((m) => ({ ...m, error: ev.message }));
        else if (ev.type === "done") {
          const secs = ((performance.now() - started) / 1000).toFixed(1);
          patch((m) => ({ ...m, meta: secs + " 秒" }));
        }
      }, ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") patch((m) => ({ ...m, error: e.message }));
    } finally {
      setBusy(false);
      setNote("");
      abort.current = null;
    }
  };

  const clear = () => {
    if (busy) return;
    setMsgs([]);
  };

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scroll}>
        {msgs.length === 0 ? (
          <div className="chat-hero">
            <div className="task-icon" style={{ margin: "0 auto", width: 48, height: 48, borderRadius: 14 }}>
              <AudioLines size={22} />
            </div>
            <h2>和 {model.name} 对话</h2>
            <div className="muted">文字 / 图片 / 语音都可以，回答会同时生成文字和语音。</div>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="suggestion" onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          </div>
        ) : (
          <div className="chat-inner">
            {msgs.map((m, i) => {
              if (m.role === "user") {
                return (
                  <div key={i} className="msg user">
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", maxWidth: "78%" }}>
                      {m.image && <div className="msg-images"><img src={m.image} alt="" /></div>}
                      {m.audioIn && <audio controls src={m.audioIn} style={{ height: 34, marginBottom: 6 }} />}
                      {m.content && <div className="bubble">{m.content}</div>}
                      {m.asr && <div className="msg-meta">识别：{m.asr}</div>}
                    </div>
                  </div>
                );
              }
              return (
                <div key={i} className="msg assistant">
                  <div className="bubble">
                    {m.content ? <Markdown text={m.content} /> : !m.error && busy ? <span className="caret" /> : null}
                    {m.audioOut && (
                      <div className="row" style={{ marginTop: 12, gap: 8 }}>
                        <audio controls autoPlay src={m.audioOut} style={{ height: 36, flex: 1, maxWidth: 420 }} />
                        <a className="btn ghost icon sm" href={m.audioOut} download="minimind-reply.wav" title="下载语音">
                          <Download size={14} />
                        </a>
                      </div>
                    )}
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
          {(image || audioIn) && (
            <div className="attach-row">
              {image && (
                <div className="attach">
                  <img src={image} alt="" />
                  <button className="x" onClick={() => setImage("")}><X size={11} /></button>
                </div>
              )}
              {audioIn && (
                <div className="attach-audio">
                  <Mic size={14} /> 语音已准备
                  <button className="btn ghost icon sm" onClick={() => setAudioIn("")}><X size={13} /></button>
                </div>
              )}
            </div>
          )}
          <textarea
            rows={1}
            value={text}
            placeholder={ready ? "输入文字，或上传图片 / 录音后发送…" : "启动模型后即可使用"}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="composer-bar">
            <button className="toggle-chip" onClick={() => fileRef.current?.click()}>
              <ImagePlus size={15} /> 图片
            </button>
            <button className="toggle-chip" onClick={() => audioRef.current?.click()}>
              <AudioLines size={15} /> 音频
            </button>
            <button className={"toggle-chip " + (rec.recording ? "recording" : "")} onClick={toggleRecord}>
              {rec.recording ? <><span className="wave"><i /><i style={{ animationDelay: ".15s" }} /><i style={{ animationDelay: ".3s" }} /></span> {rec.seconds}s 停止</> : <><Mic size={15} /> 录音</>}
            </button>
            <select className="select" style={{ height: 34, width: 132 }} value={voice} onChange={(e) => setVoice(e.target.value)} title="音色">
              {voices.map((v) => <option key={v} value={v}>{v === "default" ? "默认音色" : v}</option>)}
            </select>
            {msgs.length > 0 && (
              <button className="toggle-chip" onClick={clear} title="清空对话"><Trash2 size={15} /></button>
            )}
            <span className="spacer" />
            {busy ? (
              <button className="btn dark icon" style={{ borderRadius: 12 }} onClick={() => abort.current?.abort()}>
                <Loader2 size={14} className="spin" />
              </button>
            ) : (
              <button className="btn primary icon" style={{ borderRadius: 12 }} disabled={!ready || (!text.trim() && !image && !audioIn)} onClick={() => send()}>
                <Send size={16} />
              </button>
            )}
          </div>
        </div>
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => addImage(e.target.files?.[0])} />
        <input ref={audioRef} type="file" accept="audio/*" hidden onChange={(e) => addAudio(e.target.files?.[0])} />
        {note && <div className="faint small" style={{ marginTop: 8, textAlign: "center" }}>{note}</div>}
      </div>
    </div>
  );
}

import { ArrowUp, Brain, FileText, ImagePlus, Mic, SlidersHorizontal, Square, Trash2, Volume2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Markdown, splitThink } from "../components/Markdown";
import { Field, Slider, Switch } from "../components/ui";
import { api, extractDocument, streamSSE } from "../lib/api";
import { blobToDataUrl, useRecorder } from "../lib/audio";
import { has } from "../lib/capabilities";
import { useStore } from "../lib/store";
import type { Model } from "../lib/types";

interface Msg {
  role: "user" | "assistant";
  content: string;
  images?: string[];
  files?: { name: string; text: string }[];
  audio?: string; // data URL of user voice input
  reasoning?: string;
  audioUrl?: string; // spoken reply
  meta?: string;
  error?: string;
  jobId?: string; // links a streaming reply to its backend job after a reload
}

interface Params {
  system: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  think: boolean;
}

interface ActiveJob {
  id: string;
  index: number;
  started: number;
}

interface JobSnapshot {
  id: string;
  status: "running" | "done" | "error" | "cancelled";
  content: string;
  reasoning: string;
  error: string;
  elapsed: number;
  usage?: { completion_tokens?: number };
}

const DEFAULT_PARAMS: Params = { system: "", temperature: 0.7, top_p: 0.9, max_tokens: 4096, think: false };

function newJobId() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function jobMeta(snap: JobSnapshot, started: number) {
  if (snap.status === "cancelled") return "已停止";
  const secs = snap.elapsed || Math.max(0, (Date.now() - started) / 1000);
  const tokens = snap.usage?.completion_tokens;
  const chars = snap.content?.length || 0;
  const rate = tokens && secs ? ` · ${(tokens / secs).toFixed(1)} tok/s` : "";
  return `${secs.toFixed(1)} 秒${tokens ? ` · ${tokens} tokens${rate}` : chars ? ` · ${chars} 字` : ""}`;
}

/** Replace the assistant message after a reload/navigation with the server snapshot. */
function applyJob(list: Msg[], index: number, snap: JobSnapshot, started: number): Msg[] {
  const copy = [...list];
  // Prefer the message tagged with this job: the stored history is trimmed to
  // the last 20 messages, so the absolute index can point past the end.
  let idx = copy.findIndex((m) => m.role === "assistant" && m.jobId === snap.id);
  if (idx < 0) {
    idx = index;
    if (idx < 0 || idx > copy.length || (idx < copy.length && copy[idx].role !== "assistant")) {
      idx = copy.length;
    }
    if (idx === copy.length) copy.push({ role: "assistant", content: "", jobId: snap.id });
  }
  const meta = jobMeta(snap, started);
  copy[idx] = {
    ...copy[idx],
    content: snap.content || "",
    reasoning: snap.reasoning || "",
    error: snap.error || undefined,
    meta: snap.status === "done" && !snap.content && snap.reasoning
      ? `思考占满输出额度，请调大“最大输出长度” · ${meta}`
      : meta,
  };
  return copy;
}

function loadChat(id: string): Msg[] {
  try {
    return JSON.parse(localStorage.getItem("md-chat-" + id) || "[]");
  } catch {
    return [];
  }
}

/** Same-tab session copy keeps images/audio across a refresh (localStorage copy is text-only). */
function loadSession(id: string): Msg[] {
  try {
    const raw = sessionStorage.getItem("md-chat-session-" + id);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function loadDraft(id: string): { text?: string; images?: string[]; voice?: string | null; files?: { name: string; text: string }[] } {
  try {
    return JSON.parse(sessionStorage.getItem("md-chat-draft-" + id) || "{}");
  } catch {
    return {};
  }
}

function expandContent(m: Msg): string {
  if (!m.files || !m.files.length) return m.content;
  const blocks = m.files.map((f) => "文件：" + f.name + "\n" + f.text);
  return [m.content, ...blocks].filter(Boolean).join("\n\n");
}

function loadActiveJob(id: string): ActiveJob | null {
  try {
    const raw = sessionStorage.getItem("md-chat-job-" + id);
    if (!raw) return null;
    const job = JSON.parse(raw) as ActiveJob;
    if (!job?.id || !Number.isFinite(job.started) || job.started <= 0 || Date.now() - job.started > 30 * 60 * 1000) {
      sessionStorage.removeItem("md-chat-job-" + id);
      return null;
    }
    return job;
  } catch {
    return null;
  }
}

export function ChatPanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast } = useStore();
  const initialDraft = loadDraft(model.id);
  const draftTextKey = "md-chat-draft-text-" + model.id;
  const jobKey = "md-chat-job-" + model.id;
  const [initialJob] = useState<ActiveJob | null>(() => loadActiveJob(model.id));
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    const session = loadSession(model.id);
    return session.length ? session : loadChat(model.id);
  });
  const [input, setInput] = useState(initialDraft.text || localStorage.getItem(draftTextKey) || "");
  const [images, setImages] = useState<string[]>(initialDraft.images || []);
  const [files, setFiles] = useState<{ name: string; text: string }[]>(initialDraft.files || []);
  const [voice, setVoice] = useState<string | null>(initialDraft.voice || null);
  const [speech, setSpeech] = useState(false);
  const [busy, setBusy] = useState(!!initialJob);
  const [showParams, setShowParams] = useState(false);
  const [params, setParams] = useState<Params>(() => ({ ...DEFAULT_PARAMS, ...JSON.parse(localStorage.getItem(`md-chat-params-${model.id}`) || "{}") }));
  const abort = useRef<AbortController | null>(null);
  const activeJob = useRef<ActiveJob | null>(initialJob);
  const mounted = useRef(true);
  const scroll = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const docRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const rec = useRecorder();

  const canVision = has(model, "vision");
  const canAudioIn = has(model, "audio_input");
  const canSpeech = has(model, "speech_output");

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    // localStorage keeps a text-only history; sessionStorage keeps the current tab intact after refresh.
    const slim = msgs.slice(-60).map((m) => ({ ...m, images: m.images?.length ? [] : undefined, files: undefined, audio: undefined }));
    localStorage.setItem("md-chat-" + model.id, JSON.stringify(slim));
    try {
      sessionStorage.setItem("md-chat-session-" + model.id, JSON.stringify(msgs.slice(-20)));
    } catch {
      try {
        const textOnly = msgs.slice(-20).map((m) => ({ ...m, images: undefined, files: undefined, audio: undefined }));
        sessionStorage.setItem("md-chat-session-" + model.id, JSON.stringify(textOnly));
      } catch {
        /* quota exhausted; localStorage still holds the text history */
      }
    }
  }, [msgs, model.id]);

  useEffect(() => {
    try {
      sessionStorage.setItem("md-chat-draft-" + model.id, JSON.stringify({ text: input, images, voice, files }));
    } catch {
      try {
        sessionStorage.setItem("md-chat-draft-" + model.id, JSON.stringify({ text: input, images: [], voice, files: [] }));
      } catch {
        /* ignore quota errors */
      }
    }
  }, [input, images, voice, files, model.id]);
  useEffect(() => localStorage.setItem(draftTextKey, input), [input, draftTextKey]);
  useEffect(() => localStorage.setItem(`md-chat-params-${model.id}`, JSON.stringify(params)), [params, model.id]);

  // If the page was reloaded or the user visited the log page while a reply was
  // streaming, the backend job is still alive. Poll its snapshot and fill the
  // assistant bubble back in instead of losing the answer.
  useEffect(() => {
    if (!initialJob) return;
    let alive = true;
    let timer: number | undefined;
    setBusy(true);
    const poll = async () => {
      try {
        const snap = await api.get<JobSnapshot>(`/api/chat/jobs/${encodeURIComponent(initialJob.id)}`);
        if (!alive) return;
        setMsgs((list) => applyJob(list, initialJob.index, snap, initialJob.started));
        if (snap.status === "running") {
          timer = window.setTimeout(poll, 700);
        } else {
          setBusy(false);
          activeJob.current = null;
          sessionStorage.removeItem(jobKey);
        }
      } catch {
        if (!alive) return;
        setBusy(false);
        activeJob.current = null;
        sessionStorage.removeItem(jobKey);
      }
    };
    poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [initialJob, jobKey]);

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

  const addImages = async (list: FileList | File[]) => {
    const urls = await Promise.all([...list].filter((f) => f.type.startsWith("image/")).map(blobToDataUrl));
    setImages((prev) => [...prev, ...urls].slice(0, 6));
  };

  const addFiles = async (list: FileList | File[]) => {
    const picked = [...list].filter((f) => !f.type.startsWith("image/")).slice(0, 5);
    for (const file of picked) {
      try {
        const doc = await extractDocument(file);
        setFiles((prev) => [...prev, { name: doc.name, text: doc.text }].slice(0, 5));
        if (doc.truncated) toast(doc.name + " 较长，已截断到 20 万字符", "info");
      } catch (e: any) {
        toast(e.message, "error");
      }
    }
  };

  const send = async (text = input) => {
    if (busy || (!text.trim() && !images.length && !voice && !files.length)) return;
    if (!ready) {
      toast("请先启动模型", "error");
      return;
    }
    const user: Msg = {
      role: "user",
      content: text.trim(),
      images: images.length ? images : undefined,
      files: files.length ? files : undefined,
      audio: voice || undefined,
    };
    const history = [...msgs, user];
    const job: ActiveJob = { id: newJobId(), index: history.length, started: Date.now() };
    setMsgs([...history, { role: "assistant", content: "", jobId: job.id }]);
    setInput("");
    setImages([]);
    setFiles([]);
    setVoice(null);
    setBusy(true);
    const ctrl = new AbortController();
    abort.current = ctrl;
    activeJob.current = job;
    try {
      sessionStorage.setItem(jobKey, JSON.stringify(job));
    } catch {
      /* sessionStorage can be full; the stream still works for this page */
    }
    const patch = (fn: (m: Msg) => Msg) => setMsgs((list) => [...list.slice(0, -1), fn(list[list.length - 1])]);
    const t0 = performance.now();
    let chars = 0;
    try {
      await streamSSE(`/api/models/${model.id}/chat`, {
        request_id: job.id,
        messages: history.filter((m) => !m.error).map((m) => ({
          role: m.role, content: expandContent(m), images: m.images, audio: m.audio,
        })),
        system: params.system || undefined,
        temperature: params.temperature,
        top_p: params.top_p,
        max_tokens: params.max_tokens,
        think: params.think,
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
          patch((m) => ({
            ...m,
            meta: (m.error || m.content ? "" : "思考占满输出额度，请调大“最大输出长度” · ")
              + `${secs.toFixed(1)} 秒${tokens ? ` · ${tokens} tokens · ${(tokens / secs).toFixed(1)} tok/s` : chars ? ` · ${chars} 字` : ""}`,
          }));
          if (mounted.current) {
            activeJob.current = null;
            sessionStorage.removeItem(jobKey);
          }
        }
      }, ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") {
        patch((m) => ({ ...m, error: e.message }));
        if (mounted.current && activeJob.current?.id === job.id) {
          activeJob.current = null;
          sessionStorage.removeItem(jobKey);
        }
      }
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  const stop = () => {
    const job = activeJob.current;
    if (job) {
      api.post(`/api/chat/jobs/${encodeURIComponent(job.id)}/cancel`).catch(() => {});
      activeJob.current = null;
      sessionStorage.removeItem(jobKey);
    }
    abort.current?.abort();
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
    <div
      className="chat"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const dropped = [...e.dataTransfer.files];
        const pickedImages = dropped.filter((f) => f.type.startsWith("image/"));
        const pickedDocs = dropped.filter((f) => !f.type.startsWith("image/"));
        if (pickedImages.length && canVision) addImages(pickedImages);
        if (pickedDocs.length) addFiles(pickedDocs);
      }}
    >
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
                      {m.files && m.files.length > 0 && (
                        <div className="row wrap" style={{ justifyContent: "flex-end", gap: 6, marginBottom: 6 }}>
                          {m.files.map((f, j) => (
                            <span key={j} className="chip" title={f.name}><FileText size={12} /> {f.name}</span>
                          ))}
                        </div>
                      )}
                      {m.audio && <audio controls src={m.audio} style={{ height: 34, marginBottom: 6 }} />}
                      {m.content && <div className="bubble">{m.content}</div>}
                    </div>
                  </div>
                );
              }
              const { think, answer } = splitThink(m.content);
              const reasoning = (m.reasoning || "") + (think ? (m.reasoning ? "\n" : "") + think : "");
              return (
                <div key={i} className="msg assistant">
                  <div className="bubble">
                    {reasoning && (
                      <details className="reasoning">
                        <summary><Brain size={13} /> 思考过程 · {reasoning.length} 字</summary>
                        <div>{reasoning}</div>
                      </details>
                    )}
                    {answer ? <Markdown text={answer} /> : !m.error && busy && last ? (
                      <div className="row" style={{ gap: 8, color: "var(--text-3)" }}>
                        <span className="caret" />
                        <span className="small">{reasoning ? "思考中…" : "生成中…"}</span>
                      </div>
                    ) : null}
                    {busy && last && answer && <span className="caret" style={{ marginLeft: 2 }} />}
                    {m.audioUrl && <audio controls autoPlay src={m.audioUrl} style={{ marginTop: 12, height: 36, width: "100%", maxWidth: 420 }} />}
                    {m.error && (
                      <div className="banner error" style={{ marginTop: 8, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                        {m.error}
                      </div>
                    )}
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
          {(images.length > 0 || voice || files.length > 0) && (
            <div className="attach-row">
              {images.map((src, i) => (
                <div className="attach" key={i}>
                  <img src={src} alt="" />
                  <button className="x" onClick={() => setImages(images.filter((_, j) => j !== i))}><X size={11} /></button>
                </div>
              ))}
              {files.map((f, i) => (
                <div className="attach-audio" key={f.name + i} style={{ maxWidth: 260 }}>
                  <FileText size={14} />
                  <span className="ellipsis" style={{ flex: 1 }} title={f.name}>{f.name}</span>
                  <button className="btn ghost icon sm" onClick={() => setFiles(files.filter((_, j) => j !== i))}><X size={12} /></button>
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
            placeholder={ready ? `给 ${model.name} 发消息，或添加图片 / 文件…` : "启动模型后即可开始对话"}
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
            <button className="toggle-chip" onClick={() => docRef.current?.click()} title="添加文档：txt / md / pdf / docx 等">
              <FileText size={15} /> 文件
            </button>
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
            {model.adapter === "ollama" && (
              <button className={`toggle-chip ${params.think ? "on" : ""}`} onClick={() => setParams({ ...params, think: !params.think })}
                title={params.think ? "已开启思考；点击后直接回答" : "开启后模型先思考再回答（更慢，思考占用输出额度）"}>
                <Brain size={15} /> 思考
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
              <button className="btn dark icon" style={{ borderRadius: 12 }} onClick={stop} title="停止生成"><Square size={14} fill="currentColor" /></button>
            ) : (
              <button className="btn primary icon" style={{ borderRadius: 12 }} disabled={!input.trim() && !images.length && !voice && !files.length} onClick={() => send()}>
                <ArrowUp size={17} />
              </button>
            )}
          </div>
        </div>
        <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => {
          if (e.target.files) addImages(e.target.files);
          e.target.value = "";
        }} />
        <input ref={docRef} type="file" multiple hidden
          accept=".txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.html,.htm,.css,.log,.ini,.toml,.xml,.sql,.sh,.ps1,.bat,.pdf,.docx"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }} />
      </div>

      {showParams && (
        <div className="drawer">
          <div className="row" style={{ marginBottom: 24 }}>
            <strong style={{ flex: 1, fontSize: 15 }}>对话参数</strong>
            <button className="btn ghost icon sm" onClick={() => setShowParams(false)}><X size={15} /></button>
          </div>
          <div className="stack" style={{ gap: 24 }}>
            <Field label="系统提示词" hint="设定模型的角色和回答风格，每轮都会发送。">
              <textarea className="textarea" rows={5} value={params.system} placeholder="你是一个乐于助人的助手。" onChange={(e) => setParams({ ...params, system: e.target.value })} />
            </Field>
            <Slider label="温度（随机性）" value={params.temperature} min={0} max={2} step={0.05}
              hint="越高越发散、越有创造性；它不是思考强度。想更稳定就调低。"
              onChange={(v) => setParams({ ...params, temperature: v })} />
            <Slider label="Top P（采样范围）" value={params.top_p} min={0.05} max={1} step={0.05}
              hint="通常保持 0.9；调低会让用词更保守。"
              onChange={(v) => setParams({ ...params, top_p: v })} />
            <Slider label="最大输出长度（含思考）" value={params.max_tokens} min={128} max={32768} step={128}
              hint="思考过程也占用这个额度；思考被截断时调大它。"
              onChange={(v) => setParams({ ...params, max_tokens: v })} />
            {model.adapter === "ollama" && (
              <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
                <Switch checked={params.think} onChange={(v) => setParams({ ...params, think: v })} />
                <div>
                  <div style={{ fontWeight: 560 }}>启用思考过程</div>
                  <div className="faint small">
                    默认关闭，模型直接回答。开启后会先输出思考链（折叠在“思考过程”里），
                    思考也占用“最大输出长度”，长任务会明显更慢。
                  </div>
                </div>
              </div>
            )}
            {model.adapter === "ollama" && (
              <div className="banner" style={{ alignItems: "flex-start" }}>
                <div className="small">
                  服务端上下文约 {model.defaults.num_ctx || 8192} tokens。每次发送都会重发本对话历史，
                  超出后最早的内容会被截断；换话题时建议点“清空对话”。
                </div>
              </div>
            )}
            <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => setParams(DEFAULT_PARAMS)}>恢复默认</button>
          </div>
        </div>
      )}
    </div>
  );
}

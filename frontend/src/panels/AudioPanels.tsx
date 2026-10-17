import { AudioLines, Copy, Loader2, Mic, Plus, Square, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Field, Slider } from "../components/ui";
import { api, upload } from "../lib/api";
import { toWav, useRecorder } from "../lib/audio";
import { enabledCaps } from "../lib/capabilities";
import { timeAgo } from "../lib/format";
import { useStore } from "../lib/store";
import type { Model, OutputItem } from "../lib/types";

interface Voice {
  name: string;
  ref: string; // upload id or absolute path
  prompt_text: string;
  prompt_lang: string;
}

const LANGS = [
  ["zh", "中文"], ["en", "英文"], ["ja", "日文"], ["ko", "韩文"], ["yue", "粤语"], ["auto", "自动（多语种混合）"],
];

/** Upload-or-record a short audio clip; returns an upload id. */
function AudioInput({ value, onChange, hint }: { value: { id: string; url: string; name: string } | null; onChange: (v: { id: string; url: string; name: string } | null) => void; hint?: string }) {
  const { toast } = useStore();
  const rec = useRecorder();
  const ref = useRef<HTMLInputElement>(null);
  const setBlob = async (blob: Blob, name: string) => {
    const wav = await toWav(blob).catch(() => blob);
    const up = await upload(wav, name.replace(/\.\w+$/, "") + ".wav");
    onChange({ id: up.id, url: up.url, name });
  };
  return (
    <div className="stack" style={{ gap: 10 }}>
      {value ? (
        <div className="audio-row" style={{ padding: "8px 12px" }}>
          <audio controls src={value.url} />
          <button className="btn ghost icon sm" onClick={() => onChange(null)}><Trash2 size={14} /></button>
        </div>
      ) : (
        <div className="row">
          <button className="btn" onClick={() => ref.current?.click()}><Upload size={15} /> 上传音频</button>
          <button className={`btn ${rec.recording ? "danger" : ""}`} onClick={async () => {
            if (rec.recording) {
              const wav = await rec.stop();
              if (wav) setBlob(wav, `录音-${Date.now()}.wav`);
            } else {
              rec.start().catch(() => toast("无法使用麦克风", "error"));
            }
          }}>
            {rec.recording ? <><Square size={13} fill="currentColor" /> 停止 {rec.seconds}s</> : <><Mic size={15} /> 录一段</>}
          </button>
          {hint && <span className="faint small">{hint}</span>}
        </div>
      )}
      <input ref={ref} type="file" accept="audio/*" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) setBlob(f, f.name); e.target.value = ""; }} />
    </div>
  );
}

function OutputList({ items, onDelete }: { items: OutputItem[]; onDelete: (id: string) => void }) {
  if (!items.length) return <div className="empty" style={{ padding: "64px 0" }}><p className="faint">合成的音频会显示在这里，并自动保存到作品库。</p></div>;
  return (
    <div className="stack" style={{ gap: 10 }}>
      {items.map((o) => (
        <div key={o.id} className="audio-row">
          <div className="txt">
            <div className="ellipsis" title={o.prompt}>{o.prompt}</div>
            <div className="faint small">{o.meta?.voice ? `${o.meta.voice} · ` : ""}{timeAgo(o.created_at)}</div>
          </div>
          <audio controls src={o.url} preload="none" />
          <button className="btn ghost icon sm" onClick={() => onDelete(o.id)}><Trash2 size={14} /></button>
        </div>
      ))}
    </div>
  );
}

export function TtsPanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast, refresh } = useStore();
  const caps = enabledCaps(model);
  const sovits = caps.some((c) => (c.type === "tts" || c.type === "voice_clone") && c.binding.protocol === "gpt_sovits");
  const cloneCap = caps.some((c) => c.type === "voice_clone");
  const [text, setText] = useState("");
  const [lang, setLang] = useState("zh");
  const [speed, setSpeed] = useState(1);
  const [voiceName, setVoiceName] = useState("alloy");
  const [instructions, setInstructions] = useState("");
  const [ref, setRef] = useState<{ id: string; url: string; name: string } | null>(null);
  const [promptText, setPromptText] = useState("");
  const [promptLang, setPromptLang] = useState("zh");
  const [topK, setTopK] = useState(15);
  const [temperature, setTemperature] = useState(1);
  const [weights, setWeights] = useState<{ gpt: string[]; sovits: string[] }>({ gpt: [], sovits: [] });
  const [gptW, setGptW] = useState("");
  const [sovitsW, setSovitsW] = useState("");
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState<OutputItem[]>([]);
  const voices: Voice[] = model.defaults.voices || [];
  const [activeVoice, setActiveVoice] = useState<string>("");

  const load = () => api.get<OutputItem[]>(`/api/outputs?kind=audio&model_id=${model.id}&limit=40`).then(setItems).catch(() => {});
  useEffect(() => {
    load();
    if (sovits) api.get<typeof weights>(`/api/models/${model.id}/sovits/weights`).then(setWeights).catch(() => {});
  }, [model.id]); // eslint-disable-line

  const pickVoice = (v: Voice) => {
    setActiveVoice(v.name);
    setRef({ id: v.ref, url: v.ref.startsWith("/api/") ? v.ref : `/api/files/uploads/${v.ref}`, name: v.name });
    setPromptText(v.prompt_text);
    setPromptLang(v.prompt_lang);
  };

  const saveVoice = async () => {
    if (!ref) return;
    const name = prompt("给这个音色起个名字", ref.name.replace(/\.\w+$/, ""));
    if (!name) return;
    const v: Voice = { name, ref: ref.url, prompt_text: promptText, prompt_lang: promptLang };
    const next = { ...model, defaults: { ...model.defaults, voices: [...voices.filter((x) => x.name !== name), v] } };
    await api.put(`/api/models/${model.id}`, next);
    await refresh();
    setActiveVoice(name);
    toast("音色已保存", "success");
  };

  const deleteVoice = async (name: string) => {
    const next = { ...model, defaults: { ...model.defaults, voices: voices.filter((x) => x.name !== name) } };
    await api.put(`/api/models/${model.id}`, next);
    await refresh();
  };

  const switchWeights = async () => {
    try {
      await api.post(`/api/models/${model.id}/sovits/weights`, { gpt: gptW, sovits: sovitsW });
      toast("已切换模型权重", "success");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const run = async () => {
    if (!text.trim()) return toast("请输入要朗读的文字", "error");
    if (!ready) return toast("请先启动模型", "error");
    setBusy(true);
    try {
      await api.post(`/api/models/${model.id}/tts`, sovits ? {
        text, text_lang: lang, ref_audio: ref?.url, prompt_text: promptText, prompt_lang: promptLang, speed,
        top_k: topK, temperature, voice_name: activeVoice || ref?.name,
      } : { text, voice: voiceName, speed, instructions: instructions || undefined, ref_audio: cloneCap ? ref?.url : undefined });
      await load();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" style={{ maxWidth: 1200 }}>
      <div className="panel-split" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="stack" style={{ gap: 22 }}>
          <Field label="要朗读的文字">
            <textarea className="textarea" rows={7} value={text} placeholder="在这里输入文字，支持长文本，会自动分句合成。" onChange={(e) => setText(e.target.value)} />
          </Field>
          {sovits ? (
            <>
              <div className="card pad stack" style={{ gap: 18 }}>
                <div className="row"><AudioLines size={16} color="var(--accent)" /><strong>参考音色</strong><span className="faint small">GPT-SoVITS 会模仿这段声音</span></div>
                {voices.length > 0 && (
                  <div className="grid-2" style={{ gap: 8 }}>
                    {voices.map((v) => (
                      <div key={v.name} className={`voice-card ${activeVoice === v.name ? "on" : ""}`} onClick={() => pickVoice(v)}>
                        <AudioLines size={14} /> <span className="ellipsis" style={{ flex: 1 }}>{v.name}</span>
                        <button className="btn ghost icon sm" style={{ width: 22, height: 22 }} onClick={(e) => { e.stopPropagation(); deleteVoice(v.name); }}><Trash2 size={12} /></button>
                      </div>
                    ))}
                  </div>
                )}
                <AudioInput value={ref} onChange={(v) => { setRef(v); setActiveVoice(""); }} hint="3~10 秒，吐字清晰" />
                <Field label="参考音频的文字内容" hint="填写参考音频里说的话，效果更好">
                  <input className="input" value={promptText} onChange={(e) => setPromptText(e.target.value)} />
                </Field>
                <div className="grid-2">
                  <Field label="参考音频语言">
                    <select className="select" value={promptLang} onChange={(e) => setPromptLang(e.target.value)}>{LANGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                  </Field>
                  <div className="field" style={{ justifyContent: "flex-end" }}>
                    <button className="btn" disabled={!ref} onClick={saveVoice}><Plus size={14} /> 保存为常用音色</button>
                  </div>
                </div>
              </div>
              <div className="grid-2">
                <Field label="合成语言">
                  <select className="select" value={lang} onChange={(e) => setLang(e.target.value)}>{LANGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                </Field>
                <Slider label="语速" value={speed} min={0.6} max={1.65} step={0.05} onChange={setSpeed} format={(v) => `${v.toFixed(2)}×`} />
              </div>
              <div className="grid-2">
                <Slider label="Top K" value={topK} min={1} max={50} step={1} onChange={setTopK} />
                <Slider label="温度" value={temperature} min={0.1} max={1.5} step={0.05} onChange={setTemperature} />
              </div>
              {(weights.gpt.length > 0 || weights.sovits.length > 0) && (
                <details className="card pad">
                  <summary style={{ cursor: "pointer", fontWeight: 580 }}>切换训练好的模型权重</summary>
                  <div className="stack" style={{ gap: 14, marginTop: 16 }}>
                    <Field label="GPT 权重">
                      <select className="select" value={gptW} onChange={(e) => setGptW(e.target.value)}>
                        <option value="">保持不变</option>
                        {weights.gpt.map((w) => <option key={w} value={w}>{w.split(/[\\/]/).pop()}</option>)}
                      </select>
                    </Field>
                    <Field label="SoVITS 权重">
                      <select className="select" value={sovitsW} onChange={(e) => setSovitsW(e.target.value)}>
                        <option value="">保持不变</option>
                        {weights.sovits.map((w) => <option key={w} value={w}>{w.split(/[\\/]/).pop()}</option>)}
                      </select>
                    </Field>
                    <button className="btn" disabled={!ready || (!gptW && !sovitsW)} onClick={switchWeights}>应用权重</button>
                  </div>
                </details>
              )}
            </>
          ) : (
            <>
              <div className="grid-2">
                <Field label="音色" hint="服务支持的音色名称"><input className="input" value={voiceName} onChange={(e) => setVoiceName(e.target.value)} /></Field>
                <Slider label="语速" value={speed} min={0.5} max={2} step={0.05} onChange={setSpeed} format={(v) => `${v.toFixed(2)}×`} />
              </div>
              <Field label="风格指令（可选）"><input className="input" value={instructions} placeholder="例如：温柔、缓慢地朗读" onChange={(e) => setInstructions(e.target.value)} /></Field>
              {cloneCap && (<Field label="参考音频（声音克隆）"><AudioInput value={ref} onChange={setRef} /></Field>)}
            </>
          )}
          <button className="btn primary lg" disabled={busy} onClick={run}>
            {busy ? <Loader2 size={17} className="spin" /> : <AudioLines size={17} />} {busy ? "合成中…" : "开始合成"}
          </button>
        </div>
        <div>
          <div className="section-head" style={{ marginBottom: 16 }}><h3 className="section-title">合成记录</h3></div>
          <OutputList items={items} onDelete={async (id) => { await api.del(`/api/outputs/${id}`); load(); }} />
        </div>
      </div>
    </div>
  );
}

export function AsrPanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast } = useStore();
  const [file, setFile] = useState<{ id: string; url: string; name: string } | null>(null);
  const [language, setLanguage] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ text: string; name: string; elapsed: number }[]>([]);

  const run = async () => {
    if (!file) return toast("请先上传或录制音频", "error");
    if (!ready) return toast("请先启动模型", "error");
    setBusy(true);
    try {
      const r = await api.post<{ text: string; elapsed: number }>(`/api/models/${model.id}/asr`, { file: file.id, language });
      setResults([{ ...r, name: file.name }, ...results]);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" style={{ maxWidth: 860 }}>
      <div className="stack" style={{ gap: 22 }}>
        <Field label="音频"><AudioInput value={file} onChange={setFile} hint="支持常见音频格式，会自动转为 WAV" /></Field>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ width: 220 }}>
            <Field label="语言">
              <select className="select" value={language} onChange={(e) => setLanguage(e.target.value)}>
                <option value="">自动检测</option><option value="zh">中文</option><option value="en">英文</option><option value="ja">日文</option>
              </select>
            </Field>
          </div>
          <span className="spacer" />
          <button className="btn primary lg" disabled={busy} onClick={run}>{busy ? <Loader2 size={17} className="spin" /> : <Mic size={17} />} 转成文字</button>
        </div>
        <div className="divider" />
        {results.length === 0 ? <p className="faint" style={{ textAlign: "center" }}>识别结果会显示在这里。</p> : results.map((r, i) => (
          <div key={i} className="card pad">
            <div className="row" style={{ marginBottom: 10 }}>
              <span className="faint small" style={{ flex: 1 }}>{r.name} · {r.elapsed}s</span>
              <button className="btn ghost sm" onClick={() => { navigator.clipboard.writeText(r.text); toast("已复制", "success"); }}><Copy size={13} /> 复制</button>
            </div>
            <div style={{ lineHeight: 1.8, whiteSpace: "pre-wrap" }}>{r.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

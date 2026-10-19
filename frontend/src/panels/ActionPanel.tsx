import { Clapperboard, Download, Loader2, Play, Square, Trash2, Upload, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Field } from "../components/ui";
import { api, upload } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useStore } from "../lib/store";
import type { ActionField, ActionSpec, Capability, Model, OutputItem } from "../lib/types";

interface Pair {
  cap: Capability;
  action: ActionSpec;
}

interface Props {
  model: Model;
  ready: boolean;
  capTypes: string[];
  endpoint: "video" | "image";
  kind: "video" | "image";
}

function pairsFor(model: Model, capTypes: string[]): Pair[] {
  const out: Pair[] = [];
  const seen = new Set<string>();
  for (const cap of model.capabilities) {
    if (!cap.enabled || !capTypes.includes(cap.type)) continue;
    const actions = cap.actions || [];
    const candidates = actions.length
      ? actions
      : [{ id: "", label: cap.label || cap.type, fields: [] } as ActionSpec];
    for (const action of candidates) {
      // image_generation and image_edit can expose the same "edit" action; show it once.
      const key = action.id || cap.type + ":" + (action.label || cap.label || cap.type);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ cap, action });
    }
  }
  return out;
}

function loadActionState(modelId: string, actionKey: string): { values?: Record<string, any>; preset?: string } | null {
  try {
    const raw = localStorage.getItem("md-action-" + modelId + ":" + actionKey);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function defaultsOf(pair: Pair | undefined): Record<string, any> {
  const values: Record<string, any> = { ...(pair?.action.defaults || {}) };
  for (const field of pair?.action.fields || []) {
    if (field.default !== undefined && values[field.name] === undefined) values[field.name] = field.default;
  }
  return values;
}

export function ActionPanel({ model, ready, capTypes, endpoint, kind }: Props) {
  const { toast } = useStore();
  const pairs = useMemo(() => pairsFor(model, capTypes), [model, capTypes]);
  const [idx, setIdx] = useState(0);
  const pair = pairs[Math.min(idx, Math.max(0, pairs.length - 1))];
  const [values, setValues] = useState<Record<string, any>>(() => defaultsOf(pair));
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OutputItem[]>([]);
  const [history, setHistory] = useState<OutputItem[]>([]);
  const [presetNote, setPresetNote] = useState("");
  const [activePreset, setActivePreset] = useState("");

  const actionKey = pair ? pair.cap.type + ":" + pair.action.id : "";

  useEffect(() => {
    const saved = loadActionState(model.id, actionKey);
    const presets = pair?.action.presets || [];
    const savedPreset = saved?.preset && presets.some((p) => p.id === saved.preset) ? saved.preset : "";
    setValues(saved?.values ? { ...defaultsOf(pair), ...saved.values } : defaultsOf(pair));
    setActivePreset(savedPreset);
    setPresetNote(savedPreset ? (presets.find((p) => p.id === savedPreset)?.note || "") : "");
    setPreviews({});
    setResult([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionKey, model.id]);

  const persist = (nextValues: Record<string, any>, preset: string) => {
    try {
      localStorage.setItem("md-action-" + model.id + ":" + actionKey, JSON.stringify({ values: nextValues, preset }));
    } catch {
      /* ignore quota errors */
    }
  };

  const load = () =>
    api.get<OutputItem[]>("/api/outputs?kind=" + kind + "&model_id=" + model.id + "&limit=40")
      .then(setHistory)
      .catch(() => {});
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model.id]);

  if (!pair) {
    return (
      <div className="panel">
        <div className="banner">这个模型没有配置可用的 {kind} 功能。</div>
      </div>
    );
  }

  const fields = pair.action.fields || [];

  const setValue = (name: string, value: any) => {
    setValues((v) => {
      const next = { ...v, [name]: value };
      persist(next, "");
      return next;
    });
    setActivePreset("");
    setPresetNote("");
  };

  const applyPreset = (preset: NonNullable<ActionSpec["presets"]>[number]) => {
    setValues((v) => {
      const next = { ...v, ...preset.values };
      persist(next, preset.id);
      return next;
    });
    setActivePreset(preset.id);
    setPresetNote(preset.note || "");
  };

  const pickImage = async (field: ActionField, file?: File) => {
    if (!file) return;
    try {
      const up = await upload(file, file.name);
      setValue(field.name, up.id);
      setPreviews((p) => ({ ...p, [field.name]: URL.createObjectURL(file) }));
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const run = async () => {
    if (!ready) return toast("请先启动模型", "error");
    for (const field of fields) {
      if (field.required && !String(values[field.name] ?? "").trim()) {
        return toast("请填写" + (field.label || field.name), "error");
      }
    }
    setBusy(true);
    setResult([]);
    try {
      const res = await api.post<{ outputs: OutputItem[] }>("/api/models/" + model.id + "/" + endpoint, {
        action: pair.action.id,
        ...values,
      });
      setResult(res.outputs || []);
      await load();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    try {
      await api.post("/api/models/" + model.id + "/video/stop");
      toast("已请求中断", "info");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const remove = async (id: string) => {
    await api.del("/api/outputs/" + id).catch(() => {});
    setResult((list) => list.filter((o) => o.id !== id));
    await load();
  };

  const media = (item: OutputItem) =>
    item.kind === "video" ? (
      <video src={item.url} controls preload="metadata" style={{ width: "100%", borderRadius: 12, display: "block" }} />
    ) : (
      <img src={item.url} alt="" style={{ width: "100%", borderRadius: 12, display: "block" }} />
    );

  return (
    <div className="panel" style={{ maxWidth: 1280 }}>
      {pairs.length > 1 && (
        <div className="segmented" style={{ marginBottom: 24 }}>
          {pairs.map((p, i) => (
            <button key={p.cap.type + p.action.id} className={i === idx ? "on" : ""} onClick={() => setIdx(i)}>
              {p.action.label}
            </button>
          ))}
        </div>
      )}
      <div className="panel-split">
        <div className="stack" style={{ gap: 20, position: "sticky", top: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            <Clapperboard size={17} color="var(--accent)" />
            <strong>{pair.action.label}</strong>
            {pair.action.description && <span className="faint small">{pair.action.description}</span>}
          </div>
          {(pair.action.presets || []).length > 0 && (
            <div className="stack" style={{ gap: 8 }}>
              <div className="row" style={{ gap: 10 }}>
                <div className="field-label">画质预设</div>
                <span className={"chip " + (activePreset ? "selected" : "outline")} style={{ height: 22, fontSize: 11 }}>
                  {activePreset
                    ? "已选：" + ((pair.action.presets || []).find((p) => p.id === activePreset)?.label || activePreset)
                    : "当前：自定义"}
                </span>
              </div>
              <div className="row wrap" style={{ gap: 6 }}>
                {(pair.action.presets || []).map((preset) => (
                  <button
                    key={preset.id}
                    className={"chip clickable " + (activePreset === preset.id ? "selected" : "outline")}
                    onClick={() => applyPreset(preset)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
              {presetNote && <div className="faint small">{presetNote}</div>}
            </div>
          )}
          {fields.map((field) => (
            <Field key={field.name} label={field.label || field.name} hint={field.help}>
              {field.type === "image" ? (
                <div className="stack" style={{ gap: 8 }}>
                  {previews[field.name] ? (
                    <div className="row" style={{ gap: 10 }}>
                      <img src={previews[field.name]} alt="" style={{ width: 96, height: 96, objectFit: "cover", borderRadius: 10 }} />
                      <button
                        className="btn ghost sm"
                        onClick={() => {
                          setValue(field.name, "");
                          setPreviews((p) => ({ ...p, [field.name]: "" }));
                        }}
                      >
                        <X size={13} /> 移除
                      </button>
                    </div>
                  ) : (
                    <label className="btn" style={{ alignSelf: "flex-start" }}>
                      <Upload size={15} /> 选择图片
                      <input
                        type="file"
                        accept={field.accept || "image/*"}
                        hidden
                        onChange={(e) => pickImage(field, e.target.files?.[0])}
                      />
                    </label>
                  )}
                </div>
              ) : field.type === "textarea" ? (
                <textarea
                  className="textarea"
                  rows={4}
                  value={values[field.name] ?? ""}
                  placeholder={field.placeholder || ""}
                  onChange={(e) => setValue(field.name, e.target.value)}
                />
              ) : field.type === "select" ? (
                <select className="select" value={values[field.name] ?? ""} onChange={(e) => setValue(field.name, e.target.value)}>
                  {(field.options || []).map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              ) : field.type === "number" ? (
                <input
                  className="input mono"
                  type="number"
                  value={values[field.name] ?? ""}
                  onChange={(e) => setValue(field.name, e.target.value === "" ? "" : Number(e.target.value))}
                />
              ) : (
                <input className="input" value={values[field.name] ?? ""} placeholder={field.placeholder || ""} onChange={(e) => setValue(field.name, e.target.value)} />
              )}
            </Field>
          ))}
          <div className="row" style={{ gap: 10 }}>
            <button className="btn primary lg" disabled={busy} onClick={run}>
              {busy ? <Loader2 size={17} className="spin" /> : <Play size={17} />}
              {busy ? "生成中…" : "开始生成"}
            </button>
            {endpoint === "video" && busy && (
              <button className="btn" onClick={stop}><Square size={14} /> 中断</button>
            )}
          </div>
          {busy && (
            <div className="faint small">任务在 ComfyUI 里排队执行，首次加载模型可能要好几分钟；可以先去日志页看进度。</div>
          )}
        </div>

        <div>
          <div className="section-head" style={{ marginBottom: 16 }}>
            <h3 className="section-title">本次结果</h3>
            <span className="section-sub">{result.length ? result.length + " 个文件" : ""}</span>
          </div>
          {result.length > 0 ? (
            <div className="stack" style={{ gap: 14 }}>
              {result.map((item) => (
                <div key={item.id} className="card pad">
                  {media(item)}
                  <div className="row" style={{ marginTop: 10 }}>
                    <span className="faint small ellipsis" style={{ flex: 1 }}>{item.prompt}</span>
                    <a className="btn sm" href={item.url} download><Download size={13} /> 下载</a>
                    <button className="btn ghost icon sm" onClick={() => remove(item.id)}><Trash2 size={13} /></button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty" style={{ padding: "70px 0" }}>
              <p className="faint">生成的内容会显示在这里，并自动保存到作品库。</p>
            </div>
          )}

          <div className="section-head" style={{ margin: "28px 0 16px" }}>
            <h3 className="section-title">历史记录</h3>
            <span className="section-sub">{history.length ? history.length + " 个" : ""}</span>
          </div>
          {history.length === 0 ? (
            <p className="faint small">还没有生成记录。</p>
          ) : (
            <div className="gallery-grid">
              {history.map((item) => (
                <div key={item.id} className="gallery-item" title={item.prompt}>
                  {media(item)}
                  <div className="cap">
                    <div className="ellipsis">{item.prompt}</div>
                    <div style={{ opacity: 0.7 }}>{timeAgo(item.created_at)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

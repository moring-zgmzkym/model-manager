import { Dice5, Download, Loader2, Wand2, X } from "lucide-react";
import { Fragment, useEffect, useRef, useState } from "react";
import { Field, Slider } from "../components/ui";
import { api, upload } from "../lib/api";
import { has } from "../lib/capabilities";
import { timeAgo } from "../lib/format";
import { useStore } from "../lib/store";
import type { Model, OutputItem } from "../lib/types";

const RATIOS = [
  { label: "1:1", size: "1024x1024", w: 16, h: 16 },
  { label: "4:3", size: "1152x864", w: 18, h: 13.5 },
  { label: "3:4", size: "864x1152", w: 13.5, h: 18 },
  { label: "16:9", size: "1280x720", w: 20, h: 11.25 },
  { label: "9:16", size: "720x1280", w: 11.25, h: 20 },
];

export function Lightbox({ item, onClose, onDelete }: { item: OutputItem; onClose: () => void; onDelete?: () => void }) {
  useEffect(() => {
    const fn = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [onClose]);
  return (
    <div className="lightbox" onClick={onClose}>
      <div className="lightbox-img">
        {item.kind === "video"
          ? <video src={item.url} controls autoPlay onClick={(e) => e.stopPropagation()} />
          : <img src={item.url} alt="" onClick={(e) => e.stopPropagation()} />}
      </div>
      <div className="lightbox-side" onClick={(e) => e.stopPropagation()}>
        <div className="row"><strong style={{ flex: 1 }}>作品详情</strong><button className="btn ghost icon sm" onClick={onClose}><X size={15} /></button></div>
        <Field label="提示词"><div style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>{item.prompt || "—"}</div></Field>
        <dl className="kv" style={{ gridTemplateColumns: "80px 1fr" }}>
          <dt>模型</dt><dd>{item.model_name}</dd>
          <dt>时间</dt><dd>{new Date(item.created_at * 1000).toLocaleString("zh-CN")}</dd>
          {Object.entries(item.meta || {}).filter(([, v]) => v !== null && v !== "" && v !== undefined).map(([k, v]) => (
            <Fragment key={k}><dt>{k}</dt><dd className="mono small">{String(v)}</dd></Fragment>
          ))}
        </dl>
        <div className="row" style={{ marginTop: "auto" }}>
          <a className="btn" href={item.url} download><Download size={15} /> 下载</a>
          {onDelete && <button className="btn danger" onClick={onDelete}>删除</button>}
        </div>
      </div>
    </div>
  );
}

export function ImagePanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast } = useStore();
  const canGen = has(model, "image_generation");
  const canEdit = has(model, "image_edit");
  const [mode, setMode] = useState<"gen" | "edit">(canGen ? "gen" : "edit");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [ratio, setRatio] = useState(RATIOS[0]);
  const [n, setN] = useState(1);
  const [steps, setSteps] = useState(30);
  const [guidance, setGuidance] = useState(4);
  const [seed, setSeed] = useState("");
  const [src, setSrc] = useState<{ id: string; preview: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<OutputItem[]>([]);
  const [view, setView] = useState<OutputItem | null>(null);
  const [over, setOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api.get<OutputItem[]>(`/api/outputs?kind=image&model_id=${model.id}&limit=60`).then(setHistory).catch(() => {});
  useEffect(() => { load(); }, [model.id]); // eslint-disable-line

  const pickFile = async (f?: File) => {
    if (!f) return;
    const up = await upload(f, f.name);
    setSrc({ id: up.id, preview: URL.createObjectURL(f) });
  };

  const run = async () => {
    if (!prompt.trim()) return toast("请先描述你想要的画面", "error");
    if (!ready) return toast("请先启动模型", "error");
    if (mode === "edit" && !src) return toast("请上传要编辑的图片", "error");
    setBusy(true);
    try {
      await api.post(`/api/models/${model.id}/image`, {
        prompt, negative_prompt: negative || undefined, size: ratio.size, n, steps, guidance_scale: guidance,
        true_cfg_scale: guidance, seed: seed === "" ? undefined : Number(seed), image: mode === "edit" ? src?.id : undefined,
      });
      await load();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" style={{ maxWidth: 1280 }}>
      <div className="panel-split">
        <div className="stack" style={{ gap: 22, position: "sticky", top: 0 }}>
          {canGen && canEdit && (
            <div className="segmented">
              <button className={mode === "gen" ? "on" : ""} onClick={() => setMode("gen")}>文生图</button>
              <button className={mode === "edit" ? "on" : ""} onClick={() => setMode("edit")}>图像编辑</button>
            </div>
          )}
          {mode === "edit" && (
            <div
              className={`dropzone ${over ? "over" : ""}`}
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); pickFile(e.dataTransfer.files[0]); }}
            >
              {src ? <img src={src.preview} alt="" /> : "点击或拖入要编辑的图片"}
              <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => pickFile(e.target.files?.[0])} />
            </div>
          )}
          <Field label={mode === "edit" ? "修改指令" : "画面描述"}>
            <textarea
              className="textarea"
              rows={5}
              value={prompt}
              placeholder={mode === "edit" ? "例如：把背景换成黄昏的海边" : "例如：清晨薄雾中的江南水乡，青石板路，电影感光影，高细节"}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => (e.metaKey || e.ctrlKey) && e.key === "Enter" && run()}
            />
          </Field>
          <Field label="不希望出现的内容（可选）">
            <input className="input" value={negative} placeholder="模糊、低质量、文字水印" onChange={(e) => setNegative(e.target.value)} />
          </Field>
          {mode === "gen" && (
            <Field label="画面比例">
              <div className="ratio-grid">
                {RATIOS.map((r) => (
                  <button key={r.label} className={`ratio ${ratio.label === r.label ? "on" : ""}`} onClick={() => setRatio(r)}>
                    <i style={{ width: r.w, height: r.h }} />{r.label}
                  </button>
                ))}
              </div>
            </Field>
          )}
          <div className="grid-2">
            <Slider label="数量" value={n} min={1} max={4} step={1} onChange={setN} />
            <Slider label="步数" value={steps} min={4} max={80} step={1} onChange={setSteps} />
          </div>
          <div className="grid-2">
            <Slider label="引导强度" value={guidance} min={1} max={12} step={0.5} onChange={setGuidance} />
            <Field label="随机种子">
              <div className="row" style={{ gap: 6 }}>
                <input className="input mono" value={seed} placeholder="随机" onChange={(e) => setSeed(e.target.value.replace(/\D/g, ""))} />
                <button className="btn icon" title="随机" onClick={() => setSeed(String(Math.floor(Math.random() * 2 ** 31)))}><Dice5 size={15} /></button>
              </div>
            </Field>
          </div>
          <button className="btn primary lg" disabled={busy} onClick={run}>
            {busy ? <Loader2 size={17} className="spin" /> : <Wand2 size={17} />} {busy ? "生成中…" : mode === "edit" ? "开始编辑" : "生成图片"}
          </button>
        </div>
        <div>
          <div className="section-head" style={{ marginBottom: 16 }}>
            <h3 className="section-title">生成结果</h3>
            <span className="section-sub">{history.length ? `${history.length} 张` : ""}</span>
          </div>
          <div className="gallery-grid">
            {busy && Array.from({ length: n }).map((_, i) => <div key={i} className="placeholder-img">正在绘制…</div>)}
            {history.map((o) => (
              <div key={o.id} className="gallery-item" onClick={() => setView(o)}>
                <img src={o.url} alt="" loading="lazy" />
                <div className="cap"><div className="ellipsis">{o.prompt}</div><div style={{ opacity: .7 }}>{timeAgo(o.created_at)}</div></div>
              </div>
            ))}
          </div>
          {!busy && history.length === 0 && <div className="empty" style={{ padding: "80px 0" }}><p className="faint">生成的图片会显示在这里，并自动保存到作品库。</p></div>}
        </div>
      </div>
      {view && (
        <Lightbox item={view} onClose={() => setView(null)}
          onDelete={async () => { await api.del(`/api/outputs/${view.id}`); setView(null); load(); }} />
      )}
    </div>
  );
}

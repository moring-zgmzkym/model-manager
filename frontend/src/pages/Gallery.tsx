import { FolderOpen, Images, Music2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Empty } from "../components/ui";
import { api } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useStore } from "../lib/store";
import type { OutputItem } from "../lib/types";
import { Lightbox } from "../panels/ImagePanel";

export function Gallery() {
  const { models, toast } = useStore();
  const [items, setItems] = useState<OutputItem[]>([]);
  const [kind, setKind] = useState<"all" | "image" | "audio">("all");
  const [modelId, setModelId] = useState("");
  const [view, setView] = useState<OutputItem | null>(null);

  const load = () => api.get<OutputItem[]>("/api/outputs?limit=1000").then(setItems).catch(() => {});
  useEffect(() => { load(); }, []);

  const shown = items.filter((o) => (kind === "all" || o.kind === kind) && (!modelId || o.model_id === modelId));
  const images = shown.filter((o) => o.kind === "image");
  const audios = shown.filter((o) => o.kind === "audio");
  const usedModels = [...new Set(items.map((o) => o.model_id))].map((id) => ({ id, name: items.find((o) => o.model_id === id)?.model_name || models.find((m) => m.id === id)?.name || id }));

  const del = async (id: string) => {
    await api.del(`/api/outputs/${id}`);
    setView(null);
    load();
  };

  return (
    <div className="page wide">
      <div className="page-head">
        <div>
          <div className="eyebrow">作品库</div>
          <h1 className="page-title">所有生成的内容</h1>
          <div className="page-sub">图片和音频会自动保存到本地，按日期整理在数据目录的 outputs 文件夹里。</div>
        </div>
        <button className="btn" onClick={() => api.post("/api/open-folder", { outputs: true }).catch((e) => toast(e.message, "error"))}>
          <FolderOpen size={15} /> 打开文件夹
        </button>
      </div>
      <div className="row" style={{ marginBottom: 28 }}>
        <div className="segmented">
          <button className={kind === "all" ? "on" : ""} onClick={() => setKind("all")}>全部 <span className="faint">{items.length}</span></button>
          <button className={kind === "image" ? "on" : ""} onClick={() => setKind("image")}><Images size={14} /> 图片</button>
          <button className={kind === "audio" ? "on" : ""} onClick={() => setKind("audio")}><Music2 size={14} /> 音频</button>
        </div>
        <span className="spacer" />
        <select className="select" style={{ width: 220 }} value={modelId} onChange={(e) => setModelId(e.target.value)}>
          <option value="">所有模型</option>
          {usedModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </div>

      {shown.length === 0 ? (
        <div className="card"><Empty icon={<Images size={24} />} title="还没有作品" text="在工作台里生成图片或语音后，会自动出现在这里。" /></div>
      ) : (
        <div className="stack" style={{ gap: 40 }}>
          {images.length > 0 && (
            <div>
              {kind === "all" && <h3 className="section-title" style={{ marginBottom: 16 }}>图片</h3>}
              <div className="gallery-grid">
                {images.map((o) => (
                  <div key={o.id} className="gallery-item" onClick={() => setView(o)}>
                    <img src={o.url} alt="" loading="lazy" />
                    <div className="cap"><div className="ellipsis">{o.prompt}</div><div style={{ opacity: .7 }}>{o.model_name} · {timeAgo(o.created_at)}</div></div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {audios.length > 0 && (
            <div>
              {kind === "all" && <h3 className="section-title" style={{ marginBottom: 16 }}>音频</h3>}
              <div className="stack" style={{ gap: 10 }}>
                {audios.map((o) => (
                  <div key={o.id} className="audio-row">
                    <div className="txt">
                      <div className="ellipsis" title={o.prompt}>{o.prompt || "（无文本）"}</div>
                      <div className="faint small">{o.model_name} · {timeAgo(o.created_at)}</div>
                    </div>
                    <audio controls src={o.url} preload="none" />
                    <button className="btn ghost icon sm" onClick={() => del(o.id)}><Trash2 size={14} /></button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {view && <Lightbox item={view} onClose={() => setView(null)} onDelete={() => del(view.id)} />}
    </div>
  );
}

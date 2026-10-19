import { Plus, Search } from "lucide-react";
import { useState } from "react";
import { AddModelDialog } from "../components/AddModelDialog";
import { Empty } from "../components/ui";
import { enabledCaps } from "../lib/capabilities";
import { useStore } from "../lib/store";
import { ModelCard } from "./Overview";

const FILTERS: { id: string; label: string; caps?: string[] }[] = [
  { id: "all", label: "全部" },
  { id: "running", label: "运行中" },
  { id: "language", label: "语言", caps: ["chat", "vision", "embedding"] },
  { id: "image", label: "图像", caps: ["image_generation", "image_edit"] },
  { id: "video", label: "视频", caps: ["video_generation"] },
  { id: "audio", label: "语音", caps: ["tts", "voice_clone", "asr", "speech_output", "audio_input"] },
];

export function Models() {
  const { models, loading } = useStore();
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");

  const f = FILTERS.find((x) => x.id === filter)!;
  const shown = models.filter((m) => {
    if (q && !`${m.name} ${m.description} ${m.path}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (filter === "running") return m.runtime.status === "running";
    if (f.caps) return enabledCaps(m).some((c) => f.caps!.includes(c.type));
    return true;
  });

  return (
    <div className="page wide">
      <div className="page-head">
        <div>
          <div className="eyebrow">模型库</div>
          <h1 className="page-title">所有模型</h1>
          <div className="page-sub">每个模型都可以一键启动和关闭。点击卡片查看能力、启动配置和日志。</div>
        </div>
        <button className="btn primary lg" onClick={() => setAdding(true)}><Plus size={17} /> 添加模型</button>
      </div>
      <div className="row" style={{ marginBottom: 28, gap: 14 }}>
        <div className="row wrap" style={{ gap: 6 }}>
          {FILTERS.map((x) => (
            <button key={x.id} className={`chip clickable ${filter === x.id ? "selected" : "outline"}`} style={{ height: 30, padding: "0 13px" }} onClick={() => setFilter(x.id)}>
              {x.label}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <div style={{ position: "relative", width: 280 }}>
          <Search size={15} style={{ position: "absolute", left: 12, top: 11.5, color: "var(--text-3)" }} />
          <input className="input" style={{ paddingLeft: 34 }} placeholder="搜索模型" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {!loading && shown.length === 0 ? (
        <div className="card">
          <Empty
            title={models.length ? "没有符合条件的模型" : "模型库是空的"}
            text={models.length ? "换个筛选条件试试。" : "可以添加单个模型，也可以一次扫描整个模型文件夹。"}
            action={!models.length && <button className="btn primary" onClick={() => setAdding(true)}><Plus size={15} /> 添加模型</button>}
          />
        </div>
      ) : (
        <div className="model-grid">{shown.map((m) => <ModelCard key={m.id} model={m} />)}</div>
      )}
      {adding && <AddModelDialog onClose={() => setAdding(false)} />}
    </div>
  );
}

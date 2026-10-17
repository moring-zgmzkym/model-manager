import { ArrowRight, AudioLines, Cpu, Eye, Images, MemoryStick, MessageSquare, Monitor, Music2, Plus, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddModelDialog } from "../components/AddModelDialog";
import { CapChip, Empty, ModelAvatar, PowerButton, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { TASKS, enabledCaps } from "../lib/capabilities";
import { bytes, duration, greeting } from "../lib/format";
import { useStore } from "../lib/store";
import type { Model, OutputItem, SystemStats } from "../lib/types";

const TASK_ICONS: Record<string, typeof MessageSquare> = { chat: MessageSquare, vision: Eye, image: Images, voice: AudioLines };

export function ModelCard({ model }: { model: Model }) {
  const nav = useNavigate();
  const caps = enabledCaps(model).filter((c) => c.type !== "custom");
  const rt = model.runtime;
  return (
    <div className="card hover model-card" onClick={() => nav(`/models/${model.id}`)}>
      <div className="model-card-head">
        <ModelAvatar model={model} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="model-card-name ellipsis">{model.name}</div>
          <div className="model-card-desc ellipsis">{model.description || model.path}</div>
        </div>
        <PowerButton model={model} />
      </div>
      <div className="cap-row">
        {caps.slice(0, 5).map((c) => <CapChip key={c.type + c.label} type={c.type} label={c.label} />)}
        {caps.length > 5 && <span className="chip">+{caps.length - 5}</span>}
        {caps.length === 0 && <span className="faint small">尚未配置能力</span>}
      </div>
      <div className="model-card-foot">
        <StatusBadge status={rt.status} external={rt.external} />
        <span className="spacer" />
        {rt.status === "running" ? (
          <>
            {rt.memory ? <span className="faint small tnum">{bytes(rt.memory)}</span> : null}
            <button className="btn sm" onClick={(e) => { e.stopPropagation(); nav(`/studio/${model.id}`); }}>
              使用 <ArrowRight size={13} />
            </button>
          </>
        ) : rt.status === "error" ? (
          <span className="small ellipsis" style={{ color: "var(--danger)", maxWidth: 180 }} title={rt.message}>{rt.message}</span>
        ) : rt.status === "starting" ? (
          <span className="faint small">{rt.message || "加载中"} · {duration(rt.started_at)}</span>
        ) : (
          <span className="faint small mono">{model.launch.port ? `:${model.launch.port}` : ""}</span>
        )}
      </div>
    </div>
  );
}

export function Overview() {
  const { models, loading } = useStore();
  const nav = useNavigate();
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [recent, setRecent] = useState<OutputItem[]>([]);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const load = () => api.get<SystemStats>("/api/system").then(setStats).catch(() => {});
    load();
    const t = setInterval(load, 3000);
    api.get<OutputItem[]>("/api/outputs?limit=6").then(setRecent).catch(() => {});
    return () => clearInterval(t);
  }, []);

  const running = models.filter((m) => m.runtime.status === "running");
  const gpu = stats?.gpus[0];

  const goTask = (caps: string[], panel: string) => {
    const pick = (list: Model[]) => list.find((m) => enabledCaps(m).some((c) => caps.includes(c.type)));
    const m = pick(running) || pick(models);
    nav(m ? `/studio/${m.id}?panel=${panel}` : "/models");
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">{new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}</div>
          <h1 className="page-title">{greeting()}</h1>
          <div className="page-sub">
            {models.length === 0
              ? "从添加第一个本地模型开始。"
              : running.length
                ? `${running.length} 个模型正在运行，共 ${models.length} 个模型。`
                : `${models.length} 个模型已就绪，点击电源按钮即可一键启动。`}
          </div>
        </div>
        <button className="btn primary lg" onClick={() => setAdding(true)}><Plus size={17} /> 添加模型</button>
      </div>

      <div className="stats">
        <div className="card stat">
          <div className="stat-label"><Zap size={14} /> 运行中的模型</div>
          <div className="stat-value">{running.length}<small>/ {models.length}</small></div>
          <div className="meter"><div style={{ width: `${models.length ? (running.length / models.length) * 100 : 0}%`, background: "var(--success)" }} /></div>
        </div>
        <div className="card stat">
          <div className="stat-label"><Cpu size={14} /> 处理器</div>
          <div className="stat-value">{stats ? Math.round(stats.cpu) : "—"}<small>%</small></div>
          <div className="meter"><div style={{ width: `${stats?.cpu ?? 0}%` }} /></div>
        </div>
        <div className="card stat">
          <div className="stat-label"><MemoryStick size={14} /> 内存</div>
          <div className="stat-value">{stats ? bytes(stats.memory.used).split(" ")[0] : "—"}<small>{stats ? `${bytes(stats.memory.used).split(" ")[1]} / ${bytes(stats.memory.total)}` : ""}</small></div>
          <div className="meter"><div style={{ width: `${stats?.memory.percent ?? 0}%` }} /></div>
        </div>
        <div className="card stat">
          <div className="stat-label"><Monitor size={14} /> {gpu ? "显存" : "显卡"}</div>
          {gpu ? (
            <>
              <div className="stat-value">{bytes(gpu.memory_used).split(" ")[0]}<small>{bytes(gpu.memory_used).split(" ")[1]} / {bytes(gpu.memory_total)}</small></div>
              <div className="meter"><div style={{ width: `${(gpu.memory_used / gpu.memory_total) * 100}%`, background: "var(--warning)" }} /></div>
            </>
          ) : (
            <>
              <div className="stat-value" style={{ fontSize: 15, color: "var(--text-3)", marginTop: 16 }}>未检测到 NVIDIA 显卡</div>
            </>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="section-title">我想要…</h2>
          <span className="section-sub">自动选择合适的模型并打开工作台</span>
        </div>
        <div className="task-grid">
          {TASKS.map((t) => {
            const Icon = TASK_ICONS[t.id];
            const avail = models.some((m) => enabledCaps(m).some((c) => t.caps.includes(c.type)));
            return (
              <div key={t.id} className="card hover task" style={{ opacity: avail ? 1 : 0.55 }} onClick={() => goTask(t.caps, t.panel)}>
                <div className="task-icon"><Icon size={19} strokeWidth={1.8} /></div>
                <div>
                  <h4>{t.label}</h4>
                  <p>{avail ? t.desc : "暂无支持此功能的模型"}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="section-title">我的模型</h2>
          {models.length > 0 && <button className="btn ghost sm" onClick={() => nav("/models")}>全部模型 <ArrowRight size={13} /></button>}
        </div>
        {!loading && models.length === 0 ? (
          <div className="card">
            <Empty
              title="还没有模型"
              text="填写本地模型的文件夹路径，ModelDock 会自动识别它能做什么、该怎么启动。"
              action={<button className="btn primary" onClick={() => setAdding(true)}><Plus size={15} /> 添加模型</button>}
            />
          </div>
        ) : (
          <div className="model-grid">
            {[...models].sort((a, b) => Number(b.runtime.status === "running") - Number(a.runtime.status === "running")).slice(0, 6)
              .map((m) => <ModelCard key={m.id} model={m} />)}
          </div>
        )}
      </div>

      {recent.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2 className="section-title">最近作品</h2>
            <button className="btn ghost sm" onClick={() => nav("/gallery")}>作品库 <ArrowRight size={13} /></button>
          </div>
          <div className="recent-strip">
            {recent.map((o) => (
              <div key={o.id} className={`recent-item ${o.kind}`} onClick={() => nav("/gallery")} style={{ cursor: "pointer" }} title={o.prompt}>
                {o.kind === "image" ? <img src={o.url} alt="" /> : <Music2 size={22} />}
              </div>
            ))}
          </div>
        </div>
      )}
      {adding && <AddModelDialog onClose={() => setAdding(false)} />}
    </div>
  );
}

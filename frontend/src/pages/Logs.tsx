import { Search } from "lucide-react";
import { useState } from "react";
import { LogViewer } from "../components/LogViewer";
import { Empty, ModelAvatar } from "../components/ui";
import { useStore } from "../lib/store";

export function Logs() {
  const { models, catalog } = useStore();
  const services = models.flatMap((m) => [
    { key: m.id, label: m.name, sub: "主服务", model: m, status: m.runtime.status },
    ...Object.entries(m.services).map(([ctype, rt]) => ({
      key: `${m.id}:${ctype}`, label: m.name, sub: catalog?.capabilities[ctype]?.label || ctype, model: m, status: rt.status,
    })),
  ]);
  const [key, setKey] = useState(services.find((s) => s.status === "running")?.key || services[0]?.key);
  const [filter, setFilter] = useState("");

  return (
    <div className="page wide">
      <div className="page-head">
        <div>
          <div className="eyebrow">运行日志</div>
          <h1 className="page-title">实时输出</h1>
          <div className="page-sub">模型加载进度、报错信息都会显示在这里。启动失败时，先看看最后几行。</div>
        </div>
      </div>
      {services.length === 0 ? (
        <div className="card"><Empty title="还没有模型" /></div>
      ) : (
        <div className="logs-layout">
          <div className="card" style={{ padding: 8, overflowY: "auto" }}>
            {services.map((s) => (
              <div key={s.key} className={`studio-model ${s.key === key ? "on" : ""}`} onClick={() => setKey(s.key)}>
                <ModelAvatar model={s.model} size="sm" />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="nm ellipsis">{s.label}</div>
                  <div className="st"><span className={`dot ${s.status}`} style={{ width: 6, height: 6 }} /> {s.sub}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
            <div style={{ position: "relative", width: 300 }}>
              <Search size={15} style={{ position: "absolute", left: 12, top: 11.5, color: "var(--text-3)" }} />
              <input className="input" style={{ paddingLeft: 34 }} placeholder="筛选日志" value={filter} onChange={(e) => setFilter(e.target.value)} />
            </div>
            <div style={{ flex: 1, minHeight: 0 }}><LogViewer serviceKey={key} filter={filter} /></div>
          </div>
        </div>
      )}
    </div>
  );
}

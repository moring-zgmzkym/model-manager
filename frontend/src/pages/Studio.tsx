import { Loader2, Play, Sparkles } from "lucide-react";
import { useEffect } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Empty, ModelAvatar, PANEL_ICONS, PowerButton, StatusBadge } from "../components/ui";
import { panelsFor } from "../lib/capabilities";
import { duration } from "../lib/format";
import { useStore } from "../lib/store";
import { ChatPanel } from "../panels/ChatPanel";
import { CustomPanel } from "../panels/CustomPanel";
import { EmbeddingPanel } from "../panels/EmbeddingPanel";
import { ImagePanel } from "../panels/ImagePanel";
import { AsrPanel, TtsPanel } from "../panels/AudioPanels";
import { WebuiPanel } from "../panels/WebuiPanel";

export function Studio() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const { models, getModel, start } = useStore();
  const nav = useNavigate();

  const sorted = [...models].filter((m) => panelsFor(m).length)
    .sort((a, b) => Number(b.runtime.status === "running") - Number(a.runtime.status === "running"));
  const model = getModel(id) || undefined;

  useEffect(() => {
    if (!id && sorted.length) nav(`/studio/${sorted[0].id}`, { replace: true });
  }, [id, sorted, nav]);

  const panels = panelsFor(model);
  const panelId = panels.find((p) => p.id === params.get("panel"))?.id || panels[0]?.id;

  if (!models.length || !sorted.length) {
    return (
      <div className="page">
        <Empty icon={<Sparkles size={24} />} title="工作台还没有可用的模型" text="先在模型库中添加模型，并确认至少启用了一项能力。"
          action={<button className="btn primary" onClick={() => nav("/models")}>前往模型库</button>} />
      </div>
    );
  }

  const st = model?.runtime.status;
  const needsServer = panelId !== "webui";
  const remote = model?.adapter === "openai_remote";
  const ready = st === "running" || remote;

  return (
    <div className="page full">
      <div className="studio">
        <aside className="studio-side">
          <h2>模型</h2>
          {sorted.map((m) => (
            <div key={m.id} className={`studio-model ${m.id === id ? "on" : ""}`} onClick={() => nav(`/studio/${m.id}`)}>
              <ModelAvatar model={m} size="sm" />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="nm ellipsis">{m.name}</div>
                <div className="st"><span className={`dot ${m.runtime.status}`} style={{ width: 6, height: 6 }} />
                  {panelsFor(m).map((p) => p.label).join(" · ")}
                </div>
              </div>
            </div>
          ))}
        </aside>
        {model ? (
          <section className="studio-main">
            <div className="studio-top">
              <div className="row" style={{ gap: 12, minWidth: 0 }}>
                <strong className="ellipsis" style={{ fontSize: 15 }}>{model.name}</strong>
                <StatusBadge status={model.runtime.status} external={model.runtime.external} />
              </div>
              <span className="spacer" />
              {panels.length > 1 && (
                <div className="pill-tabs">
                  {panels.map((p) => {
                    const Icon = PANEL_ICONS[p.id];
                    return (
                      <button key={p.id} className={p.id === panelId ? "on" : ""} onClick={() => setParams({ panel: p.id })} title={p.hint}>
                        <Icon size={14} /> {p.label}
                      </button>
                    );
                  })}
                </div>
              )}
              <PowerButton model={model} />
            </div>
            {needsServer && !ready && (
              <div style={{ padding: "16px 36px 0" }}>
                <div className={`banner ${st === "error" ? "error" : st === "starting" ? "warn" : "accent"}`}>
                  {st === "starting" ? <Loader2 size={16} className="spin" /> : null}
                  <span style={{ flex: 1 }}>
                    {st === "starting" ? `模型正在加载（${duration(model.runtime.started_at)}），完成后即可使用。`
                      : st === "error" ? `启动失败：${model.runtime.message}`
                        : "模型还没有启动，启动后才能使用。"}
                  </span>
                  {st !== "starting" && <button className="btn sm primary" onClick={() => start(model.id)}><Play size={13} /> 一键启动</button>}
                  {st === "error" && <button className="btn sm" onClick={() => nav(`/models/${model.id}`)}>查看日志</button>}
                </div>
              </div>
            )}
            <div className="studio-body" style={panelId === "chat" || panelId === "webui" ? { overflow: "hidden", position: "relative" } : undefined}>
              {panelId === "chat" && <ChatPanel key={model.id} model={model} ready={!!ready} />}
              {panelId === "image" && <ImagePanel key={model.id} model={model} ready={!!ready} />}
              {panelId === "tts" && <TtsPanel key={model.id} model={model} ready={!!ready} />}
              {panelId === "asr" && <AsrPanel key={model.id} model={model} ready={!!ready} />}
              {panelId === "embedding" && <EmbeddingPanel key={model.id} model={model} ready={!!ready} />}
              {panelId === "webui" && <WebuiPanel key={model.id} model={model} />}
              {panelId === "custom" && <CustomPanel key={model.id} model={model} ready={!!ready} />}
            </div>
          </section>
        ) : (
          <div className="page"><Empty title="请选择一个模型" /></div>
        )}
      </div>
    </div>
  );
}

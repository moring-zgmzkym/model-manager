import { ExternalLink, Loader2, Play, RotateCw, Square } from "lucide-react";
import { useState } from "react";
import { Empty } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Model } from "../lib/types";

export function WebuiPanel({ model }: { model: Model }) {
  const { refresh, toast, start, stop } = useStore();
  const cap = model.capabilities.find((c) => c.type === "webui" && c.enabled);
  const [nonce, setNonce] = useState(0);
  if (!cap) return null;
  const sub = !!cap.binding.command;
  const status = sub ? model.services.webui?.status || "stopped" : model.runtime.status;
  const url = (cap.binding.url as string) || model.base_url;

  const act = async (action: "start" | "stop") => {
    try {
      if (sub) await api.post(`/api/models/${model.id}/services/webui/${action}`);
      else if (action === "start") await start(model.id);
      else await stop(model.id);
      await refresh();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  return (
    <div className="iframe-wrap">
      <div className="row" style={{ padding: "10px 36px", borderBottom: "1px solid var(--border)", gap: 10 }}>
        <span className={`dot ${status}`} />
        <span className="mono small faint ellipsis" style={{ flex: 1 }}>{url}</span>
        {sub && <span className="faint small">独立服务</span>}
        <button className="btn ghost sm" onClick={() => setNonce(nonce + 1)}><RotateCw size={13} /> 刷新</button>
        <a className="btn sm" href={url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> 新窗口打开</a>
        {status === "running" || status === "starting"
          ? <button className="btn sm" onClick={() => act("stop")}><Square size={12} /> 停止界面</button>
          : <button className="btn sm primary" onClick={() => act("start")}><Play size={12} /> 启动界面</button>}
      </div>
      {status === "running" ? (
        <iframe key={nonce} src={url} title="webui" />
      ) : status === "starting" ? (
        <Empty icon={<Loader2 size={24} className="spin" />} title="界面正在启动" text="首次启动可能需要一点时间，可在日志页查看进度。" />
      ) : (
        <Empty title="原生界面未启动" text="这是模型自带的网页界面（例如 GPT-SoVITS 的训练界面）。启动后会直接显示在这里。"
          action={<button className="btn primary" onClick={() => act("start")}><Play size={15} /> 启动界面</button>} />
      )}
    </div>
  );
}

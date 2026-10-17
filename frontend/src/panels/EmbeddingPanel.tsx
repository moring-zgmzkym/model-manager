import { Binary, Loader2, Plus, X } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Model } from "../lib/types";

export function EmbeddingPanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast } = useStore();
  const [inputs, setInputs] = useState(["今天天气真好", "今天阳光明媚", "我想学习编程"]);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ dim: number; similarity: number[][]; elapsed: number } | null>(null);

  const run = async () => {
    if (!ready) return toast("请先启动模型", "error");
    setBusy(true);
    try {
      setRes(await api.post(`/api/models/${model.id}/embed`, { inputs }));
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const shown = inputs.filter((s) => s.trim());
  return (
    <div className="panel" style={{ maxWidth: 980 }}>
      <div className="panel-split" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="stack" style={{ gap: 12 }}>
          <div className="field-label">输入几段文本，比较它们的语义相似度</div>
          {inputs.map((v, i) => (
            <div className="row" key={i}>
              <span className="chip" style={{ width: 28, justifyContent: "center" }}>{i + 1}</span>
              <input className="input" value={v} onChange={(e) => setInputs(inputs.map((x, j) => (j === i ? e.target.value : x)))} />
              <button className="btn ghost icon sm" disabled={inputs.length <= 2} onClick={() => setInputs(inputs.filter((_, j) => j !== i))}><X size={14} /></button>
            </div>
          ))}
          <div className="row">
            <button className="btn ghost" onClick={() => setInputs([...inputs, ""])}><Plus size={15} /> 添加文本</button>
            <span className="spacer" />
            <button className="btn primary" disabled={busy} onClick={run}>{busy ? <Loader2 size={15} className="spin" /> : <Binary size={15} />} 计算</button>
          </div>
        </div>
        <div>
          {res ? (
            <div className="card pad">
              <div className="faint small" style={{ marginBottom: 16 }}>向量维度 {res.dim} · {res.elapsed}s · 余弦相似度</div>
              <div className="heat" style={{ gridTemplateColumns: `repeat(${res.similarity.length}, 1fr)` }}>
                {res.similarity.flatMap((row, i) => row.map((v, j) => (
                  <div key={`${i}-${j}`} title={`${shown[i]} ↔ ${shown[j]}`}
                    style={{ background: `color-mix(in srgb, var(--accent) ${Math.max(0, v) * 85}%, var(--bg-soft))`, color: v > 0.55 ? "#fff" : "var(--text-2)" }}>
                    {v.toFixed(2)}
                  </div>
                )))}
              </div>
            </div>
          ) : <div className="empty"><p className="faint">相似度矩阵会显示在这里。</p></div>}
        </div>
      </div>
    </div>
  );
}

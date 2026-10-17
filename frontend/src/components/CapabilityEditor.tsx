import { Lock, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { confidenceLabel } from "../lib/capabilities";
import { useStore } from "../lib/store";
import type { Capability } from "../lib/types";
import { CapIcon, Switch } from "./ui";

/** Toggle, inspect and add capabilities. Every edit marks the capability `locked` so rescans keep it. */
export function CapabilityEditor({ caps, onChange, compact }: {
  caps: Capability[]; onChange: (caps: Capability[]) => void; compact?: boolean;
}) {
  const { catalog } = useStore();
  const [adding, setAdding] = useState(false);
  const existing = new Set(caps.map((c) => c.type));
  const addable = Object.entries(catalog?.capabilities || {}).filter(([t]) => !existing.has(t) || t === "custom");

  const update = (i: number, patch: Partial<Capability>) =>
    onChange(caps.map((c, j) => (j === i ? { ...c, ...patch, locked: true } : c)));

  return (
    <div className="cap-list">
      {caps.length === 0 && <div className="banner">还没有识别到能力，可以手动添加。</div>}
      {caps.map((c, i) => {
        const info = catalog?.capabilities[c.type];
        return (
          <div key={`${c.type}-${i}`} className={`cap-item ${c.enabled ? "" : "off"}`}>
            <div className="cap-icon"><CapIcon type={c.type} size={17} /></div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="cap-title">
                {c.label || info?.label || c.type}
                <span className={`chip ${c.source === "manifest" || c.source === "runtime" ? "success" : c.confidence >= 0.85 ? "accent" : "outline"}`} style={{ height: 20, fontSize: 11 }}>
                  {confidenceLabel(c)}
                </span>
                {c.locked && <Lock size={12} className="faint" />}
              </div>
              <div className="cap-desc">{info?.desc}</div>
              {!compact && c.evidence.length > 0 && (
                <ul className="evidence">
                  {c.evidence.slice(0, 4).map((e) => <li key={e}>{e}</li>)}
                </ul>
              )}
            </div>
            <div className="row" style={{ gap: 14 }}>
              {!compact && (
                <span className="faint small tnum" title="识别置信度">
                  <span className="conf"><div style={{ width: `${Math.round(c.confidence * 100)}%` }} /></span>
                  {" "}{Math.round(c.confidence * 100)}%
                </span>
              )}
              {c.source === "user" && (
                <button className="btn ghost icon sm" title="移除" onClick={() => onChange(caps.filter((_, j) => j !== i))}>
                  <Trash2 size={14} />
                </button>
              )}
              <Switch checked={c.enabled} onChange={(v) => update(i, { enabled: v })} />
            </div>
          </div>
        );
      })}
      {adding ? (
        <div className="card pad" style={{ padding: 16 }}>
          <div className="field-label" style={{ marginBottom: 10 }}>选择要添加的能力</div>
          <div className="row wrap">
            {addable.map(([t, info]) => (
              <button
                key={t}
                className="chip clickable outline"
                onClick={() => {
                  onChange([...caps, {
                    type: t, enabled: true, source: "user", confidence: 1, evidence: ["手动添加"],
                    binding: { protocol: info.protocol }, locked: true,
                  }]);
                  setAdding(false);
                }}
              >
                <CapIcon type={t} size={12} /> {info.label}
              </button>
            ))}
            <button className="btn ghost sm" onClick={() => setAdding(false)}>取消</button>
          </div>
        </div>
      ) : (
        <button className="btn ghost" style={{ alignSelf: "flex-start" }} onClick={() => setAdding(true)}>
          <Plus size={15} /> 添加能力
        </button>
      )}
    </div>
  );
}

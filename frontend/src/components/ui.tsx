import {
  AudioLines, Boxes, Code2, Eye, FileAudio, Globe, ImagePlus, Images, Loader2, MessageSquare, Mic, Power,
  Sparkles, Speech, Wand2, X, CheckCircle2, AlertCircle, Info, Binary,
} from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { initials, modelColor } from "../lib/format";
import type { Model, Status } from "../lib/types";
import { useStore } from "../lib/store";

export const CAP_ICONS: Record<string, typeof MessageSquare> = {
  chat: MessageSquare,
  vision: Eye,
  audio_input: Mic,
  speech_output: Speech,
  asr: FileAudio,
  tts: AudioLines,
  voice_clone: Wand2,
  image_generation: Images,
  image_edit: ImagePlus,
  embedding: Binary,
  webui: Globe,
  custom: Code2,
};

export const PANEL_ICONS: Record<string, typeof MessageSquare> = {
  chat: MessageSquare,
  image: Images,
  tts: AudioLines,
  asr: FileAudio,
  embedding: Binary,
  webui: Globe,
  custom: Code2,
};

export function CapIcon({ type, size = 15 }: { type: string; size?: number }) {
  const Icon = CAP_ICONS[type] || Sparkles;
  return <Icon size={size} strokeWidth={1.8} />;
}

export function CapChip({ type, label }: { type: string; label?: string | null }) {
  const { catalog } = useStore();
  return (
    <span className="chip">
      <CapIcon type={type} size={12} />
      {label || catalog?.capabilities[type]?.label || type}
    </span>
  );
}

const STATUS_TEXT: Record<Status, string> = {
  stopped: "未运行",
  starting: "启动中",
  running: "运行中",
  stopping: "停止中",
  error: "出错",
};

export function StatusBadge({ status, external }: { status: Status; external?: boolean }) {
  return (
    <span className="status">
      <span className={`dot ${status}`} />
      {STATUS_TEXT[status]}
      {external && status === "running" ? <span className="faint">· 外部</span> : null}
    </span>
  );
}

export function PowerButton({ model, size }: { model: Model; size?: "lg" }) {
  const { start, stop } = useStore();
  const st = model.runtime.status;
  const busy = st === "starting" || st === "stopping";
  const remote = model.adapter === "openai_remote" && !model.launch.command;
  const cls = st === "running" ? "on" : busy ? "busy" : st === "error" ? "err" : "";
  const title = remote ? "外部服务" : st === "running" ? "停止" : st === "starting" ? "正在启动，点击取消" : "一键启动";
  return (
    <button
      className={`power ${cls} ${size || ""}`}
      title={title}
      disabled={remote || st === "stopping"}
      onClick={(e) => {
        e.stopPropagation();
        if (st === "running" || st === "starting") stop(model.id);
        else start(model.id);
      }}
    >
      {busy ? <Loader2 size={size ? 20 : 16} className="spin" /> : <Power size={size ? 20 : 16} strokeWidth={2.2} />}
    </button>
  );
}

export function ModelAvatar({ model, size }: { model: Pick<Model, "id" | "name" | "color">; size?: "sm" | "lg" }) {
  const c = modelColor(model.id, model.color);
  return (
    <div className={`avatar ${size || ""}`} style={{ background: `linear-gradient(145deg, ${c}, ${c}cc)` }}>
      {initials(model.name)}
    </div>
  );
}

export function Modal({ title, sub, onClose, children, footer, wide }: {
  title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const fn = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`}>
        <div className="modal-head">
          <div style={{ flex: 1 }}>
            <h3 className="modal-title">{title}</h3>
            {sub ? <div className="muted small" style={{ marginTop: 6 }}>{sub}</div> : null}
          </div>
          <button className="btn ghost icon sm" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}

export function Toasts() {
  const { toasts } = useStore();
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.kind === "success" ? <CheckCircle2 size={16} /> : t.kind === "error" ? <AlertCircle size={16} /> : <Info size={16} />}
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function Switch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="switch" onClick={(e) => e.stopPropagation()}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

export function Empty({ icon, title, text, action }: { icon?: ReactNode; title: string; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon || <Boxes size={24} strokeWidth={1.6} />}</div>
      <h3>{title}</h3>
      {text ? <p>{text}</p> : null}
      {action}
    </div>
  );
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </div>
  );
}

export function Slider({ label, value, min, max, step, onChange, format }: {
  label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; format?: (v: number) => string;
}) {
  return (
    <div className="field">
      <span className="field-label" style={{ justifyContent: "space-between" }}>
        {label}
        <span className="mono faint tnum" style={{ fontWeight: 500 }}>{format ? format(value) : value}</span>
      </span>
      <input className="range" type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

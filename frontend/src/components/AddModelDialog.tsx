import { ArrowLeft, FolderSearch, Loader2, ScanSearch, Sparkles } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { DetectResult, Model } from "../lib/types";
import { CapabilityEditor } from "./CapabilityEditor";
import { FileBrowser } from "./FileBrowser";
import { CapChip, Field, Modal, Switch } from "./ui";

function toModel(d: DetectResult): Partial<Model> {
  return {
    name: d.name, path: d.path, adapter: d.adapter, description: d.description, launch: d.launch,
    capabilities: d.capabilities, defaults: d.defaults,
    api_base: d.adapter === "openai_remote" ? d.path : "",
  };
}

export function AddModelDialog({ onClose }: { onClose: () => void }) {
  const { refresh, toast, settings } = useStore();
  const nav = useNavigate();
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [path, setPath] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<DetectResult | null>(null);
  const [batch, setBatch] = useState<DetectResult[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const run = async () => {
    if (!path.trim()) return;
    setBusy(true);
    try {
      if (mode === "single") {
        const d = await api.post<DetectResult>("/api/detect", { path });
        if (!d.exists) toast(d.notes[0] || "路径不存在", "error");
        else setDraft(d);
      } else {
        const list = await api.post<DetectResult[]>("/api/detect-batch", { path });
        setBatch(list);
        setPicked(new Set(list.filter((d) => !d.already_added).map((d) => d.path)));
        if (!list.length) toast("这个文件夹下没有识别到模型", "info");
      }
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const saveOne = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      const m = await api.post<Model>("/api/models", toModel(draft));
      await refresh();
      toast(`已添加 ${m.name}`, "success");
      onClose();
      nav(`/models/${m.id}`);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const saveBatch = async () => {
    if (!batch) return;
    setBusy(true);
    try {
      for (const d of batch.filter((d) => picked.has(d.path))) await api.post("/api/models", toModel(d));
      await refresh();
      toast(`已添加 ${picked.size} 个模型`, "success");
      onClose();
      nav("/models");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const upd = (patch: Partial<DetectResult>) => draft && setDraft({ ...draft, ...patch });
  const updLaunch = (patch: Partial<DetectResult["launch"]>) => draft && setDraft({ ...draft, launch: { ...draft.launch, ...patch } });

  // ---------------------------------------------------------------- review step
  if (draft) {
    return (
      <Modal
        wide
        title={<span className="row"><Sparkles size={18} color="var(--accent)" /> 识别结果：{draft.adapter_label}</span>}
        sub="ModelDock 已经分析了这个文件夹。确认无误后保存，之后随时可以修改。"
        onClose={onClose}
        footer={
          <>
            <button className="btn ghost" onClick={() => setDraft(null)}><ArrowLeft size={15} /> 返回</button>
            <span className="spacer" />
            <button className="btn primary" disabled={busy || !draft.name} onClick={saveOne}>
              {busy && <Loader2 size={15} className="spin" />} 添加到模型库
            </button>
          </>
        }
      >
        <div className="stack" style={{ gap: 28 }}>
          <div className="grid-2">
            <Field label="显示名称"><input className="input" value={draft.name} onChange={(e) => upd({ name: e.target.value })} /></Field>
            <Field label="描述"><input className="input" value={draft.description} onChange={(e) => upd({ description: e.target.value })} /></Field>
          </div>
          {draft.notes.length > 0 && (
            <div className="banner accent" style={{ alignItems: "flex-start", flexDirection: "column", gap: 4 }}>
              {draft.notes.map((n) => <div key={n} className="small">· {n}</div>)}
            </div>
          )}
          <div>
            <div className="section-head" style={{ marginBottom: 12 }}>
              <h4 className="section-title" style={{ fontSize: 14.5 }}>自动识别的能力</h4>
              <span className="section-sub">低可信度的能力默认关闭，可手动打开</span>
            </div>
            <CapabilityEditor caps={draft.capabilities} onChange={(capabilities) => upd({ capabilities })} />
          </div>
          {draft.adapter !== "openai_remote" && (
            <div>
              <h4 className="section-title" style={{ fontSize: 14.5, marginBottom: 12 }}>启动方式</h4>
              <div className="stack" style={{ gap: 16 }}>
                <Field label="启动命令" hint={<>可用占位符：<span className="mono">{"{python} {port} {host} {servers}"}</span></>}>
                  <textarea className="textarea mono" rows={2} value={draft.launch.command} onChange={(e) => updLaunch({ command: e.target.value })} />
                </Field>
                <div className="grid-3">
                  <Field label="端口" hint="留空自动分配">
                    <input className="input mono" value={draft.launch.port ?? ""} placeholder={`自动（${settings?.port_range_start ?? 8100} 起）`}
                      onChange={(e) => updLaunch({ port: e.target.value ? Number(e.target.value) : null })} />
                  </Field>
                  <Field label="就绪检测路径"><input className="input mono" value={draft.launch.health_path} placeholder="/v1/models" onChange={(e) => updLaunch({ health_path: e.target.value })} /></Field>
                  <Field label="停止命令（可选）"><input className="input mono" value={draft.launch.stop_command} placeholder="默认结束进程树" onChange={(e) => updLaunch({ stop_command: e.target.value })} /></Field>
                </div>
              </div>
            </div>
          )}
        </div>
      </Modal>
    );
  }

  // ---------------------------------------------------------------- batch results
  if (batch) {
    return (
      <Modal
        wide
        title={`在文件夹中发现 ${batch.length} 个模型`}
        sub={<span className="mono">{path}</span>}
        onClose={onClose}
        footer={
          <>
            <button className="btn ghost" onClick={() => setBatch(null)}><ArrowLeft size={15} /> 返回</button>
            <span className="spacer" />
            <button className="btn primary" disabled={busy || picked.size === 0} onClick={saveBatch}>
              {busy && <Loader2 size={15} className="spin" />} 添加 {picked.size} 个模型
            </button>
          </>
        }
      >
        <div className="card list">
          {batch.map((d) => (
            <div className="list-row" key={d.path} style={{ opacity: d.already_added ? 0.5 : 1 }}>
              <Switch
                checked={picked.has(d.path)}
                onChange={(v) => {
                  const n = new Set(picked);
                  v ? n.add(d.path) : n.delete(d.path);
                  setPicked(n);
                }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row" style={{ gap: 8 }}>
                  <strong>{d.name}</strong>
                  <span className="chip outline">{d.adapter_label}</span>
                  {d.already_added && <span className="chip">已添加</span>}
                </div>
                <div className="cap-row" style={{ marginTop: 8 }}>
                  {d.capabilities.filter((c) => c.enabled).map((c) => <CapChip key={c.type} type={c.type} />)}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Modal>
    );
  }

  // ---------------------------------------------------------------- path step
  return (
    <Modal
      title="添加模型"
      sub="填写模型所在的文件夹，ModelDock 会自动识别模型类型、可用功能和启动方式。"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={busy || !path.trim()} onClick={run}>
            {busy ? <Loader2 size={15} className="spin" /> : <ScanSearch size={15} />}
            {mode === "single" ? "开始识别" : "扫描文件夹"}
          </button>
        </>
      }
    >
      <div className="stack" style={{ gap: 22 }}>
        <div className="segmented">
          <button className={mode === "single" ? "on" : ""} onClick={() => setMode("single")}>单个模型</button>
          <button className={mode === "batch" ? "on" : ""} onClick={() => setMode("batch")}>批量扫描文件夹</button>
        </div>
        <Field
          label={mode === "single" ? "模型路径" : "包含多个模型的文件夹"}
          hint={mode === "single"
            ? "模型文件夹、整合包目录，或已运行服务的地址（如 http://127.0.0.1:11434）"
            : "例如 D:\\AI\\models —— 会逐个分析其中的子文件夹"}
        >
          <div className="row">
            <input
              className="input mono"
              autoFocus
              value={path}
              placeholder={mode === "single" ? "D:\\AI\\models\\qwen3.5" : "D:\\AI\\models"}
              onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
            <button className="btn" onClick={() => setBrowsing(true)}><FolderSearch size={15} /> 浏览</button>
          </div>
        </Field>
      </div>
      {browsing && (
        <FileBrowser initial={path} onClose={() => setBrowsing(false)} onPick={(p) => { setPath(p); setBrowsing(false); }} />
      )}
    </Modal>
  );
}

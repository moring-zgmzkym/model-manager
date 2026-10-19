import { ArrowLeft, ExternalLink, FolderOpen, Loader2, Play, RefreshCw, RotateCw, Save, Sparkles, Square, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CapabilityEditor } from "../components/CapabilityEditor";
import { LogViewer } from "../components/LogViewer";
import { CapIcon, Empty, Field, ModelAvatar, PowerButton, StatusBadge, Switch } from "../components/ui";
import { api } from "../lib/api";
import { panelsFor } from "../lib/capabilities";
import { bytes, duration } from "../lib/format";
import { useStore } from "../lib/store";
import type { Model } from "../lib/types";

type Tab = "overview" | "capabilities" | "launch" | "logs" | "advanced";

function envToText(env: Record<string, string>) {
  return Object.entries(env).map(([k, v]) => `${k}=${v}`).join("\n");
}
function textToEnv(t: string) {
  const env: Record<string, string> = {};
  t.split("\n").forEach((l) => {
    const i = l.indexOf("=");
    if (i > 0) env[l.slice(0, i).trim()] = l.slice(i + 1).trim();
  });
  return env;
}

export function ModelDetail() {
  const { id } = useParams();
  const { getModel, refresh, toast, catalog } = useStore();
  const nav = useNavigate();
  const model = getModel(id);
  const [tab, setTab] = useState<Tab>("overview");
  const [draft, setDraft] = useState<Model | null>(null);
  const [envText, setEnvText] = useState("");
  const [rawCaps, setRawCaps] = useState("");
  const [saving, setSaving] = useState(false);
  const [rescanning, setRescanning] = useState(false);

  useEffect(() => {
    if (model && (!draft || draft.id !== model.id)) {
      setDraft(model);
      setEnvText(envToText(model.launch.env));
      setRawCaps(JSON.stringify(model.capabilities, null, 2));
    }
  }, [model, draft]);

  if (!model || !draft) {
    return <div className="page"><Empty title="找不到这个模型" action={<button className="btn" onClick={() => nav("/models")}>返回模型库</button>} /></div>;
  }

  const dirty = JSON.stringify({ ...draft, runtime: 0, services: 0, updated_at: 0 }) !==
    JSON.stringify({ ...model, runtime: 0, services: 0, updated_at: 0 }) || envText !== envToText(model.launch.env);

  const save = async (next?: Model) => {
    const body = next || { ...draft, launch: { ...draft.launch, env: textToEnv(envText) } };
    setSaving(true);
    try {
      const saved = await api.put<Model>(`/api/models/${model.id}`, body);
      setDraft(saved);
      setRawCaps(JSON.stringify(saved.capabilities, null, 2));
      await refresh();
      toast("已保存", "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  const rescan = async () => {
    setRescanning(true);
    try {
      const res = await api.post<{ model: Model; notes: string[] }>(`/api/models/${model.id}/rescan`);
      setDraft(res.model);
      setRawCaps(JSON.stringify(res.model.capabilities, null, 2));
      await refresh();
      toast("已重新识别能力", "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setRescanning(false);
    }
  };

  const remove = async () => {
    if (!confirm(`确定从模型库移除 “${model.name}” 吗？模型文件不会被删除。`)) return;
    await api.del(`/api/models/${model.id}`);
    await refresh();
    nav("/models");
  };

  const svcAction = async (ctype: string, action: "start" | "stop") => {
    try {
      await api.post(`/api/models/${model.id}/services/${ctype}/${action}`);
      await refresh();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const L = draft.launch;
  const setL = (patch: Partial<Model["launch"]>) => setDraft({ ...draft, launch: { ...L, ...patch } });
  const rt = model.runtime;
  const panels = panelsFor(model);
  const subServices = model.capabilities.filter((c) => c.enabled && c.binding.command);

  return (
    <div className="page">
      <button className="btn ghost sm" style={{ marginBottom: 24, marginLeft: -10 }} onClick={() => nav("/models")}>
        <ArrowLeft size={14} /> 模型库
      </button>
      <div className="page-head" style={{ alignItems: "center" }}>
        <div className="row" style={{ gap: 20 }}>
          <ModelAvatar model={model} size="lg" />
          <div>
            <h1 className="page-title">{model.name}</h1>
            <div className="row" style={{ marginTop: 8, gap: 14 }}>
              <StatusBadge status={rt.status} external={rt.external} />
              <span className="faint small">{catalog?.adapters[model.adapter] || model.adapter}</span>
              {rt.status === "running" && <span className="faint small">已运行 {duration(rt.started_at)}</span>}
              {rt.memory ? <span className="faint small tnum">内存 {bytes(rt.memory)}</span> : null}
            </div>
          </div>
        </div>
        <div className="row">
          {rt.status === "running" && (
            <button className="btn" onClick={() => api.post(`/api/models/${model.id}/restart`).then(refresh)}><RotateCw size={15} /> 重启</button>
          )}
          <button className="btn primary" disabled={!panels.length} onClick={() => nav(`/studio/${model.id}`)}>
            <Sparkles size={15} /> 打开工作台
          </button>
          <PowerButton model={model} size="lg" />
        </div>
      </div>

      {rt.status === "error" && (
        <div className="banner error" style={{ marginBottom: 28 }}>
          <span style={{ flex: 1 }}>{rt.message}</span>
          <button className="btn sm" onClick={() => setTab("logs")}>查看日志</button>
        </div>
      )}
      {rt.status === "starting" && (
        <div className="banner warn" style={{ marginBottom: 28 }}>
          <Loader2 size={16} className="spin" />
          <span style={{ flex: 1 }}>{rt.message || "正在加载模型"} · 已等待 {duration(rt.started_at)}</span>
          <button className="btn sm" onClick={() => setTab("logs")}>查看日志</button>
        </div>
      )}

      <div className="tabs">
        {([["overview", "概览"], ["capabilities", "能力"], ["launch", "启动配置"], ["logs", "日志"], ["advanced", "高级"]] as [Tab, string][]).map(([t, l]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{l}</button>
        ))}
        <span className="spacer" />
        {dirty && tab !== "logs" && (
          <button className="btn primary sm" style={{ marginBottom: 8 }} disabled={saving} onClick={() => save()}>
            {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />} 保存修改
          </button>
        )}
      </div>

      {tab === "overview" && (
        <div className="stack" style={{ gap: 28 }}>
          <div className="card pad">
            <dl className="kv">
              <dt>模型路径</dt>
              <dd className="row" style={{ gap: 8 }}>
                <span className="mono small">{model.path}</span>
                {!model.path.startsWith("http") && (
                  <button className="btn ghost icon sm" title="打开文件夹" onClick={() => api.post("/api/open-folder", { path: model.path }).catch((e) => toast(e.message, "error"))}>
                    <FolderOpen size={14} />
                  </button>
                )}
              </dd>
              <dt>服务地址</dt>
              <dd className="mono small">{model.base_url}</dd>
              {model.description && (<><dt>描述</dt><dd>{model.description}</dd></>)}
              {model.defaults.model && (<><dt>服务模型名</dt><dd className="mono small">{model.defaults.model}</dd></>)}
              {rt.pid ? (<><dt>进程</dt><dd className="mono small">PID {rt.pid}</dd></>) : null}
            </dl>
          </div>

          <div>
            <div className="section-head">
              <h3 className="section-title">可以做什么</h3>
              <button className="btn ghost sm" onClick={() => setTab("capabilities")}>管理能力</button>
            </div>
            {panels.length === 0 ? (
              <div className="banner">没有启用的能力。到“能力”页面打开或添加。</div>
            ) : (
              <div className="task-grid">
                {model.capabilities.filter((c) => c.enabled).map((c) => {
                  const info = catalog?.capabilities[c.type];
                  return (
                    <div key={c.type + c.label} className="card hover task" onClick={() => nav(`/studio/${model.id}?panel=${info?.panel || "chat"}`)}>
                      <div className="task-icon"><CapIcon type={c.type} size={18} /></div>
                      <div><h4>{c.label || info?.label}</h4><p>{info?.desc}</p></div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {subServices.length > 0 && (
            <div>
              <div className="section-head"><h3 className="section-title">附属服务</h3><span className="section-sub">需要单独启动的界面或工具</span></div>
              <div className="card list">
                {subServices.map((c) => {
                  const st = model.services[c.type]?.status || "stopped";
                  return (
                    <div className="list-row" key={c.type}>
                      <div className="cap-icon"><CapIcon type={c.type} /></div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 580 }}>{c.label || catalog?.capabilities[c.type]?.label}</div>
                        <div className="mono faint small ellipsis">{c.binding.command}</div>
                      </div>
                      <StatusBadge status={st} />
                      {st === "running" && c.binding.url && (
                        <a className="btn sm" href={c.binding.url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> 打开</a>
                      )}
                      {st === "running" || st === "starting" ? (
                        <button className="btn sm" onClick={() => svcAction(c.type, "stop")}><Square size={12} /> 停止</button>
                      ) : (
                        <button className="btn sm" onClick={() => svcAction(c.type, "start")}><Play size={12} /> 启动</button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div>
            <div className="section-head"><h3 className="section-title">最新日志</h3><button className="btn ghost sm" onClick={() => setTab("logs")}>全部</button></div>
            <LogViewer serviceKey={model.id} height={220} />
          </div>
        </div>
      )}

      {tab === "capabilities" && (
        <div className="stack" style={{ gap: 20 }}>
          <div className="row">
            <div className="muted small" style={{ flex: 1 }}>
              能力来自模型文件分析、<span className="mono">modeldock.json</span> 清单和运行时探测。手动修改过的能力会被锁定，重新识别时不会被覆盖。
            </div>
            <button className="btn" disabled={rescanning} onClick={rescan}>
              {rescanning ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}
              {rt.status === "running" ? "重新识别（含运行时探测）" : "重新识别"}
            </button>
          </div>
          <CapabilityEditor caps={draft.capabilities} onChange={(capabilities) => setDraft({ ...draft, capabilities })} />
        </div>
      )}

      {tab === "launch" && (
        <div className="stack" style={{ gap: 24 }}>
          <Field label="启动命令" hint={<>占位符：<span className="mono">{"{python}"}</span> 解释器、<span className="mono">{"{port}"}</span> 端口、<span className="mono">{"{host}"}</span> 地址、<span className="mono">{"{servers}"}</span> 内置服务目录。也可以直接填写 .bat / .sh 启动脚本的路径。</>}>
            <textarea className="textarea mono" rows={3} value={L.command} onChange={(e) => setL({ command: e.target.value })} />
          </Field>
          <Field label="停止命令（可选）" hint="留空时 ModelDock 会结束整个进程树；如果模型有专门的停止脚本，填在这里。">
            <input className="input mono" value={L.stop_command} onChange={(e) => setL({ stop_command: e.target.value })} />
          </Field>
          <div className="grid-2">
            <Field label="工作目录"><input className="input mono" value={L.cwd} onChange={(e) => setL({ cwd: e.target.value })} /></Field>
            <Field label="Python 解释器" hint="留空使用设置中的默认解释器"><input className="input mono" value={L.python} placeholder="例如 D:\conda\envs\qwen\python.exe" onChange={(e) => setL({ python: e.target.value })} /></Field>
          </div>
          <div className="grid-3">
            <Field label="监听地址"><input className="input mono" value={L.host} onChange={(e) => setL({ host: e.target.value })} /></Field>
            <Field label="端口"><input className="input mono" value={L.port ?? ""} onChange={(e) => setL({ port: e.target.value ? Number(e.target.value) : null })} /></Field>
            <Field label="就绪检测路径" hint="留空时只检测端口"><input className="input mono" value={L.health_path} placeholder="/v1/models" onChange={(e) => setL({ health_path: e.target.value })} /></Field>
          </div>
          <div className="grid-2">
            <Field label="启动超时（秒）"><input className="input mono" type="number" value={L.startup_timeout} onChange={(e) => setL({ startup_timeout: Number(e.target.value) })} /></Field>
            <Field label="API 地址覆盖" hint="模型服务不在上面端口时填写，例如 http://127.0.0.1:11434">
              <input className="input mono" value={draft.api_base} onChange={(e) => setDraft({ ...draft, api_base: e.target.value })} />
            </Field>
          </div>
          <Field label="环境变量" hint="每行一个，如 CUDA_VISIBLE_DEVICES=0">
            <textarea className="textarea mono" rows={4} value={envText} onChange={(e) => setEnvText(e.target.value)} />
          </Field>
          <div className="row">
            <Switch checked={L.adopt_external} onChange={(v) => setL({ adopt_external: v })} />
            <div>
              <div style={{ fontWeight: 560 }}>接管外部启动的服务</div>
              <div className="faint small">如果端口上已有服务在运行（比如你手动运行了启动脚本），直接显示为运行中。</div>
            </div>
          </div>
          <div className="row">
            <Switch checked={L.keep_running_on_exit} onChange={(v) => setL({ keep_running_on_exit: v })} />
            <div>
              <div style={{ fontWeight: 560 }}>退出 ModelDock 时保持运行</div>
              <div className="faint small">ComfyUI 这类可能被其他程序共用的服务建议打开，关闭管理器不会结束它。</div>
            </div>
          </div>
        </div>
      )}

      {tab === "logs" && (
        <div className="stack" style={{ gap: 14 }}>
          <div className="row">
            <span className="muted small" style={{ flex: 1 }}>日志同时保存在数据目录的 logs 文件夹中。</span>
            <button className="btn sm" onClick={() => api.del(`/api/logs/${model.id}`).then(() => location.reload())}>清空</button>
          </div>
          <LogViewer serviceKey={model.id} height="calc(100vh - 380px)" />
        </div>
      )}

      {tab === "advanced" && (
        <div className="stack" style={{ gap: 24 }}>
          <div className="grid-2">
            <Field label="显示名称"><input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
            <Field label="描述"><input className="input" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          </div>
          <div className="grid-3">
            <Field label="服务模型名" hint="请求时发送的 model 字段">
              <input className="input mono" value={draft.defaults.model || ""} onChange={(e) => setDraft({ ...draft, defaults: { ...draft.defaults, model: e.target.value } })} />
            </Field>
            <Field label="API Key（可选）">
              <input className="input mono" type="password" value={draft.defaults.api_key || ""} onChange={(e) => setDraft({ ...draft, defaults: { ...draft.defaults, api_key: e.target.value } })} />
            </Field>
            <Field label="主题色">
              <input className="input" type="color" style={{ padding: 4 }} value={draft.color || "#6366f1"} onChange={(e) => setDraft({ ...draft, color: e.target.value })} />
            </Field>
          </div>
          <Field label="能力 JSON" hint="高级用法：直接编辑能力和调用协议（例如自定义 HTTP 接口模板），点击“应用”后再保存。">
            <textarea className="textarea mono" rows={14} value={rawCaps} onChange={(e) => setRawCaps(e.target.value)} />
          </Field>
          <div className="row">
            <button className="btn" onClick={() => {
              try {
                setDraft({ ...draft, capabilities: JSON.parse(rawCaps) });
                toast("已应用，记得保存", "info");
              } catch {
                toast("JSON 格式不正确", "error");
              }
            }}>应用 JSON</button>
            <span className="spacer" />
            <button className="btn danger" onClick={remove}><Trash2 size={15} /> 从模型库移除</button>
          </div>
        </div>
      )}
    </div>
  );
}

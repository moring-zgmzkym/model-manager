import { CheckCircle2, FolderOpen, Puzzle, RefreshCw, Save, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Field, Switch } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Catalog, Settings as S } from "../lib/types";

const MANIFEST_EXAMPLE = `{
  "name": "MiniMind-O",
  "description": "极简全模态模型",
  "launch": {
    "command": "{python} scripts/serve_openai_api.py --port {port}",
    "health_path": "/v1/models"
  },
  "capabilities": ["chat", "vision", "audio_input", "speech_output"]
}`;

const PLUGIN_EXAMPLE = `# plugins/my_engine.py
NAME = "我的推理引擎"

def detect(root, scan):
    if not (root / "my_engine.yaml").exists():
        return None
    return {
        "adapter": "script_project",
        "launch": {"command": "my-engine serve --port {port}", "health_path": "/health"},
        "capabilities": [{"type": "chat"}, {"type": "tts"}],
        "notes": ["由 my_engine 插件识别"],
    }`;

export function Settings() {
  const { settings, refreshSettings, toast, theme, setThemePref, catalog } = useStore();
  const [draft, setDraft] = useState<S | null>(settings);
  const [plugins, setPlugins] = useState<Catalog["plugins"]>(catalog?.plugins || []);
  useEffect(() => setDraft(settings), [settings]);
  useEffect(() => setPlugins(catalog?.plugins || []), [catalog]);
  if (!draft) return null;

  const save = async () => {
    try {
      await api.put("/api/settings", draft);
      await refreshSettings();
      toast("设置已保存", "success");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  return (
    <div className="page" style={{ maxWidth: 880 }}>
      <div className="page-head">
        <div>
          <div className="eyebrow">设置</div>
          <h1 className="page-title">偏好与扩展</h1>
        </div>
        <button className="btn primary" onClick={save}><Save size={15} /> 保存</button>
      </div>

      <div className="stack" style={{ gap: 40 }}>
        <section>
          <h3 className="section-title" style={{ marginBottom: 18 }}>外观</h3>
          <div className="segmented">
            {(["light", "dark", "system"] as const).map((t) => (
              <button key={t} className={(localStorage.getItem("md-theme") || "system") === t ? "on" : ""} onClick={() => setThemePref(t)}>
                {{ light: "浅色", dark: "深色", system: "跟随系统" }[t]}
              </button>
            ))}
          </div>
          <span className="faint small" style={{ marginLeft: 12 }}>当前：{theme === "dark" ? "深色" : "浅色"}</span>
        </section>

        <section className="stack" style={{ gap: 20 }}>
          <h3 className="section-title">运行环境</h3>
          <Field label="默认 Python 解释器" hint={<>启动命令中的 <span className="mono">{"{python}"}</span> 会替换成它。建议指向已安装 torch / transformers 的环境。留空则使用 ModelDock 自身的解释器：<span className="mono">{draft.python_executable}</span></>}>
            <input className="input mono" value={draft.default_python} placeholder="例如 D:\miniconda3\envs\ai\python.exe" onChange={(e) => setDraft({ ...draft, default_python: e.target.value })} />
          </Field>
          <div className="grid-2">
            <Field label="自动分配端口起点"><input className="input mono" type="number" value={draft.port_range_start} onChange={(e) => setDraft({ ...draft, port_range_start: Number(e.target.value) })} /></Field>
            <Field label="数据目录" hint="配置、日志、上传文件和作品">
              <div className="row">
                <input className="input mono" readOnly value={draft.data_dir || ""} />
                <button className="btn icon" onClick={() => api.post("/api/open-folder", {})}><FolderOpen size={15} /></button>
              </div>
            </Field>
          </div>
          <div className="row">
            <Switch checked={draft.stop_models_on_exit} onChange={(v) => setDraft({ ...draft, stop_models_on_exit: v })} />
            <div>
              <div style={{ fontWeight: 560 }}>退出 ModelDock 时关闭由它启动的模型</div>
              <div className="faint small">释放显存；外部启动的服务不受影响。</div>
            </div>
          </div>
        </section>

        <section className="stack" style={{ gap: 16 }}>
          <div className="section-head" style={{ marginBottom: 0 }}>
            <h3 className="section-title">扩展插件</h3>
            <button className="btn sm" onClick={async () => setPlugins(await api.post("/api/plugins/reload"))}><RefreshCw size={13} /> 重新加载</button>
          </div>
          <p className="muted" style={{ margin: 0 }}>
            把 Python 文件放进项目的 <span className="mono">plugins</span> 文件夹即可教 ModelDock 识别新的模型类型。以下划线开头的文件会被忽略。
          </p>
          <div className="card list">
            {plugins.length === 0 && <div className="list-row faint">还没有插件。</div>}
            {plugins.map((p) => (
              <div className="list-row" key={p.file}>
                <Puzzle size={16} className="faint" />
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 560 }}>{p.name || p.file}</div>
                  <div className="faint small">{p.error || p.description || p.file}</div>
                </div>
                {p.ok ? <CheckCircle2 size={16} color="var(--success)" /> : <XCircle size={16} color="var(--danger)" />}
              </div>
            ))}
          </div>
          <details className="card pad">
            <summary style={{ cursor: "pointer", fontWeight: 580 }}>插件示例</summary>
            <pre className="terminal" style={{ padding: 16, marginTop: 14, minHeight: 0 }}>{PLUGIN_EXAMPLE}</pre>
          </details>
        </section>

        <section className="stack" style={{ gap: 16 }}>
          <h3 className="section-title">模型清单 modeldock.json</h3>
          <p className="muted" style={{ margin: 0 }}>
            在任意模型文件夹里放一个 <span className="mono">modeldock.json</span>，ModelDock 会优先采用其中的名称、启动命令和能力声明——最准确的扩展方式。
          </p>
          <pre className="terminal" style={{ padding: 16, minHeight: 0 }}>{MANIFEST_EXAMPLE}</pre>
          <div className="faint small">
            可用能力：{Object.entries(catalog?.capabilities || {}).map(([k, v]) => `${k}（${v.label}）`).join("、")}
          </div>
        </section>
      </div>
    </div>
  );
}

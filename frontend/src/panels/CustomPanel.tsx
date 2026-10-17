import { Code2, Loader2, Play } from "lucide-react";
import { useState } from "react";
import { Empty, Field } from "../components/ui";
import { api, upload } from "../lib/api";
import { useStore } from "../lib/store";
import type { Model, OutputItem } from "../lib/types";

/** Generic form for `custom_http` bindings: fields are declared in the capability binding. */
export function CustomPanel({ model, ready }: { model: Model; ready: boolean }) {
  const { toast } = useStore();
  const customs = model.capabilities.filter((c) => c.binding.protocol === "custom_http");
  const [idx, setIdx] = useState(0);
  const [values, setValues] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ text?: string; json?: unknown; outputs?: OutputItem[]; elapsed?: number } | null>(null);

  if (!customs.length) {
    return <Empty icon={<Code2 size={24} />} title="还没有自定义接口" text="在模型详情 → 高级 → 能力 JSON 中添加 protocol 为 custom_http 的能力，即可在这里调用任意 HTTP 接口。" />;
  }
  const cap = customs[idx];
  const fields = cap.binding.fields?.length ? cap.binding.fields : [{ name: "prompt", label: "输入", type: "textarea" as const }];

  const run = async () => {
    if (!ready) return toast("请先启动模型", "error");
    setBusy(true);
    try {
      setRes(await api.post(`/api/models/${model.id}/custom/${idx}`, values));
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" style={{ maxWidth: 1080 }}>
      {customs.length > 1 && (
        <div className="segmented" style={{ marginBottom: 24 }}>
          {customs.map((c, i) => <button key={i} className={i === idx ? "on" : ""} onClick={() => { setIdx(i); setRes(null); }}>{c.label || `接口 ${i + 1}`}</button>)}
        </div>
      )}
      <div className="panel-split" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="stack" style={{ gap: 18 }}>
          <div className="faint small mono">{(cap.binding.method as string) || "POST"} {(cap.binding.url as string) || `${model.base_url}${cap.binding.path || "/"}`}</div>
          {fields.map((f) => (
            <Field key={f.name} label={f.label || f.name}>
              {f.type === "textarea" ? (
                <textarea className="textarea" rows={5} value={values[f.name] ?? f.default ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
              ) : f.type === "select" ? (
                <select className="select" value={values[f.name] ?? f.default ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}>
                  {(f.options || []).map((o) => <option key={o}>{o}</option>)}
                </select>
              ) : f.type === "file" ? (
                <input type="file" accept={f.accept} onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) setValues({ ...values, [f.name]: (await upload(file, file.name)).id });
                }} />
              ) : (
                <input className="input" type={f.type === "number" ? "number" : "text"} value={values[f.name] ?? f.default ?? ""}
                  onChange={(e) => setValues({ ...values, [f.name]: f.type === "number" ? Number(e.target.value) : e.target.value })} />
              )}
            </Field>
          ))}
          <button className="btn primary" disabled={busy} onClick={run}>{busy ? <Loader2 size={15} className="spin" /> : <Play size={15} />} 调用</button>
        </div>
        <div className="stack" style={{ gap: 14 }}>
          {res?.text && <div className="card pad" style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>{res.text}</div>}
          {res?.outputs?.map((o) => o.kind === "image" ? <img key={o.id} src={o.url} style={{ maxWidth: "100%", borderRadius: 14 }} alt="" /> : <audio key={o.id} controls src={o.url} />)}
          {res?.json !== undefined && <pre className="terminal" style={{ padding: 16, minHeight: 0, maxHeight: 420 }}>{JSON.stringify(res.json, null, 2)}</pre>}
          {!res && <div className="empty"><p className="faint">接口返回会显示在这里。</p></div>}
        </div>
      </div>
    </div>
  );
}

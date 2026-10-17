import { useEffect, useRef, useState } from "react";
import { clock } from "../lib/format";
import { useLogs } from "../lib/store";

function cls(line: string) {
  if (line.startsWith("[ModelDock]")) return "sys";
  if (/error|traceback|exception|失败|错误/i.test(line)) return "err";
  if (/warn/i.test(line)) return "warn";
  return "";
}

export function LogViewer({ serviceKey, filter = "", height }: { serviceKey?: string; filter?: string; height?: number | string }) {
  const [lines] = useLogs(serviceKey);
  const ref = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);
  const shown = filter ? lines.filter((l) => l.line.toLowerCase().includes(filter.toLowerCase())) : lines;

  useEffect(() => {
    if (stick && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [shown.length, stick]);

  return (
    <div
      className="terminal"
      ref={ref}
      style={height ? { height } : undefined}
      onScroll={(e) => {
        const el = e.currentTarget;
        setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
      }}
    >
      {shown.length === 0 ? (
        <div className="terminal-empty">暂无日志。启动模型后，这里会实时显示输出。</div>
      ) : (
        shown.map((l, i) => (
          <div className="ln" key={i}>
            <span className="ts">{clock(l.t)}</span>
            <span className={cls(l.line)}>{l.line}</span>
          </div>
        ))
      )}
    </div>
  );
}

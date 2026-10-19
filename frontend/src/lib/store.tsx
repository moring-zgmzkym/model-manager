import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Catalog, LogLine, Model, Runtime, Settings } from "./types";

type LogListener = (key: string, line: LogLine) => void;

interface Toast {
  id: number;
  kind: "info" | "success" | "error";
  text: string;
}

interface Store {
  models: Model[];
  catalog: Catalog | null;
  settings: Settings | null;
  loading: boolean;
  connected: boolean;
  refresh: () => Promise<void>;
  refreshSettings: () => Promise<void>;
  getModel: (id?: string) => Model | undefined;
  start: (id: string) => Promise<void>;
  stop: (id: string) => Promise<void>;
  onLog: (fn: LogListener) => () => void;
  toast: (text: string, kind?: Toast["kind"]) => void;
  toasts: Toast[];
  theme: "light" | "dark";
  setThemePref: (t: Settings["theme"]) => void;
}

const Ctx = createContext<Store | null>(null);

function systemDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [models, setModels] = useState<Model[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [themePref, setThemePrefState] = useState<Settings["theme"]>(
    (localStorage.getItem("md-theme") as Settings["theme"]) || "system",
  );
  const [dark, setDark] = useState(systemDark());
  const listeners = useRef(new Set<LogListener>());

  const theme = themePref === "system" ? (dark ? "dark" : "light") : themePref;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const fn = () => setDark(mq.matches);
    mq?.addEventListener("change", fn);
    return () => mq?.removeEventListener("change", fn);
  }, []);

  const toast = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 3200);
  }, []);

  const refresh = useCallback(async () => {
    const list = await api.get<Model[]>("/api/models");
    setModels(list);
  }, []);

  const refreshSettings = useCallback(async () => {
    const s = await api.get<Settings>("/api/settings");
    setSettings(s);
  }, []);

  const setThemePref = useCallback(
    (t: Settings["theme"]) => {
      setThemePrefState(t);
      localStorage.setItem("md-theme", t);
      document.documentElement.dataset.theme = t === "system" ? (systemDark() ? "dark" : "light") : t;
    },
    [],
  );

  useEffect(() => {
    Promise.all([refresh(), api.get<Catalog>("/api/catalog").then(setCatalog), refreshSettings()])
      .catch((e) => toast(e.message, "error"))
      .finally(() => setLoading(false));
  }, [refresh, refreshSettings, toast]);

  // live events
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/events`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = window.setTimeout(connect, 2000);
      };
      ws.onmessage = (msg) => {
        const ev = JSON.parse(msg.data);
        if (ev.type === "status") {
          const rt = ev as Runtime & { model_id: string };
          setModels((list) =>
            list.map((m) => {
              if (m.id !== rt.model_id) return m;
              if (rt.key === m.id) return { ...m, runtime: rt };
              const ctype = rt.key.split(":")[1];
              return { ...m, services: { ...m.services, [ctype]: rt } };
            }),
          );
        } else if (ev.type === "model") {
          refresh();
        } else if (ev.type === "log") {
          listeners.current.forEach((fn) => fn(ev.key, { t: ev.t, line: ev.line }));
        }
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [refresh]);

  // gentle polling so memory usage / uptime stay fresh
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") refresh().catch(() => {});
    }, 8000);
    return () => clearInterval(t);
  }, [refresh]);

  const getModel = useCallback((id?: string) => models.find((m) => m.id === id), [models]);

  const start = useCallback(
    async (id: string) => {
      try {
        const rt = await api.post<Runtime>(`/api/models/${id}/start`);
        setModels((list) => list.map((m) => (m.id === id ? { ...m, runtime: { ...m.runtime, ...rt } } : m)));
        if (rt.status === "error") toast(rt.message || "启动失败", "error");
      } catch (e: any) {
        toast(e.message, "error");
      }
    },
    [toast],
  );

  const stop = useCallback(
    async (id: string) => {
      const target = models.find((m) => m.id === id);
      if (target && (target.adapter === "comfyui" || target.launch.keep_running_on_exit)) {
        const ok = window.confirm(
          "停止「" + target.name + "」会中断正在运行的任务（ComfyUI 可能被其他程序共用）。确定停止吗？",
        );
        if (!ok) return;
      }
      setModels((list) => list.map((m) => (m.id === id ? { ...m, runtime: { ...m.runtime, status: "stopping" } } : m)));
      try {
        const rt = await api.post<Runtime>(`/api/models/${id}/stop`);
        setModels((list) => list.map((m) => (m.id === id ? { ...m, runtime: { ...m.runtime, ...rt } } : m)));
      } catch (e: any) {
        toast(e.message, "error");
      }
    },
    [models, toast],
  );

  const onLog = useCallback((fn: LogListener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  const value = useMemo<Store>(
    () => ({
      models, catalog, settings, loading, connected, refresh, refreshSettings, getModel, start, stop, onLog,
      toast, toasts, theme, setThemePref,
    }),
    [models, catalog, settings, loading, connected, refresh, refreshSettings, getModel, start, stop, onLog, toast, toasts, theme, setThemePref],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("StoreProvider missing");
  return s;
}

export function useLogs(key: string | undefined) {
  const { onLog } = useStore();
  const [lines, setLines] = useState<LogLine[]>([]);
  useEffect(() => {
    if (!key) return;
    let alive = true;
    api.get<LogLine[]>(`/api/logs/${encodeURIComponent(key)}`).then((l) => alive && setLines(l)).catch(() => {});
    const off = onLog((k, line) => {
      if (k === key) setLines((prev) => (prev.length > 3000 ? [...prev.slice(-2500), line] : [...prev, line]));
    });
    return () => {
      alive = false;
      off();
    };
  }, [key, onLog]);
  return [lines, setLines] as const;
}

import { Boxes, FolderOpen, Gauge, Layers3, Moon, ScrollText, Settings2, Sparkles, Sun } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useStore } from "../lib/store";

export function Sidebar() {
  const { models, connected, theme, setThemePref } = useStore();
  const running = models.filter((m) => m.runtime.status === "running").length;
  const item = (to: string, icon: React.ReactNode, label: string, extra?: React.ReactNode) => (
    <NavLink to={to} end={to === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
      {icon}
      {label}
      {extra}
    </NavLink>
  );
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><Layers3 size={18} strokeWidth={2} /></div>
        <div>
          <div className="brand-name">ModelDock</div>
          <div className="brand-sub">本地模型管理</div>
        </div>
      </div>
      {item("/", <Gauge size={17} strokeWidth={1.8} />, "总览")}
      {item("/models", <Boxes size={17} strokeWidth={1.8} />, "模型库", <span className="count">{models.length}</span>)}
      {item("/studio", <Sparkles size={17} strokeWidth={1.8} />, "工作台",
        running ? <span className="count live">{running} 运行</span> : null)}
      <div className="nav-label">资源</div>
      {item("/gallery", <FolderOpen size={17} strokeWidth={1.8} />, "作品库")}
      {item("/logs", <ScrollText size={17} strokeWidth={1.8} />, "运行日志")}
      {item("/settings", <Settings2 size={17} strokeWidth={1.8} />, "设置")}
      <div className="sidebar-foot">
        <div className="segmented" style={{ alignSelf: "stretch" }}>
          <button className={theme === "light" ? "on" : ""} style={{ flex: 1, justifyContent: "center" }} onClick={() => setThemePref("light")}>
            <Sun size={14} /> 浅色
          </button>
          <button className={theme === "dark" ? "on" : ""} style={{ flex: 1, justifyContent: "center" }} onClick={() => setThemePref("dark")}>
            <Moon size={14} /> 深色
          </button>
        </div>
        <div className="conn">
          <span className={`dot ${connected ? "running" : "error"}`} />
          {connected ? "后端已连接" : "正在连接后端…"}
        </div>
      </div>
    </aside>
  );
}

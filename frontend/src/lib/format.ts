export function bytes(n?: number | null): string {
  if (!n && n !== 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function duration(sinceSec?: number | null): string {
  if (!sinceSec) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - sinceSec));
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟`;
  const h = Math.floor(s / 3600);
  return h < 24 ? `${h} 小时 ${Math.floor((s % 3600) / 60)} 分` : `${Math.floor(h / 24)} 天`;
}

export function timeAgo(sec: number): string {
  const s = Math.floor(Date.now() / 1000 - sec);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return new Date(sec * 1000).toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

export function clock(sec: number): string {
  return new Date(sec * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

export function greeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 11) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

const PALETTE = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444"];

export function modelColor(id: string, override?: string): string {
  if (override) return override;
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export function initials(name: string): string {
  const clean = name.replace(/[-_.]/g, " ").trim();
  const words = clean.split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return clean.slice(0, 2).toUpperCase();
}

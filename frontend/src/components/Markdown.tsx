import { useMemo } from "react";

function esc(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s: string) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

/** Small, dependency-free markdown renderer: code fences, headings, lists, paragraphs. */
export function renderMarkdown(src: string): string {
  const out: string[] = [];
  const parts = src.split(/```/);
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf("\n");
      const body = nl >= 0 ? part.slice(nl + 1) : part;
      out.push(`<pre><code>${esc(body.replace(/\n$/, ""))}</code></pre>`);
      return;
    }
    const blocks = part.split(/\n{2,}/);
    for (const block of blocks) {
      const lines = block.split("\n").filter((l) => l.trim() !== "");
      if (!lines.length) continue;
      if (lines.every((l) => /^\s*[-*•]\s+/.test(l))) {
        out.push("<ul>" + lines.map((l) => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`).join("") + "</ul>");
      } else if (lines.every((l) => /^\s*\d+[.)]\s+/.test(l))) {
        out.push("<ol>" + lines.map((l) => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>`).join("") + "</ol>");
      } else if (/^#{1,6}\s/.test(lines[0]) && lines.length === 1) {
        const level = Math.min(lines[0].match(/^#+/)![0].length, 3);
        out.push(`<h${level}>${inline(lines[0].replace(/^#+\s*/, ""))}</h${level}>`);
      } else {
        out.push(`<p>${lines.map(inline).join("<br/>")}</p>`);
      }
    }
  });
  return out.join("");
}

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Split `<think>…</think>` reasoning blocks (DeepSeek / Qwen3 style) from the answer. */
export function splitThink(text: string): { think: string; answer: string; open: boolean } {
  const m = text.match(/^\s*<think>([\s\S]*?)(<\/think>|$)([\s\S]*)$/);
  if (!m) return { think: "", answer: text, open: false };
  return { think: m[1].trim(), answer: m[3].trimStart(), open: m[2] !== "</think>" };
}

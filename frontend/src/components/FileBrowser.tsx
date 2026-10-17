import { ArrowUp, Check, File, Folder, HardDrive } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Modal } from "./ui";

interface Listing {
  path: string;
  parent: string | null;
  entries: { name: string; path: string; dir: boolean }[];
}

/** Server-side folder picker: browsers can't reveal absolute paths, the backend can. */
export function FileBrowser({ initial, onPick, onClose, files }: {
  initial?: string; onPick: (path: string) => void; onClose: () => void; files?: boolean;
}) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [err, setErr] = useState("");

  const go = (path: string) => {
    setErr("");
    api.get<Listing>(`/api/fs?path=${encodeURIComponent(path)}`).then(setListing).catch((e) => setErr(e.message));
  };
  useEffect(() => go(initial || ""), [initial]);

  return (
    <Modal
      title={files ? "选择文件" : "选择文件夹"}
      sub="浏览这台电脑上的文件"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          {!files && (
            <button className="btn primary" disabled={!listing?.path} onClick={() => listing && onPick(listing.path)}>
              <Check size={15} /> 选择当前文件夹
            </button>
          )}
        </>
      }
    >
      <div className="browser">
        <div className="browser-path">
          <button className="btn ghost icon sm" disabled={listing?.parent == null} onClick={() => go(listing?.parent ?? "")}>
            <ArrowUp size={15} />
          </button>
          <span className="mono ellipsis">{listing?.path || "此电脑"}</span>
        </div>
        <div className="browser-list">
          {err && <div className="banner error" style={{ margin: 12 }}>{err}</div>}
          {listing?.entries.map((e) => (
            <div
              key={e.path}
              className="browser-item"
              onClick={() => (e.dir ? go(e.path) : files && onPick(e.path))}
              style={!e.dir && !files ? { opacity: 0.45, cursor: "default" } : undefined}
            >
              {listing.path === "" ? <HardDrive size={15} /> : e.dir ? <Folder size={15} color="var(--accent)" /> : <File size={15} />}
              <span className="ellipsis">{e.name}</span>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}

#!/usr/bin/env python3
"""ModelDock launcher.

    python run.py              # start and open in the browser
    python run.py --window     # open in a native window (needs `pip install pywebview`)
    python run.py --no-open    # server only
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))


def ensure_frontend(force: bool = False) -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if dist.exists() and not force:
        return
    npm = shutil.which("npm")
    if not npm:
        print("[ModelDock] 未找到前端构建产物，也没有安装 Node.js。请安装 Node.js 18+ 后重新运行。")
        return
    fe = ROOT / "frontend"
    print("[ModelDock] 首次运行，正在构建界面（只需一次）…")
    if not (fe / "node_modules").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=fe, check=True, shell=os.name == "nt")
    subprocess.run([npm, "run", "build"], cwd=fe, check=True, shell=os.name == "nt")


def main() -> None:
    ap = argparse.ArgumentParser(description="ModelDock 本地模型管理器")
    ap.add_argument("--host", default=os.environ.get("MODELDOCK_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MODELDOCK_PORT", "7777")))
    ap.add_argument("--window", action="store_true", help="使用独立窗口（需要 pywebview）")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--rebuild", action="store_true", help="重新构建前端")
    args = ap.parse_args()

    ensure_frontend(args.rebuild)
    import uvicorn

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    config = uvicorn.Config("modeldock.app:app", host=args.host, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    print(f"[ModelDock] 已启动：{url}")

    if args.window:
        try:
            import webview  # type: ignore
        except ImportError:
            print("[ModelDock] 未安装 pywebview，改为在浏览器中打开。可运行 pip install pywebview 获得独立窗口。")
        else:
            t = threading.Thread(target=server.run, daemon=True)
            t.start()
            while not server.started:
                time.sleep(0.1)
            webview.create_window("ModelDock", url, width=1440, height=920, min_size=(1100, 720))
            webview.start()
            server.should_exit = True
            t.join(timeout=15)
            return

    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    server.run()


if __name__ == "__main__":
    main()

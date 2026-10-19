"""Start, stop and watch model server processes.

A *service* is one process. Every model has a main service keyed by its id;
extra services (for example GPT-SoVITS' training WebUI) are keyed
``"<model id>:<capability>"``.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import psutil

from .paths import SERVERS_DIR, sub_dir

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
IS_WIN = os.name == "nt"


@dataclass
class ServiceSpec:
    key: str
    model_id: str
    name: str
    command: str
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    host: str = "127.0.0.1"
    port: Optional[int] = None
    health_path: str = ""
    startup_timeout: int = 600
    stop_command: str = ""
    adopt_external: bool = True
    python: str = ""


@dataclass
class Service:
    spec: ServiceSpec
    status: str = "stopped"  # stopped | starting | running | stopping | error
    proc: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    started_at: Optional[float] = None
    exit_code: Optional[int] = None
    message: str = ""
    external: bool = False
    logs: deque = field(default_factory=lambda: deque(maxlen=3000))
    health_failures: int = 0
    last_external_check: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        mem = cpu = None
        if self.pid and self.status in ("running", "starting"):
            try:
                procs = [psutil.Process(self.pid)]
                procs += procs[0].children(recursive=True)
                mem = sum(p.memory_info().rss for p in procs)
            except Exception:
                pass
        return {
            "key": self.spec.key, "model_id": self.spec.model_id, "status": self.status, "pid": self.pid,
            "port": self.spec.port, "started_at": self.started_at, "exit_code": self.exit_code,
            "message": self.message, "external": self.external, "memory": mem, "cpu": cpu,
        }


def expand(template: str, values: dict[str, Any]) -> str:
    """Replace {placeholders} we know about; leave everything else (e.g. JSON braces) alone."""
    def repl(m: re.Match) -> str:
        key = m.group(1)
        return str(values[key]) if key in values and values[key] is not None else m.group(0)
    return re.sub(r"\{(\w+)\}", repl, template)


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def kill_tree(pid: int, timeout: float = 8.0) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = root.children(recursive=True) + [root]
    for p in procs:
        try:
            if IS_WIN:
                p.terminate()
            else:
                p.send_signal(signal.SIGTERM)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def pid_on_port(port: int) -> Optional[int]:
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN and conn.pid:
                return conn.pid
    except Exception:
        return None
    return None


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk" if IS_WIN else "latin-1", errors="replace")


class ProcessManager:
    def __init__(self, python_resolver: Callable[[], str]) -> None:
        self.services: dict[str, Service] = {}
        self.python_resolver = python_resolver
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.listeners: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self.on_running: Optional[Callable[[Service], Any]] = None
        self.log_dir = sub_dir("logs")

    # ------------------------------------------------------------------ events
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self.listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.listeners.discard(q)

    def _emit(self, event: dict[str, Any]) -> None:
        for q in list(self.listeners):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def emit_threadsafe(self, event: dict[str, Any]) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._emit, event)

    def _set(self, svc: Service, status: str, message: str = "") -> None:
        changed = svc.status != status or svc.message != message
        svc.status, svc.message = status, message
        if changed:
            self._emit({"type": "status", **svc.snapshot()})
            if status == "running" and self.on_running:
                try:
                    res = self.on_running(svc)
                    if asyncio.iscoroutine(res):
                        asyncio.ensure_future(res)
                except Exception:
                    pass

    def _log(self, svc: Service, line: str, threadsafe: bool = False) -> None:
        entry = {"t": time.time(), "line": line}
        svc.logs.append(entry)
        try:
            with (self.log_dir / f"{svc.spec.key.replace(':', '_')}.log").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        event = {"type": "log", "key": svc.spec.key, **entry}
        if threadsafe:
            self.emit_threadsafe(event)
        else:
            self._emit(event)

    # ------------------------------------------------------------------ lifecycle
    def register(self, spec: ServiceSpec) -> Service:
        svc = self.services.get(spec.key)
        if svc is None:
            svc = Service(spec=spec)
            self.services[spec.key] = svc
        elif svc.status in ("stopped", "error"):
            svc.spec = spec
        return svc

    def get(self, key: str) -> Optional[Service]:
        return self.services.get(key)

    def forget(self, key_prefix: str) -> None:
        for key in [k for k in self.services if k == key_prefix or k.startswith(key_prefix + ":")]:
            self.services.pop(key, None)

    def values(self, spec: ServiceSpec) -> dict[str, Any]:
        return {"python": spec.python or self.python_resolver(), "servers": str(SERVERS_DIR), "host": spec.host,
                "port": spec.port, "name": spec.name}

    async def healthy(self, spec: ServiceSpec) -> bool:
        if not spec.port:
            return True
        if not spec.health_path:
            return await asyncio.to_thread(port_open, spec.host, spec.port)
        host = "127.0.0.1" if spec.host in ("0.0.0.0", "", "::") else spec.host
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                r = await client.get(f"http://{host}:{spec.port}{spec.health_path}")
                return r.status_code < 500
        except Exception:
            return False

    async def start(self, spec: ServiceSpec) -> Service:
        svc = self.register(spec)
        if svc.status in ("running", "starting"):
            return svc
        svc.spec = spec
        if not spec.command.strip():
            self._set(svc, "error", "没有配置启动命令")
            return svc
        if spec.port and await asyncio.to_thread(port_open, spec.host, spec.port):
            if spec.adopt_external and await self.healthy(spec):
                svc.external, svc.started_at, svc.pid = True, time.time(), pid_on_port(spec.port)
                self._log(svc, f"[ModelDock] 端口 {spec.port} 上已有服务在运行，直接接管。")
                self._set(svc, "running", "已接管外部启动的服务")
                return svc
            self._set(svc, "error", f"端口 {spec.port} 已被其他程序占用")
            return svc

        values = self.values(spec)
        command = expand(spec.command, values)
        cwd = expand(spec.cwd, values) or None
        if cwd and not Path(cwd).is_dir():
            self._set(svc, "error", f"工作目录不存在：{cwd}")
            return svc
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
               **{k: expand(v, values) for k, v in spec.env.items()}}
        if spec.port:
            env.setdefault("PORT", str(spec.port))
        kwargs: dict[str, Any] = {}
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        self._log(svc, f"[ModelDock] $ {command}")
        try:
            proc = subprocess.Popen(command, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, **kwargs)
        except Exception as exc:
            self._set(svc, "error", f"启动失败：{exc}")
            return svc
        svc.proc, svc.pid, svc.started_at, svc.exit_code = proc, proc.pid, time.time(), None
        svc.external, svc.health_failures = False, 0
        threading.Thread(target=self._reader, args=(svc, proc), daemon=True).start()
        self._set(svc, "starting", "正在加载模型…")
        return svc

    def _reader(self, svc: Service, proc: subprocess.Popen) -> None:
        buf = b""
        stream = proc.stdout
        assert stream is not None
        while True:
            try:
                chunk = stream.read(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"\r\n|\n|\r", buf)
            buf = parts.pop()
            for raw in parts:
                line = ANSI.sub("", decode(raw)).rstrip()
                if line:
                    self._log(svc, line, threadsafe=True)
        if buf.strip():
            self._log(svc, ANSI.sub("", decode(buf)).rstrip(), threadsafe=True)

    async def stop(self, key: str) -> Optional[Service]:
        svc = self.services.get(key)
        if not svc:
            return None
        if svc.status == "stopped":
            return svc
        self._set(svc, "stopping", "正在停止…")
        spec = svc.spec
        if spec.stop_command.strip():
            cmd = expand(spec.stop_command, self.values(spec))
            self._log(svc, f"[ModelDock] 运行停止命令：{cmd}")
            try:
                await asyncio.to_thread(subprocess.run, cmd, shell=True, cwd=expand(spec.cwd, self.values(spec)) or None,
                                        timeout=60, capture_output=True)
            except Exception as exc:
                self._log(svc, f"[ModelDock] 停止命令出错：{exc}")
        if svc.proc and svc.proc.poll() is None:
            await asyncio.to_thread(kill_tree, svc.proc.pid)
        elif svc.external or (svc.proc and spec.port):
            pid = spec.port and pid_on_port(spec.port)
            if pid and pid != os.getpid():
                await asyncio.to_thread(kill_tree, pid)
        if spec.port:
            for _ in range(20):
                if not await asyncio.to_thread(port_open, spec.host, spec.port, 0.3):
                    break
                await asyncio.sleep(0.25)
        svc.proc, svc.pid, svc.external = None, None, False
        self._log(svc, "[ModelDock] 已停止。")
        svc.last_external_check = time.time()
        self._set(svc, "stopped")
        return svc

    async def stop_all(self, managed_only: bool = True, skip_model_ids: Any = ()) -> None:
        skip = set(skip_model_ids)
        await asyncio.gather(*(self.stop(k) for k, s in self.services.items()
                               if s.status in ("running", "starting") and (s.proc or not managed_only)
                               and s.spec.model_id not in skip),
                             return_exceptions=True)

    # ------------------------------------------------------------------ monitor
    def start_monitor(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._monitor())

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()

    async def _monitor(self) -> None:
        while True:
            try:
                await asyncio.gather(*(self._tick(s) for s in list(self.services.values())))
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(1.5)

    async def _tick(self, svc: Service) -> None:
        spec = svc.spec
        if svc.status == "stopping":
            return
        if svc.proc is not None and svc.proc.poll() is not None:
            code = svc.proc.returncode
            svc.exit_code = code
            svc.proc = None
            if spec.port and await self.healthy(spec):
                # launcher script spawned the real server and exited (common for .bat files)
                svc.external = True
                svc.pid = pid_on_port(spec.port)
                self._log(svc, f"[ModelDock] 启动脚本已退出（{code}），服务仍在端口 {spec.port} 运行。")
                self._set(svc, "running", "")
                return
            if svc.status in ("starting", "running"):
                self._log(svc, f"[ModelDock] 进程已退出，退出码 {code}")
                if code == 0 and not spec.port:
                    self._set(svc, "stopped", "进程已结束")
                else:
                    tail = next((e["line"] for e in reversed(svc.logs) if not e["line"].startswith("[ModelDock]")), "")
                    self._set(svc, "error", f"进程异常退出（{code}）" + (f"：{tail[:160]}" if tail else ""))
            return
        if svc.status == "starting":
            if await self.healthy(spec):
                if not spec.port and time.time() - (svc.started_at or 0) < 3:
                    return
                self._log(svc, "[ModelDock] 服务已就绪。")
                self._set(svc, "running", "")
            elif time.time() - (svc.started_at or 0) > spec.startup_timeout:
                self._set(svc, "starting", f"已等待 {spec.startup_timeout} 秒仍未就绪，请查看日志")
        elif svc.status == "running" and svc.external and svc.proc is None:
            if not await self.healthy(spec):
                svc.health_failures += 1
                if svc.health_failures >= 3:
                    svc.external, svc.pid = False, None
                    self._set(svc, "stopped", "外部服务已关闭")
            else:
                svc.health_failures = 0
        elif svc.status in ("stopped", "error") and spec.adopt_external and spec.port:
            if time.time() - svc.last_external_check > 8:
                svc.last_external_check = time.time()
                if await asyncio.to_thread(port_open, spec.host, spec.port, 0.3) and await self.healthy(spec):
                    svc.external, svc.started_at, svc.pid = True, time.time(), pid_on_port(spec.port)
                    self._set(svc, "running", "检测到外部启动的服务")

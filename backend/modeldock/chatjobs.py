"""Detached chat jobs.

The HTTP response is only a viewer for the job. Generation lives in its own
asyncio task so navigating to the log page, refreshing the browser or losing
the connection does not cancel the model. The frontend can poll the job
snapshot after it comes back and pick up the text that was generated meanwhile.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

MAX_JOBS = 30
JOB_TTL = 30 * 60  # keep finished jobs for 30 minutes so a reload can recover


def sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _decode(raw: bytes | str) -> Optional[dict[str, Any]]:
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    text = text.strip()
    if text.startswith("data:"):
        text = text[5:].strip()
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return None
    return event if isinstance(event, dict) else None


@dataclass
class ChatJob:
    id: str
    model_id: str
    status: str = "running"  # running | done | error | cancelled
    content: str = ""
    reasoning: str = ""
    error: str = ""
    elapsed: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    cancelled: bool = False
    task: Optional[asyncio.Task] = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)

    def apply(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "delta":
            self.content += str(event.get("text") or "")
        elif kind == "reasoning":
            self.reasoning += str(event.get("text") or "")
        elif kind == "error":
            self.error = str(event.get("message") or "生成失败")
        elif kind == "done":
            self.elapsed = float(event.get("elapsed") or (time.time() - self.started))
            self.usage = event.get("usage") or {}

    def snapshot(self) -> dict[str, Any]:
        elapsed = self.elapsed
        if not elapsed and self.finished:
            elapsed = self.finished - self.started
        if self.status == "running":
            elapsed = max(elapsed, time.time() - self.started)
        return {
            "id": self.id,
            "model_id": self.model_id,
            "status": self.status,
            "content": self.content,
            "reasoning": self.reasoning,
            "error": self.error,
            "elapsed": round(elapsed, 2),
            "usage": self.usage,
        }


class ChatJobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, ChatJob] = {}

    def get(self, job_id: str) -> Optional[ChatJob]:
        return self.jobs.get(job_id)

    def create(self, model_id: str, request_id: Optional[str],
               factory: Callable[[], AsyncIterator[bytes]]) -> ChatJob:
        self._prune()
        jid = (request_id or "").strip()
        if not jid or len(jid) > 80 or not all(c.isalnum() or c in "-_" for c in jid):
            jid = uuid.uuid4().hex
        if jid in self.jobs:
            jid = uuid.uuid4().hex
        job = ChatJob(id=jid, model_id=model_id)
        self.jobs[jid] = job
        job.task = asyncio.create_task(self._consume(job, factory()))
        return job

    async def _consume(self, job: ChatJob, source: AsyncIterator[bytes]) -> None:
        saw_done = False
        try:
            async for raw in source:
                event = _decode(raw)
                if event is None:
                    continue
                job.apply(event)
                if event.get("type") == "done":
                    saw_done = True
                self._publish(job, event)
        except asyncio.CancelledError:
            job.cancelled = True
        except Exception as exc:  # a broken stream must still leave a readable snapshot
            job.error = f"生成失败：{exc.__class__.__name__}: {exc}"
        finally:
            if job.status == "running":
                job.status = "cancelled" if job.cancelled else ("error" if job.error else "done")
            job.finished = time.time()
            if not job.elapsed:
                job.elapsed = round(job.finished - job.started, 2)
            if not saw_done:
                event: dict[str, Any] = {"type": "done", "elapsed": job.elapsed, "usage": job.usage}
                if job.error:
                    event["error"] = job.error
                self._publish(job, event)

    @staticmethod
    def _publish(job: ChatJob, event: dict[str, Any]) -> None:
        for queue in list(job.subscribers):
            queue.put_nowait(event)

    async def stream(self, job: ChatJob) -> AsyncIterator[bytes]:
        if job.status != "running":
            if job.reasoning:
                yield sse({"type": "reasoning", "text": job.reasoning})
            if job.content:
                yield sse({"type": "delta", "text": job.content})
            if job.error:
                yield sse({"type": "error", "message": job.error})
            yield sse({"type": "done", "elapsed": job.elapsed, "usage": job.usage})
            return

        queue: asyncio.Queue = asyncio.Queue()
        job.subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                yield sse(event)
                if event.get("type") == "done":
                    break
        finally:
            job.subscribers.discard(queue)

    def cancel(self, job_id: str) -> Optional[ChatJob]:
        job = self.jobs.get(job_id)
        if job and job.status == "running" and job.task:
            job.cancelled = True
            job.task.cancel()
        return job

    def _prune(self) -> None:
        now = time.time()
        for jid, job in list(self.jobs.items()):
            if job.finished and now - job.finished > JOB_TTL:
                self.jobs.pop(jid, None)
        if len(self.jobs) <= MAX_JOBS:
            return
        finished = sorted((j for j in self.jobs.values() if j.finished), key=lambda j: j.finished or 0)
        for job in finished[: len(self.jobs) - MAX_JOBS]:
            self.jobs.pop(job.id, None)

    async def shutdown(self) -> None:
        tasks = [j.task for j in self.jobs.values() if j.task and not j.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.jobs.clear()

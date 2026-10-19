import asyncio

from modeldock import chatjobs


def _delta(text: str) -> bytes:
    return chatjobs.sse({"type": "delta", "text": text})


def test_job_continues_when_viewer_disconnects():
    async def fake():
        for i in range(3):
            yield _delta(str(i))
            await asyncio.sleep(0.03)
        yield chatjobs.sse({"type": "done", "elapsed": 0.1, "usage": {"completion_tokens": 3}})

    async def main():
        manager = chatjobs.ChatJobManager()
        job = manager.create("model-1", "job-disconnect", fake)
        viewer = manager.stream(job)
        first = await viewer.__anext__()
        assert b'"delta"' in first

        # The browser navigated away / refreshed: the viewer goes away.
        await viewer.aclose()
        for _ in range(100):
            if job.status != "running":
                break
            await asyncio.sleep(0.02)

        assert job.status == "done"
        assert job.content == "012"
        assert job.snapshot()["usage"]["completion_tokens"] == 3
        assert not job.subscribers
        await manager.shutdown()

    asyncio.run(main())


def test_cancel_stops_a_running_job():
    started = asyncio.Event()

    async def fake():
        started.set()
        while True:
            yield _delta("x")
            await asyncio.sleep(0.05)

    async def main():
        manager = chatjobs.ChatJobManager()
        job = manager.create("model-1", "job-cancel", fake)
        await asyncio.wait_for(started.wait(), 1)
        manager.cancel(job.id)
        for _ in range(100):
            if job.status != "running":
                break
            await asyncio.sleep(0.02)

        assert job.status == "cancelled"
        assert job.snapshot()["status"] == "cancelled"
        await manager.shutdown()

    asyncio.run(main())


def test_finished_job_can_be_read_after_the_fact():
    async def fake():
        yield chatjobs.sse({"type": "reasoning", "text": "想一想"})
        yield _delta("答案")
        yield chatjobs.sse({"type": "done", "elapsed": 1.2, "usage": {"completion_tokens": 2}})

    async def main():
        manager = chatjobs.ChatJobManager()
        job = manager.create("model-1", "job-late", fake)
        for _ in range(100):
            if job.status != "running":
                break
            await asyncio.sleep(0.01)

        events = [line async for line in manager.stream(job)]
        assert b'"reasoning"' in events[0]
        assert b'"delta"' in events[1]
        assert b'"done"' in events[-1]
        await manager.shutdown()

    asyncio.run(main())

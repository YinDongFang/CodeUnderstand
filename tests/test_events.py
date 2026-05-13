"""cu.events SSE hub 冒烟。"""
from __future__ import annotations

import asyncio

from cu.events import publish_job_stage_event, subscribe


def test_publish_from_worker_thread_delivers_to_async_queue() -> None:
    async def runner() -> None:
        loop = asyncio.get_running_loop()
        q, unsub = subscribe("job-a", loop=loop)

        def emit() -> None:
            publish_job_stage_event("job-a", "conversation", "step:fake")

        await asyncio.to_thread(emit)

        item = await asyncio.wait_for(q.get(), timeout=2.0)
        assert item["stage"] == "conversation"
        assert item["message"] == "step:fake"
        assert "ts" in item
        unsub()

    asyncio.run(runner())

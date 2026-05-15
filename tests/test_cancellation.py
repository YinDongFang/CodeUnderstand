"""节点 task 被外部 cancel：节点状态 CANCELLED，下游 SKIPPED。"""
import asyncio

import pytest

from dagflow import Flow, NodeSkipped, NodeState


async def test_running_task_cancelled_transitions_to_cancelled():
    flow = Flow()
    started = asyncio.Event()

    async def long_running():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise
        return "should not reach"

    async def child(_):
        return "downstream"

    h = flow.submit(long_running)
    h_child = flow.submit(child, h)

    # 等 long_running 真的跑起来
    await started.wait()
    # 拿到那个 task 并 cancel
    assert len(flow._inflight) >= 1
    target_task = next(iter(flow._inflight))
    target_task.cancel()

    await flow.wait_all()

    assert h.state is NodeState.CANCELLED
    assert h_child.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped) as exc_info:
        await h_child
    # __cause__ 应是 CancelledError
    cause = exc_info.value.__cause__
    assert isinstance(cause, asyncio.CancelledError)


async def test_await_handle_of_cancelled_raises_cancelled_error():
    flow = Flow()
    started = asyncio.Event()

    async def long_running():
        started.set()
        await asyncio.sleep(10)

    h = flow.submit(long_running)
    await started.wait()
    next(iter(flow._inflight)).cancel()
    await flow.wait_all()
    assert h.state is NodeState.CANCELLED
    with pytest.raises(asyncio.CancelledError):
        await h

"""wait_all 的各种边界：空 flow、多次调用、wait_all 期间并发 submit 等。"""
import asyncio

import pytest

from dagflow import Flow


async def test_wait_all_on_empty_flow_returns_immediately():
    flow = Flow()
    # 不应挂起；用 wait_for 兜底
    await asyncio.wait_for(flow.wait_all(), timeout=0.5)


async def test_wait_all_is_idempotent():
    flow = Flow()

    async def fn():
        return 1

    h = flow.submit(fn)
    await flow.wait_all()
    await flow.wait_all()  # 第二次应立刻返回
    assert (await h) == 1


async def test_wait_all_waits_for_inflight_node():
    flow = Flow()

    async def slow():
        await asyncio.sleep(0.05)
        return "slow"

    h = flow.submit(slow)
    # wait_all 应等到节点真的完成
    await flow.wait_all()
    assert h.state.name == "DONE"
    assert (await h) == "slow"

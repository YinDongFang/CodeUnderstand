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


async def test_wait_all_waits_for_nodes_added_concurrently():
    flow = Flow()

    async def slow():
        await asyncio.sleep(0.05)
        return "slow"

    async def fast():
        return "fast"

    # 先放一个慢节点
    h_slow = flow.submit(slow)

    # 在 wait_all 期间从另一个 task 加节点
    async def add_more_later():
        await asyncio.sleep(0.02)  # 在 wait_all 启动后
        return flow.submit(fast)

    waiter_task = asyncio.create_task(flow.wait_all())
    h_fast = await add_more_later()

    # wait_all 应等到 fast 也完成
    await waiter_task
    assert h_slow.state.name == "DONE"
    assert h_fast.state.name == "DONE"
    assert (await h_fast) == "fast"


async def test_wait_all_with_all_skipped_branches():
    flow = Flow()

    async def bad():
        raise ValueError("x")

    async def child(_):
        return "unreachable"

    h_bad = flow.submit(bad)
    h_c1 = flow.submit(child, h_bad)
    h_c2 = flow.submit(child, h_c1)

    # 全 SKIPPED/FAILED 也是终态，wait_all 应正常返回
    await asyncio.wait_for(flow.wait_all(), timeout=1.0)
    assert h_bad.state.name == "FAILED"
    assert h_c1.state.name == "SKIPPED"
    assert h_c2.state.name == "SKIPPED"

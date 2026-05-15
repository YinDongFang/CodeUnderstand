"""控制器在运行时基于中间结果动态 submit 后续节点。"""
import asyncio
from functools import partial

import pytest

from dagflow import Flow, NodeState


async def test_controller_branches_on_intermediate_result():
    flow = Flow()

    async def classify():
        return "simple"

    async def quick_path():
        return "quick-ok"

    async def deep_path():
        return "deep-ok"

    probe = flow.submit(classify)
    kind = await probe
    if kind == "simple":
        out = flow.submit(quick_path)
    else:
        out = flow.submit(deep_path)

    await flow.wait_all()
    assert (await out) == "quick-ok"


async def test_submit_when_parent_already_done_schedules_immediately():
    flow = Flow()

    async def parent():
        return "p"

    async def child(x):
        return x + "-c"

    h_parent = flow.submit(parent)
    # 等父完成
    await h_parent
    assert h_parent.state is NodeState.DONE
    # 此时父已 DONE；submit child 后状态应该立刻 READY 或更深（不会停在 PENDING）
    h_child = flow.submit(child, h_parent)
    assert h_child.state is not NodeState.PENDING
    await flow.wait_all()
    assert (await h_child) == "p-c"


async def test_submit_then_wait_all_then_submit_again():
    flow = Flow()

    async def make(n):
        return n

    h1 = flow.submit(partial(make, 1))
    await flow.wait_all()
    assert (await h1) == 1

    h2 = flow.submit(partial(make, 2))
    await flow.wait_all()
    assert (await h2) == 2
    # 第一个 handle 仍然可用，结果不变
    assert (await h1) == 1

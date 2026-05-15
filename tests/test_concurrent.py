"""多独立分支真并行：用 wall time 验证总耗时约等于最长分支，而非串行总和。"""
import asyncio
import time
from functools import partial

import pytest

from dagflow import Flow


async def test_independent_branches_run_in_parallel():
    flow = Flow()

    async def slow_branch(label):
        await asyncio.sleep(0.1)
        return label

    handles = []
    for i in range(5):
        handles.append(flow.submit(partial(slow_branch, f"b{i}")))

    start = time.perf_counter()
    await flow.wait_all()
    elapsed = time.perf_counter() - start

    # 5 个分支各 0.1s；串行需 0.5s 以上，并行应远小于此
    assert elapsed < 0.3, f"expected parallel execution (~0.1s), got {elapsed:.3f}s"
    for h in handles:
        assert h.state.name == "DONE"


async def test_diamond_overlaps_left_and_right_branches():
    flow = Flow()

    async def root():
        return None

    async def left(_):
        await asyncio.sleep(0.1)
        return "L"

    async def right(_):
        await asyncio.sleep(0.1)
        return "R"

    async def merge(a, b):
        return (a, b)

    h_root = flow.submit(root)
    h_left = flow.submit(left, h_root)
    h_right = flow.submit(right, h_root)
    h_merge = flow.submit(merge, h_left, h_right)

    start = time.perf_counter()
    await flow.wait_all()
    elapsed = time.perf_counter() - start

    # left/right 应并行：总耗时约 0.1s，串行将是 0.2s
    assert elapsed < 0.18, f"expected diamond branches parallel, got {elapsed:.3f}s"
    assert (await h_merge) == ("L", "R")

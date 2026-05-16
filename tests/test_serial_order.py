"""节点严格按 submit 顺序串行执行，同一时刻至多一个 RUNNING。"""
import asyncio
from functools import partial

from taskline import Flow


async def test_nodes_run_in_submit_order(state_path):
    flow = Flow(state_path)
    log = []

    async def step(label):
        log.append(label)
        return label

    for i in range(5):
        flow.submit(partial(step, i))
    await flow.wait_all()
    assert log == [0, 1, 2, 3, 4]


async def test_serial_even_with_uneven_sleeps(state_path):
    flow = Flow(state_path)
    log = []

    async def slow(label, delay):
        await asyncio.sleep(delay)
        log.append(label)
        return label

    # 先 submit 的睡得久；若并行会乱序，串行则严格 0,1,2
    flow.submit(partial(slow, 0, 0.05))
    flow.submit(partial(slow, 1, 0.01))
    flow.submit(partial(slow, 2, 0.0))
    await flow.wait_all()
    assert log == [0, 1, 2]


async def test_only_one_node_running_at_a_time(state_path):
    flow = Flow(state_path)
    concurrent = 0
    max_concurrent = 0

    async def watch():
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.01)
        concurrent -= 1
        return None

    for _ in range(4):
        flow.submit(watch)
    await flow.wait_all()
    assert max_concurrent == 1

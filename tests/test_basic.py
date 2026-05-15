"""dagflow 基础行为：NodeState 枚举、Node 数据类，以及后续 Flow 端到端用法。"""
import pytest

from dagflow.node import NodeState, Node


def test_node_state_values():
    assert NodeState.PENDING.value == "pending"
    assert NodeState.READY.value == "ready"
    assert NodeState.RUNNING.value == "running"
    assert NodeState.DONE.value == "done"
    assert NodeState.FAILED.value == "failed"
    assert NodeState.SKIPPED.value == "skipped"
    assert NodeState.CANCELLED.value == "cancelled"


def test_node_state_is_str_enum():
    # str enum: instance is a str, can be compared to strings directly
    assert isinstance(NodeState.DONE, str)
    assert NodeState.DONE == "done"


async def _dummy():
    return 1


def test_node_construction_defaults():
    n = Node(
        id="dummy#0",
        fn=_dummy,
        parent_args=(),
        parent_kwargs={},
        parents=frozenset(),
    )
    assert n.id == "dummy#0"
    assert n.fn is _dummy
    assert n.parent_args == ()
    assert n.parent_kwargs == {}
    assert n.parents == frozenset()
    assert n.state is NodeState.PENDING
    assert n.result is None
    assert n.exception is None
    assert n.future is None  # 由 Flow.submit 在事件循环内赋值


from dagflow import Flow


async def test_single_node_runs_and_returns_result():
    flow = Flow()

    async def make_value():
        return 42

    h = flow.submit(make_value)
    await flow.wait_all()
    assert (await h) == 42
    assert h.state is NodeState.DONE


async def test_single_node_returning_none_is_valid():
    flow = Flow()

    async def returns_none():
        return None

    h = flow.submit(returns_none)
    await flow.wait_all()
    assert (await h) is None
    assert h.state is NodeState.DONE


async def test_await_handle_without_wait_all():
    flow = Flow()

    async def quick():
        return "x"

    h = flow.submit(quick)
    assert (await h) == "x"


async def test_await_handle_repeated():
    flow = Flow()

    async def once():
        return "only"

    h = flow.submit(once)
    await flow.wait_all()
    assert (await h) == "only"
    assert (await h) == "only"


async def test_chain_two_nodes_positional():
    flow = Flow()

    async def head():
        return 7

    async def tail(x):
        return x * 2

    h1 = flow.submit(head)
    h2 = flow.submit(tail, h1)
    await flow.wait_all()
    assert (await h2) == 14


async def test_chain_two_nodes_kwargs():
    flow = Flow()

    async def head():
        return 7

    async def tail(value):
        return value + 1

    h1 = flow.submit(head)
    h2 = flow.submit(tail, value=h1)
    await flow.wait_all()
    assert (await h2) == 8


async def test_diamond_dependency():
    flow = Flow()

    async def root():
        return 10

    async def left(x):
        return x + 1

    async def right(x):
        return x + 2

    async def join(a, b):
        return a * b

    h_root = flow.submit(root)
    h_left = flow.submit(left, h_root)
    h_right = flow.submit(right, h_root)
    h_join = flow.submit(join, h_left, h_right)
    await flow.wait_all()
    assert (await h_join) == 11 * 12  # 132


async def test_multi_parent_positional_varargs():
    flow = Flow()
    from functools import partial

    async def src(n):
        return n

    async def collect(*vals):
        return sum(vals)

    parents = [flow.submit(partial(src, i)) for i in range(5)]
    total = flow.submit(collect, *parents)
    await flow.wait_all()
    assert (await total) == 0 + 1 + 2 + 3 + 4


async def test_same_handle_multiple_times_as_parent():
    flow = Flow()

    async def base():
        return 3

    async def double(a, b):
        return a + b

    h = flow.submit(base)
    twice = flow.submit(double, h, h)
    await flow.wait_all()
    assert (await twice) == 6
    # parents 是 frozenset，去重后只算一个父
    assert len(twice._node.parents) == 1

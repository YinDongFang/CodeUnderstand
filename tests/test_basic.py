"""taskline 基础行为：类型单元测试与 Flow 端到端用法。"""
import pytest

from taskline.errors import StateMismatchError
from taskline.hooks import HookContext
from taskline.node import NodeState, Node


def test_state_mismatch_error_is_runtime_error():
    assert issubclass(StateMismatchError, RuntimeError)
    e = StateMismatchError("diverged")
    assert "diverged" in str(e)


def test_hook_context_before_fields():
    ctx = HookContext(phase="before", node_id="fetch#0", index=0, resuming=True)
    assert ctx.phase == "before"
    assert ctx.node_id == "fetch#0"
    assert ctx.index == 0
    assert ctx.resuming is True
    assert ctx.result is None


def test_hook_context_after_carries_result():
    ctx = HookContext(phase="after", node_id="x#0", index=2,
                      resuming=False, result={"k": 1})
    assert ctx.result == {"k": 1}


def test_node_state_has_exactly_three_values():
    assert NodeState.PENDING.value == "pending"
    assert NodeState.RUNNING.value == "running"
    assert NodeState.DONE.value == "done"
    assert len(list(NodeState)) == 3


def test_node_state_is_str_enum():
    assert isinstance(NodeState.DONE, str)
    assert NodeState.DONE == "done"


async def _dummy():
    return 1


def test_node_construction_defaults():
    n = Node(id="dummy#0", index=0, fn=_dummy, parent_args=(), parent_kwargs={})
    assert n.id == "dummy#0"
    assert n.index == 0
    assert n.fn is _dummy
    assert n.parent_args == ()
    assert n.parent_kwargs == {}
    assert n.state is NodeState.PENDING
    assert n.result is None
    assert n.future is None


import asyncio

from taskline.handle import NodeHandle


def _make_node(node_id: str = "n#0") -> Node:
    async def _fn():
        return 1
    return Node(id=node_id, index=0, fn=_fn, parent_args=(), parent_kwargs={})


def test_handle_id_and_state():
    n = _make_node("foo#3")
    h = NodeHandle(n)
    assert h.id == "foo#3"
    assert h.state is NodeState.PENDING


def test_handle_equality_by_identity():
    n = _make_node()
    h1 = NodeHandle(n)
    h2 = NodeHandle(n)
    assert h1 == h2
    assert hash(h1) == hash(h2)
    d = {h1: "value"}
    assert d[h2] == "value"


def test_handle_inequality_for_different_nodes():
    h1 = NodeHandle(_make_node("a"))
    h2 = NodeHandle(_make_node("b"))
    assert h1 != h2
    assert h1 != "not a handle"


def test_handle_repr_contains_id_and_state():
    h = NodeHandle(_make_node("bar#5"))
    s = repr(h)
    assert "bar#5" in s
    assert "pending" in s


async def test_handle_await_returns_future_result():
    n = _make_node()
    n.future = asyncio.get_running_loop().create_future()
    n.future.set_result(42)
    h = NodeHandle(n)
    assert (await h) == 42


async def test_handle_await_can_be_repeated():
    n = _make_node()
    n.future = asyncio.get_running_loop().create_future()
    n.future.set_result("once")
    h = NodeHandle(n)
    assert (await h) == "once"
    assert (await h) == "once"


from functools import partial

from taskline import Flow


async def test_single_node_runs_and_returns_result(state_path):
    flow = Flow()

    async def make():
        return 42

    h = flow.submit(make)
    await flow.wait_all()
    assert (await h) == 42
    assert h.state is NodeState.DONE


async def test_single_node_returning_none(state_path):
    flow = Flow()

    async def returns_none():
        return None

    h = flow.submit(returns_none)
    await flow.wait_all()
    assert (await h) is None
    assert h.state is NodeState.DONE


async def test_await_handle_without_wait_all(state_path):
    flow = Flow()

    async def quick():
        return "x"

    h = flow.submit(quick)
    assert (await h) == "x"


async def test_chain_passes_parent_result_positional(state_path):
    flow = Flow()

    async def head():
        return 7

    async def tail(x):
        return x * 2

    h1 = flow.submit(head)
    h2 = flow.submit(tail, h1)
    await flow.wait_all()
    assert (await h2) == 14


async def test_chain_passes_parent_result_kwarg(state_path):
    flow = Flow()

    async def head():
        return 7

    async def tail(value):
        return value + 1

    h1 = flow.submit(head)
    h2 = flow.submit(tail, value=h1)
    await flow.wait_all()
    assert (await h2) == 8


async def test_multi_parent(state_path):
    flow = Flow()

    async def src(n):
        return n

    async def add(a, b):
        return a + b

    h1 = flow.submit(partial(src, 3))
    h2 = flow.submit(partial(src, 4))
    h3 = flow.submit(add, h1, h2)
    await flow.wait_all()
    assert (await h3) == 7

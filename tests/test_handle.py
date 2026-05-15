"""dagflow.errors 与 dagflow.handle.NodeHandle 的单元测试。"""
import pytest

from dagflow.errors import NodeFailed, NodeSkipped


def test_node_failed_is_exception():
    assert issubclass(NodeFailed, Exception)
    e = NodeFailed("node-1")
    assert "node-1" in str(e)


def test_node_skipped_is_exception():
    assert issubclass(NodeSkipped, Exception)
    e = NodeSkipped("node-2")
    assert "node-2" in str(e)


def test_node_failed_can_carry_cause():
    inner = ValueError("root cause")
    outer = NodeFailed("n")
    outer.__cause__ = inner
    assert outer.__cause__ is inner


def test_two_failures_distinct_instances():
    a = NodeFailed("a")
    b = NodeFailed("b")
    assert a is not b


import asyncio

from dagflow.node import Node, NodeState
from dagflow.handle import NodeHandle


def _make_node(node_id: str = "n#0") -> Node:
    async def _fn():
        return 1
    return Node(
        id=node_id,
        fn=_fn,
        parent_args=(),
        parent_kwargs={},
        parents=frozenset(),
    )


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
    # 可作 dict key
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


async def test_handle_await_propagates_exception():
    n = _make_node()
    n.future = asyncio.get_running_loop().create_future()
    err = ValueError("boom")
    n.future.set_exception(err)
    h = NodeHandle(n)
    with pytest.raises(ValueError, match="boom"):
        await h


async def test_handle_await_can_be_repeated():
    n = _make_node()
    n.future = asyncio.get_running_loop().create_future()
    n.future.set_result("once")
    h = NodeHandle(n)
    assert (await h) == "once"
    assert (await h) == "once"  # Future 缓存结果

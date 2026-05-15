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

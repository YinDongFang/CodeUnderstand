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

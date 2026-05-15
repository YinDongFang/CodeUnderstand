"""失败传染：FAILED 节点的下游全部 SKIPPED；__cause__ 链能追根因。"""
from functools import partial

import pytest

from dagflow import Flow, NodeFailed, NodeSkipped, NodeState


def _root_cause(e: BaseException) -> BaseException:
    while e.__cause__ is not None:
        e = e.__cause__
    return e


async def test_single_node_failure_raises_node_failed():
    flow = Flow()

    async def bad():
        raise ValueError("kaboom")

    h = flow.submit(bad)
    await flow.wait_all()
    assert h.state is NodeState.FAILED
    with pytest.raises(NodeFailed) as exc_info:
        await h
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "kaboom" in str(exc_info.value.__cause__)


async def test_downstream_is_skipped_when_parent_fails():
    flow = Flow()

    async def bad():
        raise RuntimeError("upstream broke")

    async def child(x):
        return x + 1  # 不应被执行

    h_bad = flow.submit(bad)
    h_child = flow.submit(child, h_bad)
    await flow.wait_all()
    assert h_bad.state is NodeState.FAILED
    assert h_child.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped):
        await h_child


async def test_skip_propagates_through_multiple_layers_and_cause_chains_to_root():
    flow = Flow()

    async def a():
        raise ValueError("root")

    async def b(x):
        return x

    async def c(x):
        return x

    async def d(x):
        return x

    h_a = flow.submit(a)
    h_b = flow.submit(b, h_a)
    h_c = flow.submit(c, h_b)
    h_d = flow.submit(d, h_c)
    await flow.wait_all()
    assert h_a.state is NodeState.FAILED
    assert h_b.state is NodeState.SKIPPED
    assert h_c.state is NodeState.SKIPPED
    assert h_d.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped) as exc_info:
        await h_d
    root = _root_cause(exc_info.value)
    assert isinstance(root, ValueError)
    assert str(root) == "root"


async def test_independent_branch_unaffected_by_failure():
    flow = Flow()

    async def good():
        return "ok"

    async def bad():
        raise ValueError("nope")

    h_good = flow.submit(good)
    h_bad = flow.submit(bad)
    await flow.wait_all()
    assert (await h_good) == "ok"
    assert h_bad.state is NodeState.FAILED
    with pytest.raises(NodeFailed):
        await h_bad


async def test_submit_after_parent_already_failed_marks_skipped_immediately():
    flow = Flow()

    async def bad():
        raise ValueError("late")

    async def child(x):
        return x

    h_bad = flow.submit(bad)
    # 等父跑完
    await flow.wait_all()
    assert h_bad.state.name == "FAILED"
    # 现在 submit 一个依赖它的子，应在 submit 内部立刻 SKIPPED
    h_child = flow.submit(child, h_bad)
    assert h_child.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped):
        await h_child
    # wait_all 应立刻返回
    await flow.wait_all()

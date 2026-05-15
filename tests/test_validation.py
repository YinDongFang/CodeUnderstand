"""submit 参数校验：非 NodeHandle 立即 TypeError，且节点不留半成品。"""
from functools import partial

import pytest

from dagflow import Flow, NodeHandle


async def _noop():
    return None


async def test_submit_rejects_positional_literal():
    flow = Flow()
    with pytest.raises(TypeError, match="positional arg #0"):
        flow.submit(_noop, 42)


async def test_submit_rejects_kwarg_literal():
    flow = Flow()
    with pytest.raises(TypeError, match="'x'"):
        flow.submit(_noop, x="literal")


async def test_submit_rejects_none():
    flow = Flow()
    with pytest.raises(TypeError):
        flow.submit(_noop, None)


async def test_submit_validation_does_not_register_node():
    flow = Flow()
    try:
        flow.submit(_noop, "bad")
    except TypeError:
        pass
    assert len(flow.nodes) == 0


async def test_submit_validation_does_not_increment_id_counter():
    flow = Flow()
    h_ok1 = flow.submit(_noop)
    with pytest.raises(TypeError):
        flow.submit(_noop, "bad")
    h_ok2 = flow.submit(_noop)
    # 计数器没被失败的 submit 污染：连续序号
    assert h_ok1.id == "_noop#0"
    assert h_ok2.id == "_noop#1"


async def test_submit_returns_node_handle():
    flow = Flow()
    h = flow.submit(_noop)
    assert isinstance(h, NodeHandle)
    assert h.id.startswith("_noop#")

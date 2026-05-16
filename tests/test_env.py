"""构造参数与持久化对齐校验。"""
import json

import pytest

from taskline import Flow, StateMismatchError


async def test_missing_state_path_raises():
    # state_path 是必传参数；不传 → Python 自带 TypeError
    with pytest.raises(TypeError, match="state_path"):
        Flow()


async def test_submit_rejects_positional_literal(state_path):
    flow = Flow(state_path)

    async def fn(x):
        return x

    with pytest.raises(TypeError, match="positional arg #0"):
        flow.submit(fn, 42)


async def test_submit_rejects_kwarg_literal(state_path):
    flow = Flow(state_path)

    async def fn(x):
        return x

    with pytest.raises(TypeError, match="'x'"):
        flow.submit(fn, x="literal")


async def test_id_mismatch_raises_state_mismatch_error(state_path):
    # 预置持久化文件，节点 id 与即将 submit 的不一致
    state_path.write_text(
        json.dumps({"version": 1, "nodes": [{"id": "other#0", "result": 1}]}),
        encoding="utf-8",
    )
    flow = Flow(state_path)

    async def fetch():
        return 2

    with pytest.raises(StateMismatchError, match="diverged"):
        flow.submit(fetch)   # 生成 id "fetch#0" != 持久化的 "other#0"


async def test_nodes_property_returns_handles(state_path):
    flow = Flow(state_path)

    async def fn():
        return 1

    h = flow.submit(fn)
    nodes = flow.nodes
    assert len(nodes) == 1
    assert nodes[0] == h

"""失败传播：节点异常穿过 wait_all；未决 handle 不 hang。"""
import asyncio

import pytest

from taskline import Flow


async def test_node_exception_propagates_through_wait_all(state_path):
    flow = Flow(state_path)

    async def bad():
        raise ValueError("kaboom")

    flow.submit(bad)
    with pytest.raises(ValueError, match="kaboom"):
        await flow.wait_all()


async def test_exception_is_not_wrapped(state_path):
    flow = Flow(state_path)

    class MyError(Exception):
        pass

    async def bad():
        raise MyError("original")

    flow.submit(bad)
    with pytest.raises(MyError, match="original"):
        await flow.wait_all()


async def test_done_nodes_persisted_before_crash(state_path):
    import json

    flow = Flow(state_path)

    async def ok():
        return "saved"

    async def bad(x):
        raise RuntimeError("crash")

    h1 = flow.submit(ok)
    flow.submit(bad, h1)
    with pytest.raises(RuntimeError):
        await flow.wait_all()

    # ok 在崩溃前已持久化
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["nodes"] == [{"id": "ok#0", "result": "saved"}]


async def test_pending_handle_await_does_not_hang_on_crash(state_path):
    flow = Flow(state_path)

    async def bad():
        raise ValueError("boom")

    async def later():
        return "never"

    flow.submit(bad)
    h_later = flow.submit(later)   # 永不会运行（bad 先崩）

    # await 未运行节点的 handle 不应 hang —— driver 崩溃时把异常塞进未决 future
    with pytest.raises(ValueError, match="boom"):
        await asyncio.wait_for(h_later, timeout=1.0)
    # 再 await wait_all，取走 driver task 的异常，避免 "Task exception was never retrieved"
    with pytest.raises(ValueError, match="boom"):
        await flow.wait_all()


async def test_non_json_serializable_result_crashes(state_path):
    flow = Flow(state_path)

    async def returns_unserializable():
        return object()   # 非 JSON 可序列化

    flow.submit(returns_unserializable)
    # 持久化时 json.dump 抛 TypeError，穿过 wait_all
    with pytest.raises(TypeError):
        await flow.wait_all()

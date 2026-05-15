"""before/after hook 的调用与上下文。"""
import pytest

from taskline import Flow


async def test_before_and_after_hooks_called_in_order(state_path):
    events = []

    async def before(ctx):
        events.append(("before", ctx.node_id, ctx.phase, ctx.resuming))

    async def after(ctx):
        events.append(("after", ctx.node_id, ctx.phase, ctx.result))

    flow = Flow(before_hook=before, after_hook=after)

    async def step():
        return "R"

    flow.submit(step)
    await flow.wait_all()
    assert events == [
        ("before", "step#0", "before", False),
        ("after", "step#0", "after", "R"),
    ]


async def test_hook_context_index_increments(state_path):
    indices = []

    async def before(ctx):
        indices.append(ctx.index)

    flow = Flow(before_hook=before)

    async def fn():
        return 1

    flow.submit(fn)
    flow.submit(fn)
    flow.submit(fn)
    await flow.wait_all()
    assert indices == [0, 1, 2]


async def test_no_hooks_configured_is_fine(state_path):
    flow = Flow()  # 不配置 hook

    async def fn():
        return 1

    h = flow.submit(fn)
    await flow.wait_all()
    assert (await h) == 1


async def test_before_hook_exception_propagates(state_path):
    async def before(ctx):
        raise ValueError("hook boom")

    flow = Flow(before_hook=before)

    async def fn():
        return 1

    flow.submit(fn)
    with pytest.raises(ValueError, match="hook boom"):
        await flow.wait_all()

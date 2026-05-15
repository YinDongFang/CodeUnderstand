"""崩溃恢复：已完成节点不重跑，从中断节点恢复，resuming 标志正确。"""
import pytest

from taskline import Flow, NodeState


async def test_resume_skips_done_nodes_and_reruns_from_crash(state_path):
    fail = {"on": True}
    calls = []

    async def step0():
        calls.append("step0")
        return 0

    async def step1(prev):
        calls.append("step1")
        return prev + 10

    async def step2(prev):
        calls.append("step2")
        if fail["on"]:
            raise RuntimeError("boom at step2")
        return prev + 100

    # run 1：step2 崩溃
    flow1 = Flow()
    h0 = flow1.submit(step0)
    h1 = flow1.submit(step1, h0)
    flow1.submit(step2, h1)
    with pytest.raises(RuntimeError, match="boom"):
        await flow1.wait_all()
    assert calls == ["step0", "step1", "step2"]

    # 重启：step2 改为成功
    fail["on"] = False
    calls.clear()

    flow2 = Flow()
    h0b = flow2.submit(step0)
    h1b = flow2.submit(step1, h0b)
    h2b = flow2.submit(step2, h1b)
    await flow2.wait_all()
    # step0/step1 从持久化加载，不重跑；只有 step2 重新运行
    assert calls == ["step2"]
    assert h0b.state is NodeState.DONE
    assert h1b.state is NodeState.DONE
    assert (await h2b) == 110


async def test_resuming_flag_only_true_for_resume_point(state_path):
    fail = {"on": True}
    before_seen = []

    async def before(ctx):
        before_seen.append((ctx.node_id, ctx.resuming))

    async def a():
        return 1

    async def b(x):
        if fail["on"]:
            raise RuntimeError("crash")
        return x + 1

    async def c(x):
        return x + 1

    # run 1：节点 b 崩溃
    flow1 = Flow(before_hook=before)
    h_a = flow1.submit(a)
    h_b = flow1.submit(b, h_a)
    flow1.submit(c, h_b)
    with pytest.raises(RuntimeError):
        await flow1.wait_all()
    # run1 是全新运行，所有 before 的 resuming 都是 False
    assert before_seen == [("a#0", False), ("b#0", False)]

    # 重启
    fail["on"] = False
    before_seen.clear()

    flow2 = Flow(before_hook=before)
    h_a2 = flow2.submit(a)
    h_b2 = flow2.submit(b, h_a2)
    flow2.submit(c, h_b2)
    await flow2.wait_all()
    # a 从持久化加载，不触发 hook；b 是恢复点 resuming=True；c 是 False
    assert before_seen == [("b#0", True), ("c#0", False)]


async def test_loaded_nodes_do_not_fire_hooks(state_path):
    fail = {"on": True}
    after_ids = []

    async def after(ctx):
        after_ids.append(ctx.node_id)

    async def a():
        return 1

    async def b(x):
        if fail["on"]:
            raise RuntimeError("crash")
        return x + 1

    flow1 = Flow(after_hook=after)
    h_a = flow1.submit(a)
    flow1.submit(b, h_a)
    with pytest.raises(RuntimeError):
        await flow1.wait_all()
    assert after_ids == ["a#0"]   # 只有 a 完成

    fail["on"] = False
    after_ids.clear()
    flow2 = Flow(after_hook=after)
    h_a2 = flow2.submit(a)
    flow2.submit(b, h_a2)
    await flow2.wait_all()
    # a 从持久化加载，after-hook 不触发；只有 b 触发
    assert after_ids == ["b#0"]

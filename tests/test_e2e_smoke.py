"""端到端冒烟：完整场景 A —— 跑→崩溃→重启→从中断节点恢复→跑完。"""
import json

import pytest

from taskline import Flow, NodeState

# 注意：节点用普通 async def 函数（非 functools.partial）。
# _next_id 取 fn.__name__；partial 没有 __name__ 会退化成 repr，id 不可预测。


async def test_e2e_crash_and_resume(state_path):
    fail = {"on": True}
    calls = []
    resume_flags = []

    async def before(ctx):
        resume_flags.append((ctx.node_id, ctx.resuming))

    async def load_alpha():
        calls.append("load:alpha")
        return {"label": "alpha"}

    async def load_beta():
        calls.append("load:beta")
        return {"label": "beta"}

    async def merge(a, b):
        calls.append("merge")
        return {"merged": [a["label"], b["label"]]}

    async def report(m):
        calls.append("report")
        if fail["on"]:
            raise RuntimeError("report failed")
        return {"summary": m["merged"], "count": len(m["merged"])}

    async def archive(r):
        calls.append("archive")
        return {"archived": r["summary"]}

    # ---- run 1：report 崩溃 ----
    flow1 = Flow(state_path, before_hook=before)
    a1 = flow1.submit(load_alpha)
    b1 = flow1.submit(load_beta)
    m1 = flow1.submit(merge, a1, b1)
    r1 = flow1.submit(report, m1)
    flow1.submit(archive, r1)              # archive 依赖 report

    with pytest.raises(RuntimeError, match="report failed"):
        await flow1.wait_all()

    # load_alpha / load_beta / merge / report 跑过；archive 没跑到
    assert calls == ["load:alpha", "load:beta", "merge", "report"]
    # 持久化文件存了前 3 个成功节点（report 崩溃未持久化）
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert [n["id"] for n in data["nodes"]] == ["load_alpha#0", "load_beta#0", "merge#0"]

    # ---- 重启：report 改为成功 ----
    fail["on"] = False
    calls.clear()
    resume_flags.clear()

    flow2 = Flow(state_path, before_hook=before)
    a2 = flow2.submit(load_alpha)
    b2 = flow2.submit(load_beta)
    m2 = flow2.submit(merge, a2, b2)
    r2 = flow2.submit(report, m2)
    arch2 = flow2.submit(archive, r2)

    await flow2.wait_all()

    # load_alpha / load_beta / merge 从持久化加载，不重跑；只有 report、archive 运行
    assert calls == ["report", "archive"]
    # 恢复点是 report#0，resuming=True；archive#0 是 False；
    # 加载的 3 个节点不触发 before-hook
    assert resume_flags == [("report#0", True), ("archive#0", False)]
    # 全部 DONE
    for h in flow2.nodes:
        assert h.state is NodeState.DONE
    # 最终结果正确
    archived = await arch2
    assert archived == {"archived": ["alpha", "beta"]}
    # report 的结果也对
    assert (await r2) == {"summary": ["alpha", "beta"], "count": 2}

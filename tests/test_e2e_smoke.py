"""端到端冒烟：一个真实形状的小流程，覆盖 DONE/FAILED/SKIPPED 三种终态。"""
from functools import partial

import pytest

from dagflow import Flow, NodeFailed, NodeSkipped, NodeState


def _root_cause(e: BaseException) -> BaseException:
    while e.__cause__ is not None:
        e = e.__cause__
    return e


async def test_e2e_smoke_mixed_outcomes():
    flow = Flow()

    async def load(label):
        return {"label": label, "data": [1, 2, 3]}

    async def load_failing(label):
        raise IOError(f"cannot load {label}")

    async def merge(a, b):
        return {"merged": (a["label"], b["label"])}

    async def transform(payload):
        return {**payload, "transformed": True}

    async def report(left, right):
        return {"summary": (left, right)}

    async def save(payload):
        return {"saved": True, "from": payload.get("label")}

    # 主分支：load_a + load_b -> merge_ab -> report
    h_load_a = flow.submit(partial(load, "a"))
    h_load_b = flow.submit(partial(load, "b"))
    h_merge_ab = flow.submit(merge, h_load_a, h_load_b)

    # 失败分支：load_c (FAILED) -> transform_c (SKIPPED) -> 被 report 依赖
    h_load_c = flow.submit(partial(load_failing, "c"))
    h_transform_c = flow.submit(transform, h_load_c)

    # report 依赖 merge_ab 和 transform_c —— 由于 transform_c 会 SKIPPED，report 也 SKIPPED
    h_report = flow.submit(report, h_merge_ab, h_transform_c)

    # 无关分支：load_d -> transform_d -> save_d，全程成功
    h_load_d = flow.submit(partial(load, "d"))
    h_transform_d = flow.submit(transform, h_load_d)
    h_save_d = flow.submit(save, h_transform_d)

    await flow.wait_all()

    # 状态断言
    assert h_load_a.state is NodeState.DONE
    assert h_load_b.state is NodeState.DONE
    assert h_merge_ab.state is NodeState.DONE

    assert h_load_c.state is NodeState.FAILED
    assert h_transform_c.state is NodeState.SKIPPED
    assert h_report.state is NodeState.SKIPPED

    assert h_load_d.state is NodeState.DONE
    assert h_transform_d.state is NodeState.DONE
    assert h_save_d.state is NodeState.DONE

    # 成功路径结果
    saved = await h_save_d
    assert saved["saved"] is True
    assert saved["from"] == "d"

    # 失败传染：report 抛 NodeSkipped，根因是 IOError
    with pytest.raises(NodeSkipped) as exc_info:
        await h_report
    root = _root_cause(exc_info.value)
    assert isinstance(root, IOError)
    assert "cannot load c" in str(root)

    # 显式 await 其余失败/跳过的 handle 消除 asyncio Future 警告
    with pytest.raises(NodeFailed):
        await h_load_c
    with pytest.raises(NodeSkipped):
        await h_transform_c

    # to_dot 覆盖所有节点和边
    dot = flow.to_dot()
    for h in [h_load_a, h_load_b, h_merge_ab,
              h_load_c, h_transform_c, h_report,
              h_load_d, h_transform_d, h_save_d]:
        assert h.id in dot
    # 边总数：merge_ab(2) + transform_c(1) + report(2) + transform_d(1) + save_d(1) = 7
    assert dot.count(" -> ") == 7

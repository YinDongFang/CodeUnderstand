"""persistence.load_state / save_state 单元测试 + flow 级持久化。"""
import json

from taskline import Flow, NodeState
from taskline.persistence import load_state, save_state


def test_load_missing_file_returns_empty(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) == []


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "state.json")
    data = {"version": 1, "nodes": [{"id": "a#0", "result": 1}]}
    save_state(p, data)
    assert load_state(p) == [{"id": "a#0", "result": 1}]


def test_save_leaves_no_tmp_file(tmp_path):
    save_state(str(tmp_path / "state.json"), {"version": 1, "nodes": []})
    assert (tmp_path / "state.json").exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_save_overwrites_existing(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(p, {"version": 1, "nodes": [{"id": "a#0", "result": 1}]})
    save_state(p, {"version": 1,
                   "nodes": [{"id": "a#0", "result": 1},
                             {"id": "b#0", "result": 2}]})
    assert len(load_state(p)) == 2


def test_load_returns_nodes_list_only(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(p, {"version": 1, "nodes": [{"id": "x#0", "result": "v"}]})
    loaded = load_state(p)
    assert isinstance(loaded, list)
    assert loaded[0]["id"] == "x#0"


async def test_flow_persists_all_done_nodes(state_path):
    flow = Flow()

    async def a():
        return {"v": 1}

    async def b(x):
        return {"v": x["v"] + 1}

    flow.submit(a)
    flow.submit(b, flow.nodes[0])
    await flow.wait_all()

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["nodes"] == [
        {"id": "a#0", "result": {"v": 1}},
        {"id": "b#0", "result": {"v": 2}},
    ]


async def test_flow_persists_incrementally(state_path):
    # 第二个节点运行时，文件里应已有第一个节点
    seen_during_b = []
    flow = Flow()

    async def a():
        return 1

    async def b(x):
        # b 运行时读文件，应看到 a 已持久化
        seen_during_b.append(json.loads(state_path.read_text(encoding="utf-8")))
        return x + 1

    h1 = flow.submit(a)
    flow.submit(b, h1)
    await flow.wait_all()

    assert len(seen_during_b) == 1
    assert seen_during_b[0]["nodes"] == [{"id": "a#0", "result": 1}]


async def test_completed_flow_file_kept_and_rerun_is_idempotent(state_path):
    calls = []

    async def a():
        calls.append("a")
        return 1

    async def b(x):
        calls.append("b")
        return x + 1

    # 第一次跑完
    flow1 = Flow()
    h1 = flow1.submit(a)
    flow1.submit(b, h1)
    await flow1.wait_all()
    assert calls == ["a", "b"]

    # 文件保留；第二次新 Flow 同序列 submit → 全部加载为 DONE，不重跑
    flow2 = Flow()
    h1b = flow2.submit(a)
    h2b = flow2.submit(b, h1b)
    await flow2.wait_all()
    assert calls == ["a", "b"]      # 没有新增调用
    assert h1b.state is NodeState.DONE
    assert (await h2b) == 2

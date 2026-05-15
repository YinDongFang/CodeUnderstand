"""to_dot：输出 graphviz DOT 字符串，包含节点 id、状态颜色、所有边。"""
import pytest

from dagflow import Flow


async def test_to_dot_contains_all_node_ids_and_edges():
    flow = Flow()

    async def a():
        return 1

    async def b(x):
        return x + 1

    async def c(x):
        return x + 2

    h_a = flow.submit(a)
    h_b = flow.submit(b, h_a)
    h_c = flow.submit(c, h_a)
    await flow.wait_all()

    dot = flow.to_dot()
    # 节点 id 都出现
    assert h_a.id in dot
    assert h_b.id in dot
    assert h_c.id in dot
    # 边都出现
    assert f'"{h_a.id}" -> "{h_b.id}"' in dot
    assert f'"{h_a.id}" -> "{h_c.id}"' in dot
    # DOT 头尾
    assert dot.startswith("digraph flow {")
    assert dot.rstrip().endswith("}")


async def test_to_dot_colors_reflect_state():
    flow = Flow()

    async def good():
        return "ok"

    async def bad():
        raise ValueError("x")

    async def child(_):
        return "unreachable"

    h_good = flow.submit(good)
    h_bad = flow.submit(bad)
    h_child = flow.submit(child, h_bad)
    await flow.wait_all()

    dot = flow.to_dot()
    # 三种不同状态对应三种 fillcolor
    assert "palegreen" in dot  # DONE
    assert "lightcoral" in dot  # FAILED
    assert "lightgray" in dot  # SKIPPED

    # 显式 await 失败/跳过的 handle 消除 asyncio Future 警告
    from dagflow import NodeFailed, NodeSkipped
    with pytest.raises(NodeFailed):
        await h_bad
    with pytest.raises(NodeSkipped):
        await h_child


async def test_to_dot_on_empty_flow():
    flow = Flow()
    dot = flow.to_dot()
    assert dot.startswith("digraph flow {")
    assert "->" not in dot  # 无边

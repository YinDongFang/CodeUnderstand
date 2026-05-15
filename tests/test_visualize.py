"""to_dot：输出 graphviz DOT 字符串。"""
from functools import partial

from taskline import Flow


async def test_to_dot_contains_nodes_and_edges(state_path):
    flow = Flow()

    async def a():
        return 1

    async def b(x):
        return x + 1

    h1 = flow.submit(a)
    flow.submit(b, h1)
    await flow.wait_all()

    dot = flow.to_dot()
    assert dot.startswith("digraph flow {")
    assert dot.rstrip().endswith("}")
    assert '"a#0"' in dot
    assert '"b#0"' in dot
    assert '"a#0" -> "b#0"' in dot


async def test_to_dot_colors_done_nodes(state_path):
    flow = Flow()

    async def a():
        return 1

    flow.submit(a)
    await flow.wait_all()
    dot = flow.to_dot()
    assert "palegreen" in dot   # DONE


async def test_to_dot_on_empty_flow(state_path):
    flow = Flow()
    dot = flow.to_dot()
    assert dot.startswith("digraph flow {")
    assert "->" not in dot

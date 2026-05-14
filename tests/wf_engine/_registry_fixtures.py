"""Registry used by subprocess worker integration tests."""

from __future__ import annotations

from wf_engine.context import NodeContext
from wf_engine.engine import Engine
from wf_engine.workflow import Workflow


def register_all(engine: Engine) -> None:
    wf = Workflow(key="supervisor_spawn_wf")

    def n1(ctx: NodeContext) -> None:
        (ctx.node_workdir / "touched.txt").write_text("ok", encoding="utf-8")

    wf.add_node("n1", n1, whitelist=["touched.txt"])
    engine.register_workflow(wf)

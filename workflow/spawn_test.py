"""Minimal workflow used by supervisor spawn integration test."""
from __future__ import annotations

import logging

from wf_engine.context import NodeContext
from wf_engine.engine import Engine
from wf_engine.workflow import Workflow


def register_all(engine: Engine) -> None:
    wf = Workflow(key="supervisor_spawn_wf")

    def n1(ctx: NodeContext) -> None:
        logging.getLogger(__name__).info("supervisor_spawn_n1")
        (ctx.node_workdir / "touched.txt").write_text("ok", encoding="utf-8")

    wf.add_node("n1", n1, whitelist=["touched.txt"])
    engine.register_workflow(wf)

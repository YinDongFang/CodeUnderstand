"""Minimal workflow used by supervisor spawn integration test."""

from __future__ import annotations

import logging

from wf_engine import NodeContext

WORKFLOW_KEY = "supervisor_spawn_wf"


def n1(ctx: NodeContext) -> None:
    logging.getLogger(__name__).info("supervisor_spawn_n1")
    (ctx.node_workdir / "touched.txt").write_text("ok", encoding="utf-8")


def get_nodes() -> list[dict]:
    return [{"id": "n1", "fn": n1, "whitelist": ["touched.txt"]}]

"""Stable substrings written to ``task.log`` so clients can slice by worker execution."""

from __future__ import annotations

# Logged at the start of each ``run_once`` invocation (one worker / execution pass).
RUN_BEGIN_PREFIX = "WF_ENGINE_RUN_BEGIN"


def format_run_begin(*, generation: int, pid: int, task_id: str) -> str:
    """Single-line message body (after the ``|``-separated logging prefix)."""
    return f"{RUN_BEGIN_PREFIX} generation={generation} pid={pid} task_id={task_id}"

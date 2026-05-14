"""Spawn worker subprocesses for task execution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def spawn_worker(
    *,
    db_path: Path | str,
    task_id: str,
    task_root: Path | str,
    workflow_key: str,
    registry_module: str | None = None,
    extra_env: dict[str, str] | None = None,
    cwd: Path | str | None = None,
    **popen_kwargs: Any,
) -> subprocess.Popen:
    """Run ``python -m wf_engine.worker_main`` with engine env vars set."""
    merged = os.environ.copy()
    merged["WF_ENGINE_DB_PATH"] = str(Path(db_path).resolve())
    merged["WF_ENGINE_TASK_ID"] = task_id
    merged["WF_ENGINE_TASK_ROOT"] = str(Path(task_root).resolve())
    merged["WF_ENGINE_WORKFLOW_KEY"] = workflow_key
    if registry_module:
        merged["WF_ENGINE_REGISTRY_MODULE"] = registry_module
    if extra_env:
        merged.update(extra_env)

    cmd = [sys.executable, "-m", "wf_engine.worker_main"]
    return subprocess.Popen(cmd, env=merged, cwd=cwd, **popen_kwargs)

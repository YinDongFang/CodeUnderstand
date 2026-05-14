"""Subprocess entry: ``python -m wf_engine.worker_main``."""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wf_engine.engine import Engine
from wf_engine.runner import run_once
from wf_engine.store.sqlite import SqliteStore


def _lease_until_iso(*, seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _load_registry(engine: Engine) -> None:
    mod_path = os.environ.get("WF_ENGINE_REGISTRY_MODULE")
    if not mod_path:
        return
    mod = importlib.import_module(mod_path)
    register_all = getattr(mod, "register_all", None)
    if register_all is None:
        msg = f"registry module {mod_path!r} has no register_all(engine: Engine)"
        raise RuntimeError(msg)
    register_all(engine)


def main() -> int:
    try:
        db_path = Path(os.environ["WF_ENGINE_DB_PATH"])
        task_id = os.environ["WF_ENGINE_TASK_ID"]
        task_root = Path(os.environ["WF_ENGINE_TASK_ROOT"])
        workflow_key = os.environ["WF_ENGINE_WORKFLOW_KEY"]

        engine = Engine()
        _load_registry(engine)
        workflow = engine.get_workflow(workflow_key)

        store = SqliteStore(db_path)
        pid = os.getpid()
        lease_until = _lease_until_iso(seconds=60)
        store.acquire_lease(task_id, pid, lease_until)
        try:
            run_once(
                store=store,
                workflow=workflow,
                task_id=task_id,
                task_root=task_root,
            )
        finally:
            store.release_lease(task_id)
        return 0
    except Exception as e:  # noqa: BLE001 — top-level worker boundary
        print(f"wf_engine.worker_main: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

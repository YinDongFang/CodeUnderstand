"""Subprocess entry: ``python -m wf_engine.worker_main``."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import traceback
from pathlib import Path

from wf_engine.engine import Engine
from wf_engine.lease_util import utc_iso_after
from wf_engine.paths import task_layout
from wf_engine.runner import run_once, wf_log_node
from wf_engine.store.sqlite import SqliteStore


class _WfNodeLogFilter(logging.Filter):
    """Populate ``record.wf_node`` from runner context for task.log formatting."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — logging.Filter API
        node = wf_log_node.get()
        setattr(record, "wf_node", node)
        return True


def _configure_task_file_logging(task_root: Path) -> None:
    """Append worker / node logs to ``<task_root>/logs/task.log`` (API tail target)."""
    log_dir = task_layout(task_root).logs
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "task.log"
    fmt = "%(asctime)s | %(levelname)s | %(wf_node)s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.addFilter(_WfNodeLogFilter())
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


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
        _configure_task_file_logging(task_root)
        logging.getLogger(__name__).info(
            "worker start pid=%s task_id=%s workflow_key=%s",
            pid,
            task_id,
            workflow_key,
        )
        lease_ttl = int(os.environ.get("WF_ENGINE_LEASE_TTL_SECONDS", "300"))
        store.acquire_lease(task_id, pid, utc_iso_after(seconds=lease_ttl))
        try:
            run_once(
                store=store,
                workflow=workflow,
                task_id=task_id,
                task_root=task_root,
                worker_pid=pid,
                lease_ttl_seconds=lease_ttl,
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

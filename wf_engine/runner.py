from __future__ import annotations

import contextvars
import copy
import os
from datetime import datetime
from pathlib import Path

from wf_engine import status as S
from wf_engine.context import NodeContext
from wf_engine.interrupt import ControlledInterrupt
from wf_engine.utils.archives import (
    WhitelistPackError,
    pack_whitelist_zip,
    warn_extraneous_workspace_files,
)
from wf_engine.utils.lease import utc_iso_after
from wf_engine.utils.log_markers import format_run_begin
from wf_engine.utils.sandbox import resolve_node_workdir
from wf_engine.utils.task_layout import task_layout
from wf_engine.store.sqlite import SqliteStore, _utc_iso
from wf_engine.workflow import Workflow

wf_log_node: contextvars.ContextVar[str] = contextvars.ContextVar("wf_log_node", default="")


def _append_task_log_line(
    task_root: Path, *, logger_name: str, level: str, message: str
) -> None:
    """Append one line in the same pipe shape as ``worker_main`` file logging."""
    log_dir = task_layout(task_root).logs
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "task.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {level} |  | {logger_name} | {message}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def run_once(
    *,
    store: SqliteStore,
    workflow: Workflow,
    task_id: str,
    task_root: Path,
    worker_pid: int | None = None,
    lease_ttl_seconds: int = 300,
) -> None:
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if task["status"] == S.TASK_WAITING_HUMAN:
        return

    snapshot_input = copy.deepcopy(task["input_json"])
    shared_context = copy.deepcopy(task.get("context_json") or {})

    layout = task_layout(task_root)
    layout.workspace.mkdir(parents=True, exist_ok=True)
    layout.zips.mkdir(parents=True, exist_ok=True)

    base = store.get_console_settings()
    settings_snapshot = {
        "root": base["root"],
        "cookie": base["cookie"],
        "authorization": base["authorization"],
        "task_parent_dir": str(task_root.resolve().parent),
    }

    pid = worker_pid if worker_pid is not None else os.getpid()
    _append_task_log_line(
        task_root,
        logger_name="wf_engine.runner",
        level="INFO",
        message=format_run_begin(
            round=int(task.get("execution_count") or 0),
            generation=int(task["worker_generation"]),
            pid=pid,
            task_id=task_id,
        ),
    )

    node_rows = store.list_nodes(task_id)
    num = len(workflow.nodes)
    if len(node_rows) != num:
        msg = f"task node count {len(node_rows)} != workflow node count {num}"
        raise ValueError(msg)

    for row in node_rows:
        ordinal = int(row["ordinal"])
        if row["status"] == S.NODE_SUCCESS:
            continue

        spec = workflow.nodes[ordinal]
        if spec.id != row["node_id"]:
            msg = f"node id mismatch at ordinal {ordinal}: db={row['node_id']!r} wf={spec.id!r}"
            raise ValueError(msg)

        t = store.get_task(task_id)
        assert t is not None
        human_input: dict | None = None
        if (
            t["interrupt_response_payload"] is not None
            and not t["interrupt_response_consumed"]
            and t.get("interrupt_node_id") == spec.id
        ):
            human_input = t["interrupt_response_payload"]

        cur = store.get_task(task_id)
        assert cur is not None
        if cur["status"] != S.TASK_RUNNING:
            store.set_task_status(task_id, S.TASK_RUNNING)

        now = _utc_iso()
        store.update_node(task_id, ordinal, status=S.NODE_RUNNING, started_at=now)

        if worker_pid is not None:
            store.acquire_lease(
                task_id,
                worker_pid,
                utc_iso_after(seconds=lease_ttl_seconds),
            )

        try:
            node_workdir = resolve_node_workdir(layout.workspace, spec.workdir_relative)
        except ValueError as e:
            store.update_node(
                task_id,
                ordinal,
                status=S.NODE_FAILED,
                finished_at=_utc_iso(),
                error_json={"category": "validation", "message": str(e)},
            )
            store.set_task_status(task_id, S.TASK_FAILED)
            store.release_lease(task_id)
            return

        node_workdir.mkdir(parents=True, exist_ok=True)

        ctx = NodeContext(
            task_id=task_id,
            node_id=spec.id,
            workflow_key=workflow.key,
            task_root=task_root,
            workspace=layout.workspace,
            node_workdir=node_workdir,
            human_input=human_input,
            input=snapshot_input,
            context=shared_context,
            settings=settings_snapshot,
        )
        injected_human = human_input is not None

        wf_log_node.set(f"[{ordinal}:{spec.id}]")
        try:
            try:
                spec.fn(ctx)
            except ControlledInterrupt as c:
                store.save_task_context(task_id, shared_context)
                store.open_interrupt(
                    task_id,
                    node_id=spec.id,
                    expected_schema=c.expected_schema,
                    ui=c.ui,
                    checkpoint=c.checkpoint,
                )
                store.update_node(
                    task_id,
                    ordinal,
                    status=S.NODE_WAITING_HUMAN,
                    clear_finished_at=True,
                )
                store.release_lease(task_id)
                return
            except Exception as e:
                store.save_task_context(task_id, shared_context)
                store.update_node(
                    task_id,
                    ordinal,
                    status=S.NODE_FAILED,
                    finished_at=_utc_iso(),
                    error_json={"category": "business", "message": str(e)},
                )
                store.set_task_status(task_id, S.TASK_FAILED)
                store.release_lease(task_id)
                return
            else:
                store.save_task_context(task_id, shared_context)
        finally:
            wf_log_node.set("")

        globs = tuple(spec.whitelist_globs)
        dest_zip = layout.zips / f"{ordinal}_{spec.id}.zip"
        if globs:
            try:
                packed_paths = pack_whitelist_zip(
                    parent=node_workdir, include_globs=globs, dest_zip=dest_zip
                )
            except WhitelistPackError as e:
                store.update_node(
                    task_id,
                    ordinal,
                    status=S.NODE_FAILED,
                    finished_at=_utc_iso(),
                    error_json={"category": "validation", "message": str(e)},
                )
                store.set_task_status(task_id, S.TASK_FAILED)
                store.release_lease(task_id)
                return
            warn_extraneous_workspace_files(
                node_workdir=node_workdir, packed_rel_paths=packed_paths
            )
            zip_path_str = str(dest_zip)
        else:
            zip_path_str = None

        fin = _utc_iso()
        if zip_path_str is not None:
            store.update_node(
                task_id,
                ordinal,
                status=S.NODE_SUCCESS,
                finished_at=fin,
                zip_path=zip_path_str,
            )
        else:
            store.update_node(
                task_id,
                ordinal,
                status=S.NODE_SUCCESS,
                finished_at=fin,
            )

        if injected_human:
            store.consume_interrupt_response(task_id)

        if ordinal == num - 1:
            store.set_task_status(task_id, S.TASK_SUCCEEDED)

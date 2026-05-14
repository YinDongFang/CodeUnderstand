from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, Field

from wf_engine import status as S
from wf_engine.utils.lease import parse_utc_iso, pid_alive
from wf_engine.utils.task_layout import task_layout
from wf_engine.utils.sandbox import resolve_node_workdir
from wf_engine.server.paths_util import effective_tasks_root
from wf_engine.server.state import ControlPlaneState
from wf_engine.task_timing import compute_active_duration_seconds
from wf_engine.utils.archives import UnsafeArchiveError, extract_zip_bytes

router = APIRouter()


class CreateTaskBody(BaseModel):
    workflow_key: str
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class CreateTaskResponse(BaseModel):
    task_id: str


class ResolveInterruptBody(BaseModel):
    interrupt_seq: int | None = None
    payload: dict[str, Any]


class RerunBody(BaseModel):
    from_node_id: str


class LogsResponse(BaseModel):
    lines: list[str]
    next_cursor: int


def _cp(request: Request) -> ControlPlaneState:
    return request.app.state.cp


def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _worker_lease_active(row: dict[str, Any]) -> bool:
    if row.get("status") != S.TASK_RUNNING:
        return False
    lu = row.get("lease_until")
    lease_ok = False
    if lu:
        try:
            lease_ok = datetime.now(timezone.utc) <= parse_utc_iso(str(lu))
        except ValueError:
            lease_ok = False
    pid = row.get("worker_pid")
    pid_ok = pid is not None and pid_alive(int(pid))
    return lease_ok and pid_ok


def _clear_workspace_contents(workspace: Path) -> None:
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        return
    for child in workspace.iterdir():
        if child.is_file() or child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            shutil.rmtree(child)


def _task_root(cp: ControlPlaneState, task_id: str) -> Path:
    row = cp.store.get_task(task_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=_err("not_found", "task not found"),
        )
    return Path(row["tasks_root"]) / task_id


def _serialize_node(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "node_id": row["node_id"],
        "ordinal": row["ordinal"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "zip_path": row["zip_path"],
    }
    err = row.get("error_json")
    if err:
        out["error"] = err
    return out


def _active_duration_seconds(row: dict[str, Any]) -> int:
    return compute_active_duration_seconds(
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        status=str(row["status"]),
        interrupt_wall_seconds_accumulated=int(
            row.get("interrupt_wall_seconds_accumulated") or 0
        ),
        waiting_human_since=row.get("waiting_human_since"),
        now=datetime.now(timezone.utc),
    )


def _serialize_task_detail(row: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row["id"],
        "name": row.get("name"),
        "workflow_key": row["workflow_key"],
        "workflow_revision": row["workflow_revision"],
        "status": row["status"],
        "input": row["input_json"],
        "context": row.get("context_json") or {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "worker_generation": row["worker_generation"],
        "execution_count": int(row.get("execution_count") or 0),
        "active_duration_seconds": _active_duration_seconds(row),
        "nodes": [_serialize_node(n) for n in nodes],
    }
    if row["status"] == S.TASK_WAITING_HUMAN:
        body["interrupt"] = {
            "seq": row["interrupt_seq"],
            "node_id": row["interrupt_node_id"],
            "expected_schema": row["interrupt_expected_schema"],
            "request_extras": row["interrupt_request_extras"],
            "checkpoint": row["interrupt_checkpoint"],
        }
    return body


def _spawn_for_task(
    cp: ControlPlaneState,
    *,
    task_id: str,
    workflow_key: str,
) -> None:
    cp.spawn_worker_fn(
        db_path=cp.store.db_path,
        task_id=task_id,
        task_root=_task_root(cp, task_id),
        workflow_key=workflow_key,
        registry_module=cp.registry_module,
    )


@router.post("/tasks", status_code=201, response_model=CreateTaskResponse)
def create_task(request: Request, body: CreateTaskBody) -> CreateTaskResponse:
    cp = _cp(request)
    try:
        wf = cp.engine.get_workflow(body.workflow_key)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=_err("unknown_workflow", f"workflow_key not registered: {body.workflow_key!r}"),
        ) from None

    raw_name = body.name.strip() or None
    tasks_base = effective_tasks_root(cp)
    try:
        tid = cp.store.create_task(
            workflow_key=wf.key,
            workflow_revision=wf.revision,
            input_obj=body.input,
            name=raw_name,
            context_obj=body.context,
            tasks_root=str(tasks_base),
        )
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if "tasks.name" in msg or "idx_tasks_name_unique" in msg:
            raise HTTPException(
                status_code=409,
                detail=_err(
                    "duplicate_task_name",
                    f"task name already exists: {raw_name!r}",
                ),
            ) from e
        raise
    root = tasks_base / tid
    layout = task_layout(root)
    layout.workspace.mkdir(parents=True, exist_ok=True)
    layout.zips.mkdir(parents=True, exist_ok=True)
    layout.logs.mkdir(parents=True, exist_ok=True)

    cp.store.init_task_nodes(tid, [n.id for n in wf.nodes])
    cp.store.set_task_status(tid, S.TASK_RUNNING)
    cp.store.mark_first_run_scheduled(tid)
    _spawn_for_task(cp, task_id=tid, workflow_key=wf.key)
    return CreateTaskResponse(task_id=tid)


@router.get("/tasks")
def list_tasks(request: Request) -> list[dict[str, Any]]:
    cp = _cp(request)
    cp.store.reconcile_all_running_tasks()
    rows = cp.store.list_tasks()
    return [
        {
            "id": r["id"],
            "name": r.get("name"),
            "status": r["status"],
            "workflow_key": r["workflow_key"],
            "created_at": r["created_at"],
            "execution_count": int(r.get("execution_count") or 0),
            "active_duration_seconds": _active_duration_seconds(r),
        }
        for r in rows
    ]


@router.get("/workflows")
def list_workflows(request: Request) -> list[dict[str, Any]]:
    cp = _cp(request)
    return cp.engine.list_workflows()


@router.get("/tasks/{task_id}")
def get_task(request: Request, task_id: str) -> dict[str, Any]:
    cp = _cp(request)
    cp.store.reconcile_stale_worker_for_task(task_id)
    row = cp.store.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_err("not_found", "task not found"))
    nodes = cp.store.list_nodes(task_id)
    return _serialize_task_detail(row, nodes)


@router.get("/tasks/{task_id}/logs", response_model=LogsResponse)
def get_task_logs(
    request: Request,
    task_id: str,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> LogsResponse:
    cp = _cp(request)
    cp.store.reconcile_stale_worker_for_task(task_id)
    row = cp.store.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_err("not_found", "task not found"))

    log_path = task_layout(_task_root(cp, task_id)).logs / "task.log"
    if not log_path.is_file():
        return LogsResponse(lines=[], next_cursor=cursor)

    data = log_path.read_bytes()
    chunk = data[cursor:]
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return LogsResponse(lines=lines, next_cursor=len(data))


@router.post("/tasks/{task_id}/interrupt/resolve", status_code=202)
def resolve_interrupt(request: Request, task_id: str, body: ResolveInterruptBody) -> dict[str, str]:
    cp = _cp(request)
    cp.store.reconcile_stale_worker_for_task(task_id)
    row = cp.store.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_err("not_found", "task not found"))
    if row["status"] != S.TASK_WAITING_HUMAN:
        raise HTTPException(
            status_code=409,
            detail=_err(
                "invalid_task_status",
                f"task status must be {S.TASK_WAITING_HUMAN!r}, got {row['status']!r}",
            ),
        )

    if body.interrupt_seq is not None and body.interrupt_seq != row["interrupt_seq"]:
        raise HTTPException(
            status_code=409,
            detail=_err(
                "interrupt_seq_mismatch",
                f"expected interrupt_seq {row['interrupt_seq']}, got {body.interrupt_seq}",
            ),
        )

    schema = row["interrupt_expected_schema"]
    if schema is not None:
        try:
            Draft202012Validator(schema).validate(body.payload)
        except ValidationError as e:
            raise HTTPException(
                status_code=422,
                detail=_err("invalid_payload", str(e.message)),
            ) from e

    cp.store.apply_resolve(task_id, body.payload)
    _spawn_for_task(cp, task_id=task_id, workflow_key=row["workflow_key"])
    return {"status": "accepted"}


@router.post("/tasks/{task_id}/rerun", status_code=202)
def rerun_task(request: Request, task_id: str, body: RerunBody) -> dict[str, str]:
    cp = _cp(request)
    cp.store.reconcile_stale_worker_for_task(task_id)
    row = cp.store.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_err("not_found", "task not found"))
    if row["status"] == S.TASK_WAITING_HUMAN:
        raise HTTPException(
            status_code=409,
            detail=_err(
                "invalid_task_status",
                "cannot rerun while task is waiting for human input",
            ),
        )
    if _worker_lease_active(row):
        raise HTTPException(
            status_code=409,
            detail=_err(
                "worker_busy",
                "task has an active worker (lease valid and process alive)",
            ),
        )

    from_ordinal: int | None = None
    for n in cp.store.list_nodes(task_id):
        if n["node_id"] == body.from_node_id:
            from_ordinal = int(n["ordinal"])
            break
    if from_ordinal is None:
        raise HTTPException(
            status_code=404,
            detail=_err(
                "unknown_node",
                f"from_node_id not found for this task: {body.from_node_id!r}",
            ),
        )

    snapshots = cp.store.list_zip_snapshots_before_node(task_id, from_ordinal)
    for _, zip_path in snapshots:
        if not Path(zip_path).is_file():
            raise HTTPException(
                status_code=409,
                detail=_err("missing_snapshot", f"snapshot zip missing on disk: {zip_path}"),
            )

    try:
        wf = cp.engine.get_workflow(row["workflow_key"])
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=_err(
                "unknown_workflow",
                f"workflow_key not registered: {row['workflow_key']!r}",
            ),
        ) from None

    layout = task_layout(_task_root(cp, task_id))
    _clear_workspace_contents(layout.workspace)

    node_rows = cp.store.list_nodes(task_id)
    try:
        # Zips are packed relative to each node's workdir; extract into the same path
        # under workspace so downstream nodes see e.g. workspace/n1/out.txt.
        for snap_ordinal, zip_path in snapshots:
            if snap_ordinal < 0 or snap_ordinal >= len(wf.nodes):
                raise HTTPException(
                    status_code=500,
                    detail=_err(
                        "snapshot_ordinal_mismatch",
                        f"snapshot ordinal {snap_ordinal} out of range for workflow",
                    ),
                )
            spec = wf.nodes[snap_ordinal]
            if snap_ordinal >= len(node_rows) or spec.id != node_rows[snap_ordinal]["node_id"]:
                raise HTTPException(
                    status_code=500,
                    detail=_err(
                        "node_id_mismatch",
                        "task node_id order does not match registered workflow",
                    ),
                )
            dest_dir = resolve_node_workdir(layout.workspace, spec.workdir_relative)
            extract_zip_bytes(
                archive_bytes=Path(zip_path).read_bytes(),
                dest_dir=dest_dir,
            )
    except UnsafeArchiveError as e:
        cp.store.set_task_status(task_id, S.TASK_FAILED)
        raise HTTPException(
            status_code=409,
            detail=_err("unsafe_archive", str(e)),
        ) from e

    cp.store.reset_nodes_from_ordinal(task_id, from_ordinal)
    cp.store.release_lease(task_id)
    cp.store.prepare_task_for_rerun_execution(task_id)
    _spawn_for_task(cp, task_id=task_id, workflow_key=row["workflow_key"])
    return {"status": "accepted"}

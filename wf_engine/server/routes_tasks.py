from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, Field

from wf_engine import status as S
from wf_engine.lease_util import parse_utc_iso, pid_alive
from wf_engine.paths import task_layout
from wf_engine.server.state import ControlPlaneState
from wf_engine.unzip_util import UnsafeArchiveError, extract_zip_safely

router = APIRouter()


class CreateTaskBody(BaseModel):
    workflow_key: str
    input: dict[str, Any] = Field(default_factory=dict)


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
    return cp.tasks_root / task_id


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


def _serialize_task_detail(row: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row["id"],
        "workflow_key": row["workflow_key"],
        "workflow_revision": row["workflow_revision"],
        "status": row["status"],
        "input": row["input_json"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "worker_generation": row["worker_generation"],
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

    tid = cp.store.create_task(
        workflow_key=wf.key,
        workflow_revision=wf.revision,
        input_obj=body.input,
        tasks_root=str(cp.tasks_root),
    )
    root = _task_root(cp, tid)
    layout = task_layout(root)
    layout.workspace.mkdir(parents=True, exist_ok=True)
    layout.zips.mkdir(parents=True, exist_ok=True)
    layout.logs.mkdir(parents=True, exist_ok=True)

    cp.store.init_task_nodes(tid, [n.id for n in wf.nodes])
    cp.store.set_task_status(tid, S.TASK_RUNNING)
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
            "status": r["status"],
            "workflow_key": r["workflow_key"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


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

    layout = task_layout(_task_root(cp, task_id))
    _clear_workspace_contents(layout.workspace)

    try:
        for _, zip_path in snapshots:
            extract_zip_safely(Path(zip_path).read_bytes(), layout.workspace)
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

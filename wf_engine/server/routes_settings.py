from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from wf_engine.server.paths_util import effective_tasks_root
from wf_engine.server.state import ControlPlaneState

router = APIRouter()


class OpsGlobalsBody(BaseModel):
    cookie: str = ""
    authorization: str = ""


class SystemConfigBody(BaseModel):
    tasks_root: str = ""


def _cp(request: Request) -> ControlPlaneState:
    return request.app.state.cp


def _err(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _normalize_tasks_root_for_storage(cp: ControlPlaneState, raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return ""
    p = Path(stripped).expanduser()
    try:
        resolved = p.resolve()
    except (OSError, ValueError) as e:
        raise HTTPException(
            status_code=422,
            detail=_err("invalid_tasks_root", str(e)),
        ) from e
    if resolved.exists() and not resolved.is_dir():
        raise HTTPException(
            status_code=422,
            detail=_err(
                "invalid_tasks_root",
                "tasks_root must be a directory path, not a file",
            ),
        )
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            status_code=422,
            detail=_err("invalid_tasks_root", f"cannot create directory: {e}"),
        ) from e
    return str(resolved)


def _system_settings_response(cp: ControlPlaneState) -> dict[str, str]:
    stored = cp.store.get_system_config()
    eff = effective_tasks_root(cp)
    return {
        "tasks_root": stored.get("tasks_root", ""),
        "server_tasks_root_default": str(cp.tasks_root),
        "tasks_root_effective": str(eff),
    }


@router.get("/settings")
def get_settings_bundle(request: Request) -> dict:
    cp = _cp(request)
    return {
        "ops": cp.store.get_ops_globals(),
        "system": _system_settings_response(cp),
    }


@router.get("/settings/ops")
def get_ops_globals(request: Request) -> dict[str, str]:
    return _cp(request).store.get_ops_globals()


@router.put("/settings/ops")
def put_ops_globals(request: Request, body: OpsGlobalsBody) -> dict[str, str]:
    store = _cp(request).store
    store.set_ops_globals(body.cookie, body.authorization)
    return store.get_ops_globals()


@router.get("/settings/system")
def get_system_settings(request: Request) -> dict[str, str]:
    return _system_settings_response(_cp(request))


@router.put("/settings/system")
def put_system_settings(request: Request, body: SystemConfigBody) -> dict[str, str]:
    cp = _cp(request)
    normalized = _normalize_tasks_root_for_storage(cp, body.tasks_root)
    cp.store.set_system_config(tasks_root=normalized)
    return _system_settings_response(cp)

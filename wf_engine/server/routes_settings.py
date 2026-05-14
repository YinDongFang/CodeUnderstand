from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from wf_engine.server.paths_util import effective_tasks_root
from wf_engine.server.state import ControlPlaneState

router = APIRouter()


class ConsoleSettingsBody(BaseModel):
    tasks_root: str = ""
    cookie: str = ""
    authorization: str = ""


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


def _settings_response(cp: ControlPlaneState) -> dict[str, str]:
    stored = cp.store.get_console_settings()
    eff = effective_tasks_root(cp)
    return {
        "tasks_root": stored["tasks_root"],
        "cookie": stored["cookie"],
        "authorization": stored["authorization"],
        "server_tasks_root_default": str(cp.tasks_root),
        "tasks_root_effective": str(eff),
    }


@router.get("/settings")
def get_settings(request: Request) -> dict[str, str]:
    return _settings_response(_cp(request))


@router.put("/settings")
def put_settings(request: Request, body: ConsoleSettingsBody) -> dict[str, str]:
    cp = _cp(request)
    normalized = _normalize_tasks_root_for_storage(cp, body.tasks_root)
    cp.store.set_console_settings(
        tasks_root=normalized,
        cookie=body.cookie,
        authorization=body.authorization,
    )
    return _settings_response(cp)

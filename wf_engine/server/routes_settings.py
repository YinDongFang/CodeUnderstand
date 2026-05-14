from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from wf_engine.server.state import ControlPlaneState

router = APIRouter()


class OpsGlobalsBody(BaseModel):
    cookie: str = ""
    authorization: str = ""


def _cp(request: Request) -> ControlPlaneState:
    return request.app.state.cp


@router.get("/settings/ops")
def get_ops_globals(request: Request) -> dict[str, str]:
    return _cp(request).store.get_ops_globals()


@router.put("/settings/ops")
def put_ops_globals(request: Request, body: OpsGlobalsBody) -> dict[str, str]:
    store = _cp(request).store
    store.set_ops_globals(body.cookie, body.authorization)
    return store.get_ops_globals()

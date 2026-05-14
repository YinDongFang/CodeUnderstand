from __future__ import annotations

from typing import Any

from fastapi import Request

from wf_engine.server.state import ControlPlaneState


def _cp(request: Request) -> ControlPlaneState:
    return request.app.state.cp


def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}

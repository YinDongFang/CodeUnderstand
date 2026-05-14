from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from wf_engine.engine import Engine
from wf_engine.store.sqlite import SqliteStore
from wf_engine.supervisor import spawn_worker

from wf_engine.server import routes_settings, routes_tasks
from wf_engine.server.state import ControlPlaneState


def create_app(
    engine: Engine,
    store: SqliteStore,
    tasks_root: Path,
    *,
    spawn_worker_fn: Callable[..., Any] | None = None,
) -> FastAPI:
    """Build the FastAPI control plane application."""
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        d = exc.detail
        if isinstance(d, dict) and isinstance(d.get("error"), dict):
            return JSONResponse(status_code=exc.status_code, content=d)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "http_error",
                    "message": str(d) if d is not None else "request failed",
                }
            },
        )

    app.state.cp = ControlPlaneState(
        engine=engine,
        store=store,
        tasks_root=Path(tasks_root).resolve(),
        spawn_worker_fn=spawn_worker_fn or spawn_worker,
    )
    app.include_router(routes_tasks.router)
    app.include_router(routes_settings.router)
    return app

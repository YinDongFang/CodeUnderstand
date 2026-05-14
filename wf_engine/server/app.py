from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI

from wf_engine.engine import Engine
from wf_engine.store.sqlite import SqliteStore
from wf_engine.supervisor import spawn_worker

from wf_engine.server import routes_tasks
from wf_engine.server.state import ControlPlaneState


def create_app(
    engine: Engine,
    store: SqliteStore,
    tasks_root: Path,
    registry_module: str | None = None,
    *,
    spawn_worker_fn: Callable[..., Any] | None = None,
) -> FastAPI:
    """Build the FastAPI control plane application."""
    app = FastAPI()
    app.state.cp = ControlPlaneState(
        engine=engine,
        store=store,
        tasks_root=Path(tasks_root).resolve(),
        registry_module=registry_module,
        spawn_worker_fn=spawn_worker_fn or spawn_worker,
    )
    app.include_router(routes_tasks.router)
    return app

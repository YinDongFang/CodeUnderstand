from __future__ import annotations

from pathlib import Path
from typing import Any

from wf_engine.workflow import Workflow


class Engine:
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register_workflow(self, wf: Workflow) -> None:
        if wf.key in self._workflows:
            msg = f"workflow_key already registered: {wf.key!r}"
            raise ValueError(msg)
        self._workflows[wf.key] = wf

    def get_workflow(self, key: str) -> Workflow:
        return self._workflows[key]

    def list_workflows(self) -> list[dict[str, Any]]:
        wfs = sorted(self._workflows.values(), key=lambda wf: wf.key)
        return [
            {
                "key": wf.key,
                "input_schema": wf.input_schema,
            }
            for wf in wfs
        ]

    def serve(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        *,
        db_path: Path,
        tasks_root: Path,
        registry_module: str | None = None,
    ) -> None:
        """Run the HTTP control plane with a single Uvicorn worker."""
        import uvicorn

        from wf_engine.server.app import create_app
        from wf_engine.store.sqlite import SqliteStore

        store = SqliteStore(Path(db_path))
        store.init_schema()
        app = create_app(self, store, Path(tasks_root), registry_module)
        uvicorn.run(app, host=host, port=port, workers=1)

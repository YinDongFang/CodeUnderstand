from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

from wf_engine.workflow import Workflow

log = logging.getLogger(__name__)

_WORKFLOW_PACKAGE = "workflow"


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

    def discover_workflows(self) -> int:
        """Import every ``.py`` module under ``workflow/`` and call its
        ``register_all(engine)`` (if present).  Returns the number of modules
        that successfully registered at least one workflow."""
        try:
            pkg: ModuleType = importlib.import_module(_WORKFLOW_PACKAGE)
        except ModuleNotFoundError:
            log.warning("workflow package not found — no workflows registered")
            return 0
        spec = getattr(pkg, "__spec__", None)
        if spec is None or spec.submodule_search_locations is None:
            return 0
        pkg_paths = [Path(p) for p in spec.submodule_search_locations]
        count_before = len(self._workflows)
        for root in pkg_paths:
            for py_file in sorted(root.glob("*.py")):
                name = py_file.stem
                if name.startswith("_"):
                    continue
                mod_name = f"{_WORKFLOW_PACKAGE}.{name}"
                try:
                    mod = importlib.import_module(mod_name)
                except Exception:
                    log.exception("failed to import %s", mod_name)
                    continue
                register_all = getattr(mod, "register_all", None)
                if register_all is None:
                    continue
                try:
                    register_all(self)
                except Exception:
                    log.exception(
                        "register_all failed for %s", mod_name
                    )
        return len(self._workflows) - count_before

    def serve(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        *,
        db_path: Path,
        tasks_root: Path,
    ) -> None:
        """Run the HTTP control plane with a single Uvicorn worker."""
        import uvicorn

        from wf_engine.server.app import create_app
        from wf_engine.store.sqlite import SqliteStore

        n = self.discover_workflows()
        log.info("discovered %d workflow module(s)", n)

        store = SqliteStore(Path(db_path))
        store.init_schema()
        app = create_app(self, store, Path(tasks_root))
        uvicorn.run(app, host=host, port=port, workers=1)

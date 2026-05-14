from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

from wf_engine.workflow import Workflow

log = logging.getLogger(__name__)

_WORKFLOW_PACKAGE = "workflow"


def _build_workflow_from_module(mod: ModuleType, *, default_key: str) -> Workflow | None:
    """Construct a ``Workflow`` from a module's declarative attributes.

    Reads ``WORKFLOW_KEY`` (optional, defaults to ``default_key``),
    ``get_input_schema()`` (optional), and ``get_nodes()`` (required).
    Returns ``None`` if ``get_nodes`` is missing — callers should treat that
    as "module is not a workflow".
    """
    get_nodes = getattr(mod, "get_nodes", None)
    if get_nodes is None:
        return None
    key = str(getattr(mod, "WORKFLOW_KEY", default_key))
    input_schema = None
    get_input_schema = getattr(mod, "get_input_schema", None)
    if get_input_schema is not None:
        input_schema = get_input_schema()
    wf = Workflow(key=key, input_schema=input_schema)
    for node in get_nodes():
        wf.add_node(
            node_id=node["id"],
            fn=node["fn"],
            workdir=node.get("workdir", "."),
            whitelist=node.get("whitelist", ()),
        )
    return wf


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
        """Import every ``.py`` module under ``workflow/`` and build a
        ``Workflow`` from its declarative metadata.

        Each module may expose:

        - ``WORKFLOW_KEY`` (optional, default = module stem)
        - ``get_input_schema()`` → ``dict | None`` (optional)
        - ``get_nodes()`` → ``list[dict]`` with keys
          ``id``, ``fn``, optional ``workdir`` (default ``"."``),
          optional ``whitelist`` (default ``[]``)

        Modules without ``get_nodes`` are skipped. Returns the number of
        workflows registered.
        """
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
                stem = py_file.stem
                if stem.startswith("_"):
                    continue
                mod_name = f"{_WORKFLOW_PACKAGE}.{stem}"
                try:
                    mod = importlib.import_module(mod_name)
                except Exception:
                    log.exception("failed to import %s", mod_name)
                    continue
                wf = _build_workflow_from_module(mod, default_key=stem)
                if wf is None:
                    continue
                try:
                    self.register_workflow(wf)
                except ValueError:
                    log.exception("duplicate workflow_key from %s", mod_name)
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

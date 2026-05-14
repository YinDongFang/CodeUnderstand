from __future__ import annotations

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

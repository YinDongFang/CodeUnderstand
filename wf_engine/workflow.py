from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wf_engine.context import NodeContext


@dataclass
class NodeSpec:
    id: str
    fn: Callable[[NodeContext], None]
    workdir_relative: str  # relative to workspace/
    whitelist_globs: Sequence[str] = field(default_factory=tuple)


@dataclass
class Workflow:
    key: str
    revision: str = "1"
    nodes: list[NodeSpec] = field(default_factory=list)
    #: JSON Schema ``type: object`` describing ``input`` keys for control-plane forms (optional).
    input_schema: dict[str, Any] | None = None

    def add_node(
        self,
        node_id: str,
        fn: Callable[[NodeContext], None],
        *,
        workdir: str = ".",
        whitelist: Sequence[str] = (),
    ) -> None:
        if ".." in Path(workdir).parts:
            raise ValueError(f"workdir must not contain '..': {workdir!r}")
        self.nodes.append(
            NodeSpec(
                id=node_id,
                fn=fn,
                workdir_relative=workdir,
                whitelist_globs=tuple(whitelist),
            )
        )

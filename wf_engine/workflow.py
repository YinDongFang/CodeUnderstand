from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

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

    def add_node(
        self,
        node_id: str,
        fn: Callable[[NodeContext], None],
        *,
        workdir: str = ".",
        whitelist: Sequence[str] = (),
    ) -> None:
        self.nodes.append(
            NodeSpec(
                id=node_id,
                fn=fn,
                workdir_relative=workdir,
                whitelist_globs=tuple(whitelist),
            )
        )

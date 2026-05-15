"""Flow：DAG 调度器。"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .node import Node, NodeState
from .handle import NodeHandle


class Flow:
    def __init__(self) -> None:
        self._nodes: list[Node] = []
        self._inflight: set[asyncio.Task] = set()
        self._id_counter: dict[str, int] = {}

    @property
    def nodes(self) -> tuple[NodeHandle, ...]:
        return tuple(NodeHandle(n) for n in self._nodes)

    def submit(
        self,
        fn: Callable[..., Awaitable[Any]],
        /,
        *parents: NodeHandle,
        **named_parents: NodeHandle,
    ) -> NodeHandle:
        # 1) 校验：所有参数必须是 NodeHandle
        for i, p in enumerate(parents):
            if not isinstance(p, NodeHandle):
                raise TypeError(
                    f"submit: positional arg #{i} must be NodeHandle, "
                    f"got {type(p).__name__}. "
                    f"Bind literal values into fn via functools.partial / lambda before submit."
                )
        for k, v in named_parents.items():
            if not isinstance(v, NodeHandle):
                raise TypeError(
                    f"submit: kwarg {k!r} must be NodeHandle, got {type(v).__name__}."
                )

        # 2) 构造 Node（校验通过后才递增计数器，保证事务性）
        parent_args = tuple(h._node for h in parents)
        parent_kwargs = {k: v._node for k, v in named_parents.items()}
        node = Node(
            id=self._next_id(fn),
            fn=fn,
            parent_args=parent_args,
            parent_kwargs=parent_kwargs,
            parents=frozenset(parent_args) | frozenset(parent_kwargs.values()),
        )
        node.future = asyncio.get_running_loop().create_future()
        self._nodes.append(node)

        # 3) 尝试调度（本任务里桩成空，下一任务补完）
        self._try_schedule(node)

        return NodeHandle(node)

    def _next_id(self, fn: Callable) -> str:
        name = getattr(fn, "__name__", None) or repr(fn)
        seq = self._id_counter.get(name, 0)
        self._id_counter[name] = seq + 1
        return f"{name}#{seq}"

    def _try_schedule(self, node: Node) -> None:
        # 桩：下一任务补充真正的调度逻辑
        pass

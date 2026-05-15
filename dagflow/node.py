"""节点状态枚举与数据类。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable


class NodeState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({
    NodeState.DONE,
    NodeState.FAILED,
    NodeState.SKIPPED,
    NodeState.CANCELLED,
})


@dataclass
class Node:
    id: str
    fn: Callable[..., Awaitable[Any]]
    parent_args: tuple["Node", ...]
    parent_kwargs: dict[str, "Node"]
    parents: frozenset["Node"]
    state: NodeState = NodeState.PENDING
    result: Any = None
    exception: BaseException | None = None
    future: asyncio.Future | None = None  # 由 Flow.submit 在事件循环内赋值

    # 让 Node 在 frozenset 里以身份（identity）参与去重
    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other

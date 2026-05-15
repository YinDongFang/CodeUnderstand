"""节点状态枚举与数据类。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable


class NodeState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"


@dataclass
class Node:
    id: str
    index: int
    fn: Callable[..., Awaitable[Any]]
    parent_args: tuple["Node", ...]
    parent_kwargs: dict[str, "Node"]
    state: NodeState = NodeState.PENDING
    result: Any = None
    future: asyncio.Future | None = None

    # 身份语义：Node 可变，值相等不适用
    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other

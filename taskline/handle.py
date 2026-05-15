"""NodeHandle：对外暴露的节点引用，可 await、可哈希。"""
from __future__ import annotations

from typing import Generic, TypeVar

from .node import Node, NodeState

T = TypeVar("T")


class NodeHandle(Generic[T]):
    __slots__ = ("_node",)

    def __init__(self, node: Node) -> None:
        self._node = node

    def __await__(self):
        return self._node.future.__await__()

    @property
    def state(self) -> NodeState:
        return self._node.state

    @property
    def id(self) -> str:
        return self._node.id

    def __hash__(self) -> int:
        return id(self._node)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, NodeHandle) and other._node is self._node

    def __repr__(self) -> str:
        return f"<NodeHandle {self._node.id} {self._node.state.value}>"

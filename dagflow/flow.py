"""Flow：DAG 调度器。"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .node import Node, NodeState, TERMINAL_STATES
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
        # 父全 DONE → 起 task；否则 PENDING 等被唤醒
        # （SKIPPED 分支在后续任务里补）
        if all(p.state is NodeState.DONE for p in node.parents):
            node.state = NodeState.READY
            task = asyncio.create_task(self._run(node))
            task.add_done_callback(self._on_task_done)
            self._inflight.add(task)

    async def _run(self, node: Node) -> None:
        node.state = NodeState.RUNNING
        try:
            real_args = tuple(p.result for p in node.parent_args)
            real_kwargs = {k: p.result for k, p in node.parent_kwargs.items()}
            value = await node.fn(*real_args, **real_kwargs)
            node.result = value
            node.state = NodeState.DONE
            node.future.set_result(value)
        finally:
            self._wake_children(node)

    def _wake_children(self, parent: Node) -> None:
        for n in self._nodes:
            if n.state is NodeState.PENDING and parent in n.parents:
                self._try_schedule(n)

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._inflight.discard(task)

    async def wait_all(self) -> None:
        """等到所有已注册节点都进入终态后返回。

        若在 wait_all 等待期间有新的 submit 进来，新节点也会被等到。
        多次调用幂等：第二次若已全终态会立刻返回。
        """
        while any(n.state not in TERMINAL_STATES for n in self._nodes):
            if self._inflight:
                await asyncio.wait(
                    self._inflight, return_when=asyncio.FIRST_COMPLETED
                )
                self._inflight = {t for t in self._inflight if not t.done()}
            else:
                await asyncio.sleep(0)

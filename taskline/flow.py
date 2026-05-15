"""Flow：串行流水线编排器。"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from .node import Node, NodeState
from .handle import NodeHandle
from .hooks import HookContext
from .errors import StateMismatchError
from .persistence import load_state, save_state

HookFn = Callable[[HookContext], Awaitable[None]]


class Flow:
    def __init__(
        self,
        *,
        before_hook: HookFn | None = None,
        after_hook: HookFn | None = None,
    ) -> None:
        try:
            self._state_path = os.environ["TASKLINE_STATE_PATH"]
        except KeyError:
            raise RuntimeError(
                "TASKLINE_STATE_PATH environment variable is required but not set."
            ) from None
        self._before_hook = before_hook
        self._after_hook = after_hook
        self._nodes: list[Node] = []
        self._id_counter: dict[str, int] = {}
        self._driver: asyncio.Task | None = None
        self._resume_data: list[dict] = load_state(self._state_path)
        self._next_index: int = len(self._resume_data)

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

        # 2) 构造 Node
        parent_args = tuple(h._node for h in parents)
        parent_kwargs = {k: v._node for k, v in named_parents.items()}
        node = Node(
            id=self._next_id(fn),
            index=len(self._nodes),
            fn=fn,
            parent_args=parent_args,
            parent_kwargs=parent_kwargs,
        )
        node.future = asyncio.get_running_loop().create_future()
        self._nodes.append(node)

        # 3) 持久化对齐
        if node.index < len(self._resume_data):
            persisted = self._resume_data[node.index]
            if persisted["id"] != node.id:
                raise StateMismatchError(
                    f"persisted node #{node.index} id {persisted['id']!r} "
                    f"!= submitted id {node.id!r}; program and state file diverged."
                )
            node.state = NodeState.DONE
            node.result = persisted["result"]
            node.future.set_result(node.result)
        else:
            if self._driver is None or self._driver.done():
                self._driver = asyncio.create_task(self._drain())

        return NodeHandle(node)

    def _next_id(self, fn: Callable) -> str:
        name = getattr(fn, "__name__", None) or repr(fn)
        seq = self._id_counter.get(name, 0)
        self._id_counter[name] = seq + 1
        return f"{name}#{seq}"

    async def _drain(self) -> None:
        try:
            while self._next_index < len(self._nodes):
                node = self._nodes[self._next_index]
                self._next_index += 1
                await self._run_node(node)
        except BaseException as e:
            # 异常管道（非错误恢复）：把异常塞进所有未决 future，
            # 使 await handle 醒来抛出而非永久 hang；然后原样上抛。
            for n in self._nodes:
                if n.future is not None and not n.future.done():
                    n.future.set_exception(e)
                    # 立即标记已取，避免无人 await 的 future 在 GC 时
                    # 打印 "Future exception was never retrieved"。
                    # future 仍保持异常态，await handle 时照样抛出。
                    n.future.exception()
            raise

    async def _run_node(self, node: Node) -> None:
        node.state = NodeState.RUNNING
        resuming = bool(self._resume_data) and node.index == len(self._resume_data)

        if self._before_hook is not None:
            await self._before_hook(
                HookContext(phase="before", node_id=node.id,
                            index=node.index, resuming=resuming)
            )

        real_args = tuple(p.result for p in node.parent_args)
        real_kwargs = {k: p.result for k, p in node.parent_kwargs.items()}
        value = await node.fn(*real_args, **real_kwargs)

        if self._after_hook is not None:
            await self._after_hook(
                HookContext(phase="after", node_id=node.id, index=node.index,
                            resuming=resuming, result=value)
            )

        # before + fn + after 全成功 → 落地状态并 checkpoint。
        # 置 DONE 与 set_result 之间无 await，避免状态/future 不一致窗口。
        node.result = value
        node.state = NodeState.DONE
        self._persist()
        node.future.set_result(value)

    def _persist(self) -> None:
        done = [
            {"id": n.id, "result": n.result}
            for n in self._nodes
            if n.state is NodeState.DONE
        ]
        save_state(self._state_path, {"version": 1, "nodes": done})

    async def wait_all(self) -> None:
        """等到 driver 把当前队列排空；driver 以异常结束时原样上抛。"""
        driver = self._driver
        while driver is not None:
            await driver
            # driver 正常结束；若 submit 期间创建了新 driver，继续等待。
            next_driver = self._driver
            if next_driver is driver or next_driver is None:
                break
            driver = next_driver

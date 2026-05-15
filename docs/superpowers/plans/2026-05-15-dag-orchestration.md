# DAG 异步任务编排框架 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个纯 asyncio、单进程的 DAG 异步任务编排库（`dagflow`），支持外部控制器在运行时动态 `submit(fn, *parents, **named_parents)`，所有依赖必须是 `NodeHandle`，失败传染下游 SKIPPED。

**Architecture:** 三层职责：`Node`（纯数据 + asyncio.Future）/ `NodeHandle`（对外不可变引用，awaitable）/ `Flow`（调度器：submit 时校验+构图+尝试调度；父完成时唤醒下游；wait_all 等所有节点终态）。状态机：PENDING → READY → RUNNING → DONE/FAILED/SKIPPED/CANCELLED。

**Tech Stack:** Python 3.10+，asyncio，dataclasses，pytest + pytest-asyncio。零运行时依赖。

**Spec：** `docs/superpowers/specs/2026-05-15-dag-orchestration-design.md`（commit `494d298`）

---

## 文件结构

```
pyproject.toml          # 新建
dagflow/
  __init__.py           # 重新导出公开 API
  errors.py             # NodeFailed, NodeSkipped
  node.py               # NodeState, Node
  handle.py             # NodeHandle
  flow.py               # Flow（含 to_dot）
tests/
  __init__.py           # 空文件
  test_handle.py        # NodeHandle 单元测试
  test_validation.py    # submit 参数校验
  test_basic.py         # 单节点 / 链 / 钻石 / 多父 / 同 handle 多次 / fn 返回 None
  test_failure.py       # FAILED 传染 / __cause__ 链
  test_cancellation.py  # 节点 task 被外部 cancel
  test_dynamic.py       # 控制器 await 中间结果再 submit
  test_wait_all.py      # wait_all 各种边界
  test_concurrent.py    # 真并行（用 wall time 验证）
  test_visualize.py     # to_dot
  test_e2e_smoke.py     # 端到端冒烟
```

每个文件单一职责；测试按行为分类，不强行做单元/集成区分。

---

## 通用约定（每个任务都适用）

- **运行命令**：所有命令在仓库根目录运行。Windows 用 PowerShell；Linux 用 bash。`python` 命令在两个平台都解析为 python3（用户已配置）。
- **运行测试的方式**：统一用 `python -m pytest <path> -v`。`-v` 显示每个 test 的 pass/fail。
- **commit 消息风格**：参考 git log，简短中文，无 scope 前缀。所有 commit 都加 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` 行。
- **TDD 节奏**：每个任务都是 写失败测试 → 跑测试确认失败 → 写实现 → 跑测试确认通过 → 提交。
- **测试文件首行**：所有测试文件首行加 `"""..."""` 模块 docstring，简述这个文件覆盖的行为。

---

## Task 1: 项目脚手架

**Files:**
- Create: `pyproject.toml`
- Create: `dagflow/__init__.py`
- Create: `dagflow/errors.py`
- Create: `dagflow/node.py`
- Create: `dagflow/handle.py`
- Create: `dagflow/flow.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: 创建 `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "dagflow"
version = "0.1.0"
description = "A pure-asyncio DAG task orchestration library"
requires-python = ">=3.10"

[project.optional-dependencies]
test = ["pytest>=7.0", "pytest-asyncio>=0.23"]

[tool.setuptools]
packages = ["dagflow"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: 创建 6 个空 Python 模块文件**

每个文件内容为空字符串（创建文件即可）：
- `dagflow/__init__.py`
- `dagflow/errors.py`
- `dagflow/node.py`
- `dagflow/handle.py`
- `dagflow/flow.py`
- `tests/__init__.py`

- [ ] **Step 3: 安装包到当前 Python 环境**

Run: `python -m pip install -e ".[test]"`
Expected: 成功安装；最后一行类似 `Successfully installed dagflow-0.1.0 ...`

- [ ] **Step 4: 验证 import 工作**

Run: `python -c "import dagflow; print('ok')"`
Expected: 输出 `ok`，无 Traceback

- [ ] **Step 5: 验证 pytest 能跑（即使没测试）**

Run: `python -m pytest`
Expected: `no tests ran` 或类似（exit code 5 是 pytest 的 "no tests collected" 状态，可接受）

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml dagflow/ tests/
git commit -m "搭建 dagflow 项目骨架

- pyproject.toml 配置 pytest-asyncio auto 模式
- dagflow 包及子模块占位
- tests 包占位

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 异常类型

**Files:**
- Modify: `dagflow/errors.py`
- Create: `tests/test_handle.py`（先建文件，本任务只放 errors 相关 test；handle 测试稍后追加）

- [ ] **Step 1: 写失败的测试**

写入 `tests/test_handle.py`：

```python
"""dagflow.errors 与 dagflow.handle.NodeHandle 的单元测试。"""
import pytest

from dagflow.errors import NodeFailed, NodeSkipped


def test_node_failed_is_exception():
    assert issubclass(NodeFailed, Exception)
    e = NodeFailed("node-1")
    assert "node-1" in str(e)


def test_node_skipped_is_exception():
    assert issubclass(NodeSkipped, Exception)
    e = NodeSkipped("node-2")
    assert "node-2" in str(e)


def test_node_failed_can_carry_cause():
    inner = ValueError("root cause")
    outer = NodeFailed("n")
    outer.__cause__ = inner
    assert outer.__cause__ is inner


def test_two_failures_distinct_instances():
    a = NodeFailed("a")
    b = NodeFailed("b")
    assert a is not b
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_handle.py -v`
Expected: ImportError 或 collection 错误（`NodeFailed`、`NodeSkipped` 不存在）

- [ ] **Step 3: 实现 `dagflow/errors.py`**

```python
"""节点失败 / 跳过的异常类型。"""


class NodeFailed(Exception):
    """节点 fn 抛异常时由框架抛出，__cause__ 指向 fn 抛的原异常。"""


class NodeSkipped(Exception):
    """因上游 FAILED/CANCELLED/SKIPPED 被跳过，__cause__ 指向最近上游的异常。"""
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_handle.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add dagflow/errors.py tests/test_handle.py
git commit -m "添加 NodeFailed 与 NodeSkipped 异常类型

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: NodeState 枚举 + Node 数据类

**Files:**
- Modify: `dagflow/node.py`
- Create: `tests/test_basic.py`（先建文件，本任务只放 Node/NodeState 单元测试）

- [ ] **Step 1: 写失败的测试**

写入 `tests/test_basic.py`：

```python
"""dagflow 基础行为：NodeState 枚举、Node 数据类，以及后续 Flow 端到端用法。"""
import pytest

from dagflow.node import NodeState, Node


def test_node_state_values():
    assert NodeState.PENDING.value == "pending"
    assert NodeState.READY.value == "ready"
    assert NodeState.RUNNING.value == "running"
    assert NodeState.DONE.value == "done"
    assert NodeState.FAILED.value == "failed"
    assert NodeState.SKIPPED.value == "skipped"
    assert NodeState.CANCELLED.value == "cancelled"


def test_node_state_is_str_enum():
    # str enum: instance is a str, can be compared to strings directly
    assert isinstance(NodeState.DONE, str)
    assert NodeState.DONE == "done"


async def _dummy():
    return 1


def test_node_construction_defaults():
    n = Node(
        id="dummy#0",
        fn=_dummy,
        parent_args=(),
        parent_kwargs={},
        parents=frozenset(),
    )
    assert n.id == "dummy#0"
    assert n.fn is _dummy
    assert n.parent_args == ()
    assert n.parent_kwargs == {}
    assert n.parents == frozenset()
    assert n.state is NodeState.PENDING
    assert n.result is None
    assert n.exception is None
    assert n.future is None  # 由 Flow.submit 在事件循环内赋值
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_basic.py -v`
Expected: ImportError（`NodeState`/`Node` 不存在）

- [ ] **Step 3: 实现 `dagflow/node.py`**

```python
"""节点状态枚举与数据类。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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
```

注意：`__hash__`/`__eq__` 用身份语义，确保 `frozenset[Node]` 不依赖字段值（字段值会变）。

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_basic.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add dagflow/node.py tests/test_basic.py
git commit -m "添加 NodeState 枚举与 Node 数据类

Node 用身份做 hash/eq，便于在 frozenset 中按身份去重。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: NodeHandle

**Files:**
- Modify: `dagflow/handle.py`
- Modify: `tests/test_handle.py`（追加 NodeHandle 测试）

- [ ] **Step 1: 追加失败测试到 `tests/test_handle.py`**

在文件末尾追加：

```python
import asyncio

from dagflow.node import Node, NodeState
from dagflow.handle import NodeHandle


def _make_node(node_id: str = "n#0") -> Node:
    async def _fn():
        return 1
    return Node(
        id=node_id,
        fn=_fn,
        parent_args=(),
        parent_kwargs={},
        parents=frozenset(),
    )


def test_handle_id_and_state():
    n = _make_node("foo#3")
    h = NodeHandle(n)
    assert h.id == "foo#3"
    assert h.state is NodeState.PENDING


def test_handle_equality_by_identity():
    n = _make_node()
    h1 = NodeHandle(n)
    h2 = NodeHandle(n)
    assert h1 == h2
    assert hash(h1) == hash(h2)
    # 可作 dict key
    d = {h1: "value"}
    assert d[h2] == "value"


def test_handle_inequality_for_different_nodes():
    h1 = NodeHandle(_make_node("a"))
    h2 = NodeHandle(_make_node("b"))
    assert h1 != h2
    assert h1 != "not a handle"


def test_handle_repr_contains_id_and_state():
    h = NodeHandle(_make_node("bar#5"))
    s = repr(h)
    assert "bar#5" in s
    assert "pending" in s


async def test_handle_await_returns_future_result():
    n = _make_node()
    n.future = asyncio.get_running_loop().create_future()
    n.future.set_result(42)
    h = NodeHandle(n)
    assert (await h) == 42


async def test_handle_await_propagates_exception():
    n = _make_node()
    n.future = asyncio.get_running_loop().create_future()
    err = ValueError("boom")
    n.future.set_exception(err)
    h = NodeHandle(n)
    with pytest.raises(ValueError, match="boom"):
        await h


async def test_handle_await_can_be_repeated():
    n = _make_node()
    n.future = asyncio.get_running_loop().create_future()
    n.future.set_result("once")
    h = NodeHandle(n)
    assert (await h) == "once"
    assert (await h) == "once"  # Future 缓存结果
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_handle.py -v`
Expected: 现有 4 个 errors 测试通过，新增 7 个 NodeHandle 测试失败（ImportError）

- [ ] **Step 3: 实现 `dagflow/handle.py`**

```python
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
        # 直接代理到底层 Future
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
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_handle.py -v`
Expected: 11 passed（4 errors + 7 handle）

- [ ] **Step 5: 提交**

```bash
git add dagflow/handle.py tests/test_handle.py
git commit -m "添加 NodeHandle 类

委托 __await__ 给底层 Future；hash/eq 按节点身份。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Flow 骨架 + submit 校验 + _next_id

**Files:**
- Modify: `dagflow/flow.py`
- Modify: `dagflow/__init__.py`
- Create: `tests/test_validation.py`

本任务只实现 submit 的"校验 + 节点构造 + 返回 handle"路径，**还不做调度**。调度在下一任务加上。这样 test_validation 可以独立先 ready。

- [ ] **Step 1: 写失败测试 `tests/test_validation.py`**

```python
"""submit 参数校验：非 NodeHandle 立即 TypeError，且节点不留半成品。"""
from functools import partial

import pytest

from dagflow import Flow, NodeHandle


async def _noop():
    return None


async def test_submit_rejects_positional_literal():
    flow = Flow()
    with pytest.raises(TypeError, match="positional arg #0"):
        flow.submit(_noop, 42)


async def test_submit_rejects_kwarg_literal():
    flow = Flow()
    with pytest.raises(TypeError, match="'x'"):
        flow.submit(_noop, x="literal")


async def test_submit_rejects_none():
    flow = Flow()
    with pytest.raises(TypeError):
        flow.submit(_noop, None)


async def test_submit_validation_does_not_register_node():
    flow = Flow()
    try:
        flow.submit(_noop, "bad")
    except TypeError:
        pass
    assert len(flow.nodes) == 0


async def test_submit_validation_does_not_increment_id_counter():
    flow = Flow()
    h_ok1 = flow.submit(_noop)
    with pytest.raises(TypeError):
        flow.submit(_noop, "bad")
    h_ok2 = flow.submit(_noop)
    # 计数器没被失败的 submit 污染：连续序号
    assert h_ok1.id == "_noop#0"
    assert h_ok2.id == "_noop#1"


async def test_submit_returns_node_handle():
    flow = Flow()
    h = flow.submit(_noop)
    assert isinstance(h, NodeHandle)
    assert h.id.startswith("_noop#")
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_validation.py -v`
Expected: ImportError（`dagflow.Flow` 不存在）

- [ ] **Step 3: 实现 `dagflow/flow.py`（先只写 submit 的校验部分，调度桩成空）**

```python
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
```

- [ ] **Step 4: 更新 `dagflow/__init__.py` 重新导出公开 API**

```python
"""dagflow：纯 asyncio 的 DAG 任务编排库。"""
from .flow import Flow
from .handle import NodeHandle
from .node import NodeState
from .errors import NodeFailed, NodeSkipped

__all__ = ["Flow", "NodeHandle", "NodeState", "NodeFailed", "NodeSkipped"]
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `python -m pytest tests/test_validation.py -v`
Expected: 6 passed

并确认已有测试没被打坏：
Run: `python -m pytest -v`
Expected: 全部 passed（17 total: 4 errors + 7 handle + 3 node + 6 validation - 重新算：4+7+3+6=20？实际还要看 test_basic.py 行数）

注：`test_basic.py` 目前只有 3 个 Node/NodeState 测试，pytest 收集到的总数应为 4+7+3+6 = 20。

- [ ] **Step 6: 提交**

```bash
git add dagflow/flow.py dagflow/__init__.py tests/test_validation.py
git commit -m "实现 Flow.submit 参数校验

submit 拒收非 NodeHandle 参数；校验失败时不污染节点列表与 id 计数器。
调度逻辑在下一步补完。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 单节点调度（happy path）+ wait_all

**Files:**
- Modify: `dagflow/flow.py`（补完 `_try_schedule` 的 DONE 路径、`_run` 成功路径、`_on_task_done`、`wait_all`）
- Modify: `tests/test_basic.py`（追加单节点端到端测试）
- Create: `tests/test_wait_all.py`

- [ ] **Step 1: 追加测试到 `tests/test_basic.py`**

在文件末尾追加：

```python
from dagflow import Flow


async def test_single_node_runs_and_returns_result():
    flow = Flow()

    async def make_value():
        return 42

    h = flow.submit(make_value)
    await flow.wait_all()
    assert (await h) == 42
    assert h.state is NodeState.DONE


async def test_single_node_returning_none_is_valid():
    flow = Flow()

    async def returns_none():
        return None

    h = flow.submit(returns_none)
    await flow.wait_all()
    assert (await h) is None
    assert h.state is NodeState.DONE


async def test_await_handle_without_wait_all():
    flow = Flow()

    async def quick():
        return "x"

    h = flow.submit(quick)
    assert (await h) == "x"


async def test_await_handle_repeated():
    flow = Flow()

    async def once():
        return "only"

    h = flow.submit(once)
    await flow.wait_all()
    assert (await h) == "only"
    assert (await h) == "only"
```

- [ ] **Step 2: 创建 `tests/test_wait_all.py`**

```python
"""wait_all 的各种边界：空 flow、多次调用、wait_all 期间并发 submit 等。"""
import asyncio

import pytest

from dagflow import Flow


async def test_wait_all_on_empty_flow_returns_immediately():
    flow = Flow()
    # 不应挂起；用 wait_for 兜底
    await asyncio.wait_for(flow.wait_all(), timeout=0.5)


async def test_wait_all_is_idempotent():
    flow = Flow()

    async def fn():
        return 1

    h = flow.submit(fn)
    await flow.wait_all()
    await flow.wait_all()  # 第二次应立刻返回
    assert (await h) == 1


async def test_wait_all_waits_for_inflight_node():
    flow = Flow()

    async def slow():
        await asyncio.sleep(0.05)
        return "slow"

    h = flow.submit(slow)
    # wait_all 应等到节点真的完成
    await flow.wait_all()
    assert h.state.name == "DONE"
    assert (await h) == "slow"
```

- [ ] **Step 3: 跑测试，确认失败**

Run: `python -m pytest tests/test_basic.py tests/test_wait_all.py -v`
Expected: 已有的 NodeState/Node 测试通过，新增的端到端测试失败（`wait_all` 不存在，或 `_try_schedule` 是空桩节点永远不跑）

- [ ] **Step 4: 补完 `dagflow/flow.py` 的调度部分**

在文件末尾的 `_try_schedule` 方法替换为完整版本，并新增 `_run` / `_on_task_done` / `wait_all`：

替换 `_try_schedule`：

```python
    def _try_schedule(self, node: Node) -> None:
        # 父全 DONE → 起 task；否则 PENDING 等被唤醒
        # （SKIPPED 分支在后续任务里补）
        if all(p.state is NodeState.DONE for p in node.parents):
            node.state = NodeState.READY
            task = asyncio.create_task(self._run(node))
            task.add_done_callback(self._on_task_done)
            self._inflight.add(task)
```

在类内追加：

```python
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
```

并在 `dagflow/flow.py` 顶部 import 里加上 `TERMINAL_STATES`：

```python
from .node import Node, NodeState, TERMINAL_STATES
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `python -m pytest tests/test_basic.py tests/test_wait_all.py -v`
Expected: 全部 passed（基础 3 + 端到端 4 + wait_all 3 = 10）

也跑一遍全量回归：
Run: `python -m pytest -v`
Expected: 全部 passed

- [ ] **Step 6: 提交**

```bash
git add dagflow/flow.py dagflow/node.py tests/test_basic.py tests/test_wait_all.py
git commit -m "实现单节点调度与 wait_all

补完 _try_schedule 的 DONE 路径、_run 成功路径、_on_task_done、wait_all。
SKIPPED/FAILED 路径在后续任务补充。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 多父依赖（chain、diamond、kwargs、同 handle 多次）

**Files:**
- Modify: `tests/test_basic.py`（追加依赖测试）

实现层面 `_run` 已经支持 `parent_args` / `parent_kwargs`；本任务只是补测试，验证 happy path 在多种依赖形状下都正确。

- [ ] **Step 1: 追加测试到 `tests/test_basic.py`**

```python
async def test_chain_two_nodes_positional():
    flow = Flow()

    async def head():
        return 7

    async def tail(x):
        return x * 2

    h1 = flow.submit(head)
    h2 = flow.submit(tail, h1)
    await flow.wait_all()
    assert (await h2) == 14


async def test_chain_two_nodes_kwargs():
    flow = Flow()

    async def head():
        return 7

    async def tail(value):
        return value + 1

    h1 = flow.submit(head)
    h2 = flow.submit(tail, value=h1)
    await flow.wait_all()
    assert (await h2) == 8


async def test_diamond_dependency():
    flow = Flow()

    async def root():
        return 10

    async def left(x):
        return x + 1

    async def right(x):
        return x + 2

    async def join(a, b):
        return a * b

    h_root = flow.submit(root)
    h_left = flow.submit(left, h_root)
    h_right = flow.submit(right, h_root)
    h_join = flow.submit(join, h_left, h_right)
    await flow.wait_all()
    assert (await h_join) == 11 * 12  # 132


async def test_multi_parent_positional_varargs():
    flow = Flow()

    async def src(n):
        return n

    async def collect(*vals):
        return sum(vals)

    parents = [flow.submit(__import__("functools").partial(src, i)) for i in range(5)]
    total = flow.submit(collect, *parents)
    await flow.wait_all()
    assert (await total) == 0 + 1 + 2 + 3 + 4


async def test_same_handle_multiple_times_as_parent():
    flow = Flow()

    async def base():
        return 3

    async def double(a, b):
        return a + b

    h = flow.submit(base)
    twice = flow.submit(double, h, h)
    await flow.wait_all()
    assert (await twice) == 6
    # parents 是 frozenset，去重后只算一个父
    assert len(twice._node.parents) == 1
```

- [ ] **Step 2: 跑测试，确认通过（实现已就绪）**

Run: `python -m pytest tests/test_basic.py -v`
Expected: 全部 passed（原 7 + 新 5 = 12）

如果有失败，重点检查 `_run` 里 `real_args = tuple(p.result for p in node.parent_args)` 是否实际把父结果展开传给了 fn。

- [ ] **Step 3: 提交**

```bash
git add tests/test_basic.py
git commit -m "覆盖多父依赖：chain / diamond / varargs / kwargs / 同 handle 多次

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 失败传染（FAILED + SKIPPED + __cause__ 链）

**Files:**
- Modify: `dagflow/flow.py`（在 `_run` 加 except 分支；新增 `_mark_skipped`；在 `_try_schedule` 加 SKIPPED 检测）
- Create: `tests/test_failure.py`

- [ ] **Step 1: 写失败测试 `tests/test_failure.py`**

```python
"""失败传染：FAILED 节点的下游全部 SKIPPED；__cause__ 链能追根因。"""
from functools import partial

import pytest

from dagflow import Flow, NodeFailed, NodeSkipped, NodeState


def _root_cause(e: BaseException) -> BaseException:
    while e.__cause__ is not None:
        e = e.__cause__
    return e


async def test_single_node_failure_raises_node_failed():
    flow = Flow()

    async def bad():
        raise ValueError("kaboom")

    h = flow.submit(bad)
    await flow.wait_all()
    assert h.state is NodeState.FAILED
    with pytest.raises(NodeFailed) as exc_info:
        await h
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "kaboom" in str(exc_info.value.__cause__)


async def test_downstream_is_skipped_when_parent_fails():
    flow = Flow()

    async def bad():
        raise RuntimeError("upstream broke")

    async def child(x):
        return x + 1  # 不应被执行

    h_bad = flow.submit(bad)
    h_child = flow.submit(child, h_bad)
    await flow.wait_all()
    assert h_bad.state is NodeState.FAILED
    assert h_child.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped):
        await h_child


async def test_skip_propagates_through_multiple_layers_and_cause_chains_to_root():
    flow = Flow()

    async def a():
        raise ValueError("root")

    async def b(x):
        return x

    async def c(x):
        return x

    async def d(x):
        return x

    h_a = flow.submit(a)
    h_b = flow.submit(b, h_a)
    h_c = flow.submit(c, h_b)
    h_d = flow.submit(d, h_c)
    await flow.wait_all()
    assert h_a.state is NodeState.FAILED
    assert h_b.state is NodeState.SKIPPED
    assert h_c.state is NodeState.SKIPPED
    assert h_d.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped) as exc_info:
        await h_d
    root = _root_cause(exc_info.value)
    assert isinstance(root, ValueError)
    assert str(root) == "root"


async def test_independent_branch_unaffected_by_failure():
    flow = Flow()

    async def good():
        return "ok"

    async def bad():
        raise ValueError("nope")

    h_good = flow.submit(good)
    h_bad = flow.submit(bad)
    await flow.wait_all()
    assert (await h_good) == "ok"
    assert h_bad.state is NodeState.FAILED
    with pytest.raises(NodeFailed):
        await h_bad


async def test_submit_after_parent_already_failed_marks_skipped_immediately():
    flow = Flow()

    async def bad():
        raise ValueError("late")

    async def child(x):
        return x

    h_bad = flow.submit(bad)
    # 等父跑完
    await flow.wait_all()
    assert h_bad.state.name == "FAILED"
    # 现在 submit 一个依赖它的子，应在 submit 内部立刻 SKIPPED
    h_child = flow.submit(child, h_bad)
    assert h_child.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped):
        await h_child
    # wait_all 应立刻返回
    await flow.wait_all()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_failure.py -v`
Expected: 全部失败（_run 没有 except 分支，节点抛异常 → task 异常 → wait_all hang 或 unhandled exception）

- [ ] **Step 3: 修改 `dagflow/flow.py`**

`_run` 加 except 分支（保留之前的 try/finally 结构）：

替换 `_run` 整个方法为：

```python
    async def _run(self, node: Node) -> None:
        node.state = NodeState.RUNNING
        try:
            real_args = tuple(p.result for p in node.parent_args)
            real_kwargs = {k: p.result for k, p in node.parent_kwargs.items()}
            value = await node.fn(*real_args, **real_kwargs)
            node.result = value
            node.state = NodeState.DONE
            node.future.set_result(value)
        except asyncio.CancelledError:
            node.state = NodeState.CANCELLED
            node.future.cancel()
            raise
        except BaseException as e:
            node.exception = e
            node.state = NodeState.FAILED
            nf = NodeFailed(node.id)
            nf.__cause__ = e
            node.future.set_exception(nf)
        finally:
            self._wake_children(node)
```

`_try_schedule` 替换为完整版本（加 SKIPPED 检测）：

```python
    def _try_schedule(self, node: Node) -> None:
        if any(
            p.state in (NodeState.FAILED, NodeState.SKIPPED, NodeState.CANCELLED)
            for p in node.parents
        ):
            self._mark_skipped(node)
        elif all(p.state is NodeState.DONE for p in node.parents):
            node.state = NodeState.READY
            task = asyncio.create_task(self._run(node))
            task.add_done_callback(self._on_task_done)
            self._inflight.add(task)
```

新增 `_mark_skipped` 方法：

```python
    def _mark_skipped(self, node: Node) -> None:
        node.state = NodeState.SKIPPED
        cause_node = next(
            (
                p
                for p in node.parents
                if p.state
                in (NodeState.FAILED, NodeState.CANCELLED, NodeState.SKIPPED)
            ),
            None,
        )
        ns = NodeSkipped(node.id)
        if cause_node is not None:
            # 用"最近一个失败/跳过的父"的 future 异常作 __cause__；
            # 若该父也是 SKIPPED，其异常自带 __cause__，链条自然延续到根因。
            try:
                cause_node.future.result()
            except BaseException as e:
                ns.__cause__ = e
        node.future.set_exception(ns)
        self._wake_children(node)
```

在 `dagflow/flow.py` 顶部 import 里追加：

```python
from .errors import NodeFailed, NodeSkipped
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_failure.py -v`
Expected: 5 passed

全量回归：
Run: `python -m pytest -v`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add dagflow/flow.py tests/test_failure.py
git commit -m "实现失败传染：FAILED 节点的下游变 SKIPPED

_run 加 except 分支抛 NodeFailed；_try_schedule 增加 SKIPPED 检测路径；
新增 _mark_skipped 复用上游 future 异常组装 __cause__ 链。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 外部 cancel 触发 CANCELLED + 下游 SKIPPED

**Files:**
- Create: `tests/test_cancellation.py`

实现层面 `_run` 的 CancelledError 分支已就绪，本任务只补测试。

- [ ] **Step 1: 写测试 `tests/test_cancellation.py`**

```python
"""节点 task 被外部 cancel：节点状态 CANCELLED，下游 SKIPPED。"""
import asyncio

import pytest

from dagflow import Flow, NodeSkipped, NodeState


async def test_running_task_cancelled_transitions_to_cancelled():
    flow = Flow()
    started = asyncio.Event()

    async def long_running():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise
        return "should not reach"

    async def child(_):
        return "downstream"

    h = flow.submit(long_running)
    h_child = flow.submit(child, h)

    # 等 long_running 真的跑起来
    await started.wait()
    # 拿到那个 task 并 cancel
    assert len(flow._inflight) >= 1
    target_task = next(iter(flow._inflight))
    target_task.cancel()

    await flow.wait_all()

    assert h.state is NodeState.CANCELLED
    assert h_child.state is NodeState.SKIPPED
    with pytest.raises(NodeSkipped) as exc_info:
        await h_child
    # __cause__ 应是 CancelledError
    cause = exc_info.value.__cause__
    assert isinstance(cause, asyncio.CancelledError)


async def test_await_handle_of_cancelled_raises_cancelled_error():
    flow = Flow()
    started = asyncio.Event()

    async def long_running():
        started.set()
        await asyncio.sleep(10)

    h = flow.submit(long_running)
    await started.wait()
    next(iter(flow._inflight)).cancel()
    await flow.wait_all()
    assert h.state is NodeState.CANCELLED
    with pytest.raises(asyncio.CancelledError):
        await h
```

注意：测试里访问了 `flow._inflight` 这个私有字段。仅在测试中可接受（生产代码不应依赖）。如需更整洁，可在 Flow 上加一个测试辅助方法，但 v1 保持简单。

- [ ] **Step 2: 跑测试，确认通过**

Run: `python -m pytest tests/test_cancellation.py -v`
Expected: 2 passed

- [ ] **Step 3: 提交**

```bash
git add tests/test_cancellation.py
git commit -m "覆盖外部 cancel：节点 CANCELLED，下游 SKIPPED

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 动态 submission 行为（控制器看到中间结果再 submit）

**Files:**
- Create: `tests/test_dynamic.py`

- [ ] **Step 1: 写测试 `tests/test_dynamic.py`**

```python
"""控制器在运行时基于中间结果动态 submit 后续节点。"""
import asyncio
from functools import partial

import pytest

from dagflow import Flow, NodeState


async def test_controller_branches_on_intermediate_result():
    flow = Flow()

    async def classify():
        return "simple"

    async def quick_path():
        return "quick-ok"

    async def deep_path():
        return "deep-ok"

    probe = flow.submit(classify)
    kind = await probe
    if kind == "simple":
        out = flow.submit(quick_path)
    else:
        out = flow.submit(deep_path)

    await flow.wait_all()
    assert (await out) == "quick-ok"


async def test_submit_when_parent_already_done_schedules_immediately():
    flow = Flow()

    async def parent():
        return "p"

    async def child(x):
        return x + "-c"

    h_parent = flow.submit(parent)
    # 等父完成
    await h_parent
    assert h_parent.state is NodeState.DONE
    # 此时父已 DONE；submit child 后状态应该立刻 READY 或更深（不会停在 PENDING）
    h_child = flow.submit(child, h_parent)
    assert h_child.state is not NodeState.PENDING
    await flow.wait_all()
    assert (await h_child) == "p-c"


async def test_submit_then_wait_all_then_submit_again():
    flow = Flow()

    async def make(n):
        return n

    h1 = flow.submit(partial(make, 1))
    await flow.wait_all()
    assert (await h1) == 1

    h2 = flow.submit(partial(make, 2))
    await flow.wait_all()
    assert (await h2) == 2
    # 第一个 handle 仍然可用，结果不变
    assert (await h1) == 1
```

- [ ] **Step 2: 跑测试，确认通过**

Run: `python -m pytest tests/test_dynamic.py -v`
Expected: 3 passed

- [ ] **Step 3: 提交**

```bash
git add tests/test_dynamic.py
git commit -m "覆盖动态 submission：基于中间结果分支、父已完成时立即调度、wait_all 后续续 submit

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: wait_all 期间并发 submit 也会被等到

**Files:**
- Modify: `tests/test_wait_all.py`

- [ ] **Step 1: 追加测试到 `tests/test_wait_all.py`**

```python
async def test_wait_all_waits_for_nodes_added_concurrently():
    flow = Flow()

    async def slow():
        await asyncio.sleep(0.05)
        return "slow"

    async def fast():
        return "fast"

    # 先放一个慢节点
    h_slow = flow.submit(slow)

    # 在 wait_all 期间从另一个 task 加节点
    async def add_more_later():
        await asyncio.sleep(0.02)  # 在 wait_all 启动后
        return flow.submit(fast)

    waiter_task = asyncio.create_task(flow.wait_all())
    h_fast = await add_more_later()

    # wait_all 应等到 fast 也完成
    await waiter_task
    assert h_slow.state.name == "DONE"
    assert h_fast.state.name == "DONE"
    assert (await h_fast) == "fast"


async def test_wait_all_with_all_skipped_branches():
    flow = Flow()

    async def bad():
        raise ValueError("x")

    async def child(_):
        return "unreachable"

    h_bad = flow.submit(bad)
    h_c1 = flow.submit(child, h_bad)
    h_c2 = flow.submit(child, h_c1)

    # 全 SKIPPED/FAILED 也是终态，wait_all 应正常返回
    await asyncio.wait_for(flow.wait_all(), timeout=1.0)
    assert h_bad.state.name == "FAILED"
    assert h_c1.state.name == "SKIPPED"
    assert h_c2.state.name == "SKIPPED"
```

- [ ] **Step 2: 跑测试，确认通过**

Run: `python -m pytest tests/test_wait_all.py -v`
Expected: 全部 passed（原 3 + 新 2 = 5）

- [ ] **Step 3: 提交**

```bash
git add tests/test_wait_all.py
git commit -m "覆盖 wait_all 等待期间并发 submit、以及全 SKIPPED/FAILED 终态场景

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: 真并行（用 wall time 验证）

**Files:**
- Create: `tests/test_concurrent.py`

- [ ] **Step 1: 写测试 `tests/test_concurrent.py`**

```python
"""多独立分支真并行：用 wall time 验证总耗时约等于最长分支，而非串行总和。"""
import asyncio
import time

import pytest

from dagflow import Flow


async def test_independent_branches_run_in_parallel():
    flow = Flow()

    async def slow_branch(label):
        await asyncio.sleep(0.1)
        return label

    handles = []
    for i in range(5):
        # 用 partial 把字面量绑进去
        from functools import partial
        handles.append(flow.submit(partial(slow_branch, f"b{i}")))

    start = time.perf_counter()
    await flow.wait_all()
    elapsed = time.perf_counter() - start

    # 5 个分支各 0.1s；串行需 0.5s 以上，并行应远小于此
    assert elapsed < 0.3, f"expected parallel execution (~0.1s), got {elapsed:.3f}s"
    for h in handles:
        assert h.state.name == "DONE"


async def test_diamond_overlaps_left_and_right_branches():
    flow = Flow()

    async def root():
        return None

    async def left(_):
        await asyncio.sleep(0.1)
        return "L"

    async def right(_):
        await asyncio.sleep(0.1)
        return "R"

    async def merge(a, b):
        return (a, b)

    h_root = flow.submit(root)
    h_left = flow.submit(left, h_root)
    h_right = flow.submit(right, h_root)
    h_merge = flow.submit(merge, h_left, h_right)

    start = time.perf_counter()
    await flow.wait_all()
    elapsed = time.perf_counter() - start

    # left/right 应并行：总耗时约 0.1s，串行将是 0.2s
    assert elapsed < 0.18, f"expected diamond branches parallel, got {elapsed:.3f}s"
    assert (await h_merge) == ("L", "R")
```

- [ ] **Step 2: 跑测试，确认通过**

Run: `python -m pytest tests/test_concurrent.py -v`
Expected: 2 passed

注：如果 CI 机器很慢导致 wall-time 断言失败，可以把阈值放宽到 0.4 / 0.25——但本地开发机不应触发。

- [ ] **Step 3: 提交**

```bash
git add tests/test_concurrent.py
git commit -m "用 wall time 验证多分支真并行

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: to_dot 可视化辅助

**Files:**
- Modify: `dagflow/flow.py`（追加 `to_dot` 方法和颜色常量）
- Create: `tests/test_visualize.py`

- [ ] **Step 1: 写失败测试 `tests/test_visualize.py`**

```python
"""to_dot：输出 graphviz DOT 字符串，包含节点 id、状态颜色、所有边。"""
from functools import partial

import pytest

from dagflow import Flow


async def test_to_dot_contains_all_node_ids_and_edges():
    flow = Flow()

    async def a():
        return 1

    async def b(x):
        return x + 1

    async def c(x):
        return x + 2

    h_a = flow.submit(a)
    h_b = flow.submit(b, h_a)
    h_c = flow.submit(c, h_a)
    await flow.wait_all()

    dot = flow.to_dot()
    # 节点 id 都出现
    assert h_a.id in dot
    assert h_b.id in dot
    assert h_c.id in dot
    # 边都出现
    assert f'"{h_a.id}" -> "{h_b.id}"' in dot
    assert f'"{h_a.id}" -> "{h_c.id}"' in dot
    # DOT 头尾
    assert dot.startswith("digraph flow {")
    assert dot.rstrip().endswith("}")


async def test_to_dot_colors_reflect_state():
    flow = Flow()

    async def good():
        return "ok"

    async def bad():
        raise ValueError("x")

    async def child(_):
        return "unreachable"

    h_good = flow.submit(good)
    h_bad = flow.submit(bad)
    h_child = flow.submit(child, h_bad)
    await flow.wait_all()

    dot = flow.to_dot()
    # 三种不同状态对应三种 fillcolor
    assert "palegreen" in dot  # DONE
    assert "lightcoral" in dot  # FAILED
    assert "lightgray" in dot  # SKIPPED


async def test_to_dot_on_empty_flow():
    flow = Flow()
    dot = flow.to_dot()
    assert dot.startswith("digraph flow {")
    assert "->" not in dot  # 无边
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_visualize.py -v`
Expected: `AttributeError: 'Flow' object has no attribute 'to_dot'`

- [ ] **Step 3: 修改 `dagflow/flow.py`**

在文件末尾（类内）追加颜色常量和方法。先在 import 区附近加常量：

```python
_DOT_COLOR = {
    NodeState.DONE: "palegreen",
    NodeState.FAILED: "lightcoral",
    NodeState.SKIPPED: "lightgray",
    NodeState.CANCELLED: "khaki",
    NodeState.RUNNING: "lightblue",
    NodeState.READY: "white",
    NodeState.PENDING: "white",
}
```

`_DOT_COLOR` 放在类外（模块级）即可。在 `Flow` 类内追加方法：

```python
    def to_dot(self) -> str:
        """返回当前图状态的 graphviz DOT 字符串。"""
        lines = ["digraph flow {", '  node [shape=box, style=filled];']
        for n in self._nodes:
            label = f"{n.id}\\n{n.state.value}"
            color = _DOT_COLOR[n.state]
            lines.append(f'  "{n.id}" [label="{label}", fillcolor={color}];')
        for n in self._nodes:
            for p in n.parents:
                lines.append(f'  "{p.id}" -> "{n.id}";')
        lines.append("}")
        return "\n".join(lines)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_visualize.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add dagflow/flow.py tests/test_visualize.py
git commit -m "实现 to_dot 可视化辅助

输出 graphviz DOT 字符串，按状态着色；不依赖 graphviz Python 包。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: E2E 冒烟测试

**Files:**
- Create: `tests/test_e2e_smoke.py`

构造 spec §8.3 描述的 6 节点流程图：

```
          load_a ──┐
                   ├─► merge_ab ──┐
          load_b ──┘              │
                                  ├─► report
   load_c (fails) ──► transform_c ─┘ (SKIPPED)
                                       │
                              (report 因 transform_c SKIPPED 而 SKIPPED)
   load_d (independent) ──► transform_d ──► save_d   (这一支照常成功)
```

注意：spec 的图描述里写了 6 个节点（load_a/b/c/d、merge_ab、transform_c、report、transform_d、save_d）——实际是 9 个。我们就实现 9 个，并相应断言。

- [ ] **Step 1: 写测试 `tests/test_e2e_smoke.py`**

```python
"""端到端冒烟：一个真实形状的小流程，覆盖 DONE/FAILED/SKIPPED 三种终态。"""
from functools import partial

import pytest

from dagflow import Flow, NodeFailed, NodeSkipped, NodeState


def _root_cause(e: BaseException) -> BaseException:
    while e.__cause__ is not None:
        e = e.__cause__
    return e


async def test_e2e_smoke_mixed_outcomes():
    flow = Flow()

    async def load(label):
        return {"label": label, "data": [1, 2, 3]}

    async def load_failing(label):
        raise IOError(f"cannot load {label}")

    async def merge(a, b):
        return {"merged": (a["label"], b["label"])}

    async def transform(payload):
        return {**payload, "transformed": True}

    async def report(left, right):
        return {"summary": (left, right)}

    async def save(payload):
        return {"saved": True, "from": payload.get("label")}

    # 主分支：load_a + load_b -> merge_ab -> report
    h_load_a = flow.submit(partial(load, "a"))
    h_load_b = flow.submit(partial(load, "b"))
    h_merge_ab = flow.submit(merge, h_load_a, h_load_b)

    # 失败分支：load_c (FAILED) -> transform_c (SKIPPED) -> 被 report 依赖
    h_load_c = flow.submit(partial(load_failing, "c"))
    h_transform_c = flow.submit(transform, h_load_c)

    # report 依赖 merge_ab 和 transform_c —— 由于 transform_c 会 SKIPPED，report 也 SKIPPED
    h_report = flow.submit(report, h_merge_ab, h_transform_c)

    # 无关分支：load_d -> transform_d -> save_d，全程成功
    h_load_d = flow.submit(partial(load, "d"))
    h_transform_d = flow.submit(transform, h_load_d)
    h_save_d = flow.submit(save, h_transform_d)

    await flow.wait_all()

    # 状态断言
    assert h_load_a.state is NodeState.DONE
    assert h_load_b.state is NodeState.DONE
    assert h_merge_ab.state is NodeState.DONE

    assert h_load_c.state is NodeState.FAILED
    assert h_transform_c.state is NodeState.SKIPPED
    assert h_report.state is NodeState.SKIPPED

    assert h_load_d.state is NodeState.DONE
    assert h_transform_d.state is NodeState.DONE
    assert h_save_d.state is NodeState.DONE

    # 成功路径结果
    saved = await h_save_d
    assert saved["saved"] is True
    assert saved["from"] == "d"

    # 失败传染：report 抛 NodeSkipped，根因是 IOError
    with pytest.raises(NodeSkipped) as exc_info:
        await h_report
    root = _root_cause(exc_info.value)
    assert isinstance(root, IOError)
    assert "cannot load c" in str(root)

    # to_dot 覆盖所有节点和边
    dot = flow.to_dot()
    for h in [h_load_a, h_load_b, h_merge_ab,
              h_load_c, h_transform_c, h_report,
              h_load_d, h_transform_d, h_save_d]:
        assert h.id in dot
    # 边总数：merge_ab(2) + transform_c(1) + report(2) + transform_d(1) + save_d(1) = 7
    assert dot.count(" -> ") == 7
```

- [ ] **Step 2: 跑测试，确认通过**

Run: `python -m pytest tests/test_e2e_smoke.py -v`
Expected: 1 passed

- [ ] **Step 3: 全量回归**

Run: `python -m pytest -v`
Expected: 全部 passed。汇总数量约：
- test_handle.py: 4 errors + 7 handle = 11
- test_basic.py: 3 node/state + 4 single-node + 5 multi-parent = 12
- test_validation.py: 6
- test_wait_all.py: 3 + 2 = 5
- test_failure.py: 5
- test_cancellation.py: 2
- test_dynamic.py: 3
- test_concurrent.py: 2
- test_visualize.py: 3
- test_e2e_smoke.py: 1
- 合计约 50 个测试

- [ ] **Step 4: 提交**

```bash
git add tests/test_e2e_smoke.py
git commit -m "添加端到端冒烟测试

9 节点真实流程覆盖 DONE/FAILED/SKIPPED 三种终态，
验证 __cause__ 链根因和 to_dot 完整性。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## 完成验证清单

实施完所有任务后，确认：

- [ ] `python -m pytest -v` 全绿
- [ ] `python -c "from dagflow import Flow, NodeHandle, NodeState, NodeFailed, NodeSkipped; print('ok')"` 输出 `ok`
- [ ] `git log --oneline` 能看到 14 个 task commit + 之前的 spec commit
- [ ] 在 Linux 上跑一遍同样的 `pytest -v`（spec §10 要求生产 Ubuntu 兼容；纯 asyncio 应该可移植，但建议至少跑一次 CI/手工验证）

---

## 已知后续工作（不在本计划范围）

下列项 spec 明确列为非目标或后续考虑，本计划**不实现**：

- 内置并发限流（`max_concurrency`）—— 用户用 `asyncio.Semaphore` 自控
- 内置重试 / 超时装饰器
- 持久化、断点续跑
- 多机分布式
- 静态类型检查（mypy）
- `submit(..., name=...)` 显式命名（当前 partial 节点 id 退化为 `repr`）
- 事件钩子 / 监听系统

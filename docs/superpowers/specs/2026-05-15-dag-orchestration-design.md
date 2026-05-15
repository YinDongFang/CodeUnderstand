# DAG 异步任务编排框架 —— 设计文档

> 日期：2026-05-15
> 状态：Draft（已与用户对齐）

---

## 1. 目标与定位

提供一个**通用的、纯 asyncio、单进程**的有向无环图（DAG）任务编排库。每个图节点是一个 `async` 函数；子节点的入参由父节点的运行结果按**参数名**注入。

库的形态是"延迟执行 + 自动依赖发现"：用户在外部代码（控制器）里像调用普通函数一样调 `flow.submit(fn, *args, **kwargs)`，参数里如果出现 `NodeHandle`，框架就把它识别为父依赖；否则当作字面量。

### 1.1 非目标 / 显式不做

- 多机分布式、跨进程持久化、断点续跑
- 多进程 / 多线程混合调度（节点只能是 `async def`）
- 静态预声明整个图（图是动态生长的；见 §3）
- 性能优化：**全图节点数预期 ≤ 50**，所有 O(N) 扫描可接受，不做反向边索引等
- 内置并发限流（用户可在节点 fn 内用 `asyncio.Semaphore` 自控）
- 内置重试 / 超时（同上，用户自己写装饰器或在节点 fn 内处理）
- 环检测（图按"父必须已存在"的方式生长，天然无环）
- mypy / 静态类型检查作为门槛
- 内置事件钩子 / 监听系统

---

## 2. 设计约束（来自需求确认）

| 维度 | 决定 |
| --- | --- |
| 场景 | 通用异步任务编排（库性质） |
| 运行时 | 纯 asyncio，单进程 |
| 父→子结果传递 | 按子节点形参名注入 |
| 图构建方式 | 外部控制器在运行时动态 `submit`；每次 submit 时父必须已存在（天然无环） |
| 失败传染 | 节点抛异常 → 直接下游 SKIPPED → 一直传染；无关分支照常 |
| 终止 | 外部控制器调 `await flow.join()`；不需要 `close` |
| 并发上限 | 不设限，交给 asyncio |
| 节点规模 | ≤ 50 |
| 开发环境 | Windows + python3（启动命令 `python`），已配置 pip 镜像源 |
| 生产环境 | Ubuntu + python3 |

---

## 3. 架构与分层

```
┌─────────────────────────────────────────────────┐
│  Flow (orchestrator)                            │
│  - submit / join / to_dot / nodes               │
│  - 维护 nodes 集合 + 就绪检测 + 状态机           │
│  - 父完成时唤醒下游                              │
└──────────────────┬──────────────────────────────┘
                   │ 拥有
                   ▼
┌─────────────────────────────────────────────────┐
│  Node (data)              NodeHandle (proxy)    │
│  - id, fn, args/kwargs    - 不可变、可哈希、     │
│  - parents (推导出)         awaitable           │
│  - state, result, exc     - submit 的返回值     │
│  - asyncio.Future         - 控制器据此构图       │
└─────────────────────────────────────────────────┘
```

- `Node` 是纯数据 + 一个 `asyncio.Future`，不知道"图"的存在。
- `NodeHandle` 是 `Node` 的对外不可变引用，可哈希、可 `await`，控制器和子节点 args 里用的都是它。
- `Flow` 是调度器，扫描 submit 的参数发现依赖、跑就绪节点、传染失败、提供 `join` 和 `to_dot`。

### 3.1 文件结构

```
dagflow/
  __init__.py        # 公开：Flow, NodeHandle, NodeState, NodeFailed, NodeSkipped
  node.py            # Node, NodeState
  handle.py          # NodeHandle
  flow.py            # Flow
  errors.py          # NodeFailed, NodeSkipped
tests/
  test_basic.py
  test_failure.py
  test_dynamic.py
  test_join.py
  test_concurrent.py
  test_visualize.py
```

---

## 4. 核心数据结构

### 4.1 `NodeState`

```python
class NodeState(str, Enum):
    PENDING   = "pending"     # 父依赖未全完成
    READY     = "ready"       # 父全 DONE，等 scheduler 起 task（瞬时态）
    RUNNING   = "running"
    DONE      = "done"        # 成功终态
    FAILED    = "failed"      # fn 抛异常
    SKIPPED   = "skipped"     # 上游 FAILED/CANCELLED 传染
    CANCELLED = "cancelled"   # 任务被外部 cancel
```

终态集合：`{DONE, FAILED, SKIPPED, CANCELLED}`。

`READY` 在实现中通常一闪而过——`_try_schedule` 判定父全 DONE 后立刻 `create_task` 进入 `RUNNING`。它保留为状态机的显式步骤、便于推理和调试。

### 4.2 `Node`（内部）

```python
@dataclass
class Node:
    id: str                                    # 自动生成，例 "fetch#3"
    fn: Callable[..., Awaitable]
    args: tuple                                # 原始 args（可能含 NodeHandle 占位）
    kwargs: dict                               # 原始 kwargs
    parents: frozenset["Node"]                 # submit 时从 args/kwargs 抽出
    state: NodeState = NodeState.PENDING
    result: Any = None
    exception: BaseException | None = None
    future: asyncio.Future = field(init=False)
```

### 4.3 `NodeHandle`（对外）

```python
class NodeHandle(Generic[T]):
    __slots__ = ("_node",)
    def __init__(self, node: Node): self._node = node

    def __await__(self):
        return self._node.future.__await__()

    @property
    def state(self) -> NodeState: return self._node.state
    @property
    def id(self) -> str: return self._node.id

    def __hash__(self):  return id(self._node)
    def __eq__(self, o): return isinstance(o, NodeHandle) and o._node is self._node
    def __repr__(self):  return f"<NodeHandle {self._node.id} {self._node.state.value}>"
```

- 故意**不暴露** `result` / `exception` 字段。读取结果**只能 `await`**——一律走 Future 语义，所有重复 await 行为一致。
- `await handle` 行为表：

  | node.state  | `await handle` 行为 |
  | ----------- | ------------------- |
  | DONE        | 返回 `result` |
  | FAILED      | 抛 `NodeFailed`（`__cause__` = 原异常） |
  | SKIPPED     | 抛 `NodeSkipped`（`__cause__` = 最近一个失败上游的 `NodeFailed`） |
  | CANCELLED   | 抛 `asyncio.CancelledError` |
  | 未终态       | 挂起等终态 |

### 4.4 异常

```python
class NodeFailed(Exception):
    """节点 fn 抛异常时由框架抛出，__cause__ 指向 fn 抛的原异常"""

class NodeSkipped(Exception):
    """因上游 FAILED/CANCELLED 被跳过，__cause__ 指向最近的失败上游 NodeFailed"""
```

`__cause__` 链可一路追溯到根因（A 失败 → B/C/D SKIPPED；在 D 上 `await` 抛 NodeSkipped，`__cause__` = C 节点的 NodeSkipped，`__cause__.__cause__` = B 的 NodeSkipped，最终能定位到 A 的原异常）。

---

## 5. 调度执行模型

### 5.1 Flow 内部状态

```python
class Flow:
    def __init__(self) -> None:
        self._nodes: list[Node] = []
        self._inflight: set[asyncio.Task] = set()
        self._id_counter: dict[str, int] = {}        # fn.__name__ -> seq
```

### 5.2 submit

```python
def submit(self, fn, /, *args, **kwargs) -> NodeHandle:
    parents_handles = [x for x in chain(args, kwargs.values())
                       if isinstance(x, NodeHandle)]
    node = Node(
        id      = self._next_id(fn),
        fn      = fn,
        args    = args,
        kwargs  = kwargs,
        parents = frozenset(h._node for h in parents_handles),
    )
    node.future = asyncio.get_event_loop().create_future()
    self._nodes.append(node)
    self._try_schedule(node)
    return NodeHandle(node)
```

**依赖识别规则**（重要）：只看**顶层** args/kwargs；嵌套结构里的 NodeHandle 不会被识别为父。需要"一组 handle 全部解析后传进 fn" 的话，写一个聚合节点：

```python
flow.submit(lambda *xs: list(xs), h1, h2, h3)
```

理由：浅层规则简单、可预测、和函数签名直接对齐。

### 5.3 _try_schedule

```python
def _try_schedule(self, node: Node) -> None:
    if any(p.state in (NodeState.FAILED, NodeState.SKIPPED, NodeState.CANCELLED)
           for p in node.parents):
        self._mark_skipped(node)
    elif all(p.state is NodeState.DONE for p in node.parents):
        node.state = NodeState.READY
        task = asyncio.create_task(self._run(node))
        task.add_done_callback(self._on_task_done)
        self._inflight.add(task)
    # else: 仍 PENDING，等父唤醒
```

### 5.4 _run

```python
async def _run(self, node: Node) -> None:
    node.state = NodeState.RUNNING
    try:
        real_args   = [a._node.result if isinstance(a, NodeHandle) else a
                       for a in node.args]
        real_kwargs = {k: (v._node.result if isinstance(v, NodeHandle) else v)
                       for k, v in node.kwargs.items()}
        value = await node.fn(*real_args, **real_kwargs)
        node.result = value
        node.state  = NodeState.DONE
        node.future.set_result(value)
    except asyncio.CancelledError:
        node.state = NodeState.CANCELLED
        node.future.cancel()
        raise
    except BaseException as e:
        node.exception = e
        node.state     = NodeState.FAILED
        nf = NodeFailed(node.id)
        nf.__cause__ = e
        node.future.set_exception(nf)
    finally:
        self._wake_children(node)
```

### 5.5 _mark_skipped

```python
def _mark_skipped(self, node: Node) -> None:
    node.state = NodeState.SKIPPED
    cause_node = next((p for p in node.parents
                       if p.state in (NodeState.FAILED, NodeState.CANCELLED,
                                      NodeState.SKIPPED)), None)
    ns = NodeSkipped(node.id)
    if cause_node is not None:
        # 直接挂"最近一个失败/跳过的父"的 future 异常，链式回溯根因
        try:
            cause_node.future.result()
        except BaseException as e:
            ns.__cause__ = e
    node.future.set_exception(ns)
    self._wake_children(node)
```

### 5.6 _wake_children

```python
def _wake_children(self, parent: Node) -> None:
    for n in self._nodes:
        if n.state is NodeState.PENDING and parent in n.parents:
            self._try_schedule(n)
```

N ≤ 50，O(N) 扫描可接受。

### 5.7 _on_task_done

```python
def _on_task_done(self, task: asyncio.Task) -> None:
    self._inflight.discard(task)
```

状态转换已经在 `_run` 的 try/finally 里完成，callback 只清理 `_inflight`。

### 5.8 join

```python
async def join(self) -> None:
    TERMINAL = {NodeState.DONE, NodeState.FAILED,
                NodeState.SKIPPED, NodeState.CANCELLED}
    while any(n.state not in TERMINAL for n in self._nodes):
        if self._inflight:
            await asyncio.wait(self._inflight,
                               return_when=asyncio.FIRST_COMPLETED)
            self._inflight = {t for t in self._inflight if not t.done()}
        else:
            # 防御：READY 起 task 是同步的，不会停在这里；
            # 但保留这一支防止状态机将来扩展时死循环
            await asyncio.sleep(0)
```

### 5.9 关键时序保证

- `submit` 返回前，节点已 append 进 `_nodes`，且若父全已终态则状态已不再 PENDING（要么 READY/RUNNING/DONE，要么 SKIPPED）——所以 `await handle` 不会卡在"节点根本没被调度过"。
- 状态转换发生在 `_run` 的 try/finally 内，不依赖 task 的 done callback 顺序——`join` 的"是否还有活"判定永远基于已经写好的状态。
- 控制器在 `_run` 进行中调 `submit` 是合法的（与节点 fn 内 await handle 不同——见 §7 风险点）。

---

## 6. 公开 API 表面

```python
# dagflow/__init__.py
from .flow import Flow
from .handle import NodeHandle
from .node import NodeState
from .errors import NodeFailed, NodeSkipped

class Flow:
    def __init__(self) -> None: ...
    def submit(self, fn: Callable[..., Awaitable[T]], /, *args, **kwargs) -> NodeHandle[T]: ...
    async def join(self) -> None: ...
    def to_dot(self) -> str: ...
    @property
    def nodes(self) -> tuple[NodeHandle, ...]: ...

class NodeHandle(Generic[T]):
    def __await__(self) -> T: ...
    @property
    def state(self) -> NodeState: ...
    @property
    def id(self) -> str: ...
```

### 6.1 典型用法

**(1) 扁平 fan-out + 聚合**
```python
flow = Flow()
pages  = [flow.submit(fetch, u) for u in urls]
parsed = [flow.submit(parse, p) for p in pages]
final  = flow.submit(merge, *parsed)
await flow.join()
print(await final)
```

**(2) 控制器看到中间结果再决定下一步**
```python
flow = Flow()
probe = flow.submit(classify, doc)
kind  = await probe                       # 直接 await，不需要 join

if kind == "simple":
    out = flow.submit(quick_path, doc)
else:
    a = flow.submit(deep_step_a, doc)
    b = flow.submit(deep_step_b, a)
    out = flow.submit(merge, a, b)

await flow.join()
print(await out)
```

**(3) 忽略失败分支**
```python
hs = [flow.submit(process, item) for item in items]
await flow.join()
ok = []
for h in hs:
    try:
        ok.append(await h)
    except (NodeFailed, NodeSkipped):
        pass
```

### 6.2 边界行为表

| 操作 | 行为 |
| --- | --- |
| `submit` 后立刻 `await handle`，不调 `join` | 阻塞直到节点终态，支持 |
| 多次 `await flow.join()` | 每次都等当前未终态节点跑完；全终态则立刻返回 |
| `join` 返回后再 `submit` | 完全合法 |
| 重复 `await` 同一 handle | 返回相同结果 / 抛相同异常（Future 语义） |
| 没 submit 任何节点就 join | 立刻返回 |
| `await handle` 时节点 SKIPPED | 抛 `NodeSkipped`，`__cause__` 链可回溯根因 |
| 节点 fn 内部 `await` 别的 handle（绕过 submit 依赖） | 允许但风险自负——见 §7 |
| 节点 fn 是同步函数 | 不支持，`await fn(...)` 抛 TypeError |

### 6.3 不变量（测试据此验证）

1. `submit` 返回前节点已登记；父全终态则状态已不再 PENDING。
2. `await flow.join()` 返回时，进入 join 那一刻 `flow.nodes` 里的所有节点都在终态。

---

## 7. 风险点与显式权衡

| 风险 | 决定 |
| --- | --- |
| 节点 fn 内部 `await` 别的 handle 绕过依赖系统 | 允许但不推荐；若那条边不在调度图里，可能死锁。文档明确说明，不在框架里防御。 |
| 子节点参数名与父节点 id 不必一致 | 一致。"按参数名注入"指的是 fn 形参名↔ submit 传入的关键字名匹配（Python 调用语义），不涉及节点 id。 |
| `to_dot` 在节点未终态时也能调 | 可以。会输出当前状态色（pending/running/...），方便实时调试。 |
| asyncio 默认 event loop 在不同平台行为差异（Win vs Ubuntu） | 不做特殊处理；纯 asyncio 标准 API。 |
| 节点 fn 抛 `BaseException` 子类（如 `SystemExit`） | 统一按 FAILED 处理，包成 `NodeFailed`；但 `CancelledError` 单独走 CANCELLED 分支（asyncio 语义要求重新抛出）。 |

---

## 8. 测试策略

全部 `pytest-asyncio` 功能测试，单元/集成不强分。

| 文件 | 测试要点 |
| --- | --- |
| `test_basic.py` | 单节点跑通 / 链式两节点 / 钻石依赖 / 字面量参数与 NodeHandle 混用 / 位置和关键字均支持 |
| `test_failure.py` | 单节点失败 → `await handle` 抛 NodeFailed / 失败传染下游 SKIPPED / `__cause__` 链能追根因 / 一支失败不影响并行无关分支 |
| `test_dynamic.py` | 控制器 await 中间结果再 submit / submit 时父已终态立刻 READY / submit 时父已 FAILED 立刻 SKIPPED 不起 task / join 返回后继续 submit |
| `test_join.py` | 空 flow join 立刻返回 / 多次 join 幂等 / 节点跑到一半 join 等待终态 / 全 SKIPPED 也是终态 |
| `test_concurrent.py` | 多独立分支真并行：用 `asyncio.sleep` + wall time 验证 ≈ 最长分支而非串行总和 |
| `test_visualize.py` | 3 节点构图调 `to_dot()`，断言出现各节点 id 与 `->` 边；不验证渲染外观 |

### 8.1 几个比较 trickier 的测试

1. **失败传染层数**：A → B → C → D，A 抛 ValueError。assert B/C/D 全 SKIPPED；`await D` 抛 NodeSkipped；沿 `__cause__` 回溯能拿到 A 的原 ValueError。
2. **失败之后 submit**：A 已 FAILED，控制器 `await A` 捕获后 submit 一个新节点 E 依赖 A——E 应在 submit 内部 `_try_schedule` 阶段就判定 SKIPPED，不会起 task，也不会卡 join。
3. **submit-然后立即 await**：`h = flow.submit(slow_fn); result = await h`，不调 join 也能拿结果。
4. **手动 cancel 触发跳过**：从外部 cancel 一个 RUNNING 节点的 task，下游应 SKIPPED，`__cause__` 指向 `CancelledError`。

---

## 9. 可视化辅助：`to_dot`

```python
_COLOR = {
    NodeState.DONE:      "palegreen",
    NodeState.FAILED:    "lightcoral",
    NodeState.SKIPPED:   "lightgray",
    NodeState.CANCELLED: "khaki",
    NodeState.RUNNING:   "lightblue",
    NodeState.READY:     "white",
    NodeState.PENDING:   "white",
}

def to_dot(self) -> str:
    lines = ["digraph flow {", '  node [shape=box, style=filled];']
    for n in self._nodes:
        label = f"{n.id}\\n{n.state.value}"
        lines.append(f'  "{n.id}" [label="{label}", fillcolor={_COLOR[n.state]}];')
    for n in self._nodes:
        for p in n.parents:
            lines.append(f'  "{p.id}" -> "{n.id}";')
    lines.append("}")
    return "\n".join(lines)
```

- 不依赖 `graphviz` Python 包，纯字符串。
- 用户自取输出贴到在线 graphviz 渲染器，或本地 `dot -Tpng` 渲染。

---

## 10. 环境与运行

- **开发**：Windows，python（python3 通过 `python` 启动），已配 pip 镜像源。`pip install pytest pytest-asyncio` 即可。
- **生产**：Ubuntu + python3。库不写文件、不开端口、无平台特例。
- **打包**：`pyproject.toml` 用 `setuptools`，`name = "dagflow"`，`requires-python = ">=3.10"`（用了 `frozenset[Node]`、`X | None` 等语法）。
- 不强制 mypy；不引入额外依赖（`graphviz` 包也不要）。

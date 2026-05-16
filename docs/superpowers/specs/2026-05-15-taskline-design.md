# taskline —— 带检查点的串行异步流水线 设计文档

> 日期：2026-05-15
> 状态：Draft（已与用户对齐）
> 取代：`2026-05-15-dag-orchestration-design.md`（dagflow，并行 DAG 编排）——本设计是对它的需求调整重写

---

## 1. 目标与定位

`taskline` 是一个**纯 asyncio、单进程、串行**的异步任务流水线库，带 JSON 检查点与崩溃恢复。

节点按 `submit` 顺序进入一个自动排空的串行队列，由单个后台 driver 逐个 `await` 执行——同一时刻只有一个节点在跑。每个节点完成后，整个运行状态写入 JSON 持久化文件。若进程因某节点抛异常而崩溃，重启同一程序时加载 JSON、跳过已完成节点（用持久化结果喂给后续节点）、从中断节点恢复执行。

这个库由早期的 `dagflow`（并行 DAG 编排）按需求调整而来。调整后"图"的概念已退化：`submit(fn, *parents)` 中的 parents 不再参与调度，仅用于声明"我要这些前置节点的返回值作为入参"。本质是**带数据依赖声明的有序流水线**。

### 1.1 相比 dagflow 的变化

- **去掉并行**：单 driver 串行执行，无 per-node `asyncio.Task`、无 `_inflight` 集合。
- **去掉就绪计算**：submit 顺序天然保证父节点在子节点之前，无需根据父状态判断能否运行（`_try_schedule` 图调度逻辑删除）。
- **去掉失败传染**：节点抛异常时，异常直接上抛、进程崩溃。无 `NodeFailed`/`NodeSkipped` 包装，无 SKIPPED 传染。
- **去掉取消**：无 CANCELLED 状态、无 cancel API。
- **新增**：JSON 检查点持久化 + 崩溃恢复。
- **新增**：flow 级 before/after hook。
- **改名**：`dagflow` → `taskline`。

### 1.2 非目标 / 显式不做

- 多机分布式、多进程/多线程。
- 并行执行节点。
- 失败传染、节点级重试、超时。
- 外部取消。
- 持久化非 JSON 可序列化的结果（用户负责保证返回值可序列化）。
- 跨不同程序版本的恢复（持久化文件与当前程序的 submit 序列必须一致，否则报错）。
- mypy / 静态类型检查作为门槛。

---

## 2. 设计约束（来自需求确认）

| 维度 | 决定 |
| --- | --- |
| 执行模型 | 自动排空的串行队列，单后台 driver |
| submit | 同步调用，不需 await；可连续 submit |
| 启动 | 无显式 `run()`；第一个 submit 即调度 driver；后续节点自动接力 |
| 节点 fn | `async def`；逐个 `await` 串行执行 |
| 父子顺序 | submit 顺序即执行顺序；父必在子前（submit 子节点前必先 submit 父）|
| 就绪判定 | 不需要——串行 + submit 顺序天然保证 |
| 失败处理 | 节点抛异常 → 不 catch，异常上抛、进程崩溃 |
| 失败后 submit | 不涉及（进程已崩溃）|
| 取消 | 完全去掉 |
| 持久化 | 运行状态写 JSON 文件；路径通过 `Flow(state_path, ...)` **构造参数**传入；**必需** |
| 结果序列化 | 节点返回值**必须 JSON 可序列化** |
| 恢复 | 重启加载 JSON，跳过已完成节点，从中断节点恢复 |
| 成功后 | 持久化文件保留；下次启动全部节点已 DONE → 整个 flow 跳过（幂等）|
| Hook | flow 级 before/after hook，均为 async；接收 `HookContext` 对象 |
| RESUME 标志 | before-hook 的 `resuming` 仅对恢复点那一个节点为 True |
| 节点规模 | 沿用 ≤ 50 假设，所有 O(N) 扫描可接受 |
| 开发环境 | Windows + python3（`python` 启动），已配 pip 镜像源 |
| 生产环境 | Ubuntu + python3 |

---

## 3. 架构与分层

```
┌──────────────────────────────────────────────────┐
│  Flow                                            │
│  - submit / wait_all / to_dot / nodes            │
│  - 单 driver task：按 submit 顺序串行排空队列     │
│  - 每节点完成后 checkpoint 持久化                 │
│  - 调用 before/after hook                        │
└───────┬──────────────────────┬───────────────────┘
        │ 拥有                  │ 委托
        ▼                      ▼
┌──────────────────┐   ┌────────────────────────────┐
│ Node (data)      │   │ persistence (load/save)    │
│ NodeHandle       │   │ HookContext                │
│ NodeState        │   │ StateMismatchError         │
└──────────────────┘   └────────────────────────────┘
```

### 3.1 文件结构

这是一次**重写 + 改名**：旧 `dagflow/` 及其测试整个删除，新建 `taskline/`。

```
taskline/
  __init__.py        # 公开 API：Flow, NodeHandle, NodeState, HookContext, StateMismatchError
  node.py            # NodeState（3 态）, Node
  handle.py          # NodeHandle
  hooks.py           # HookContext
  persistence.py     # load_state / save_state（原子写）
  errors.py          # StateMismatchError
  flow.py            # Flow：driver + 编排 + to_dot
tests/
  test_basic.py
  test_serial_order.py
  test_persistence.py
  test_resume.py
  test_hooks.py
  test_failure.py
  test_env.py
  test_visualize.py
  test_e2e_smoke.py
pyproject.toml       # name = "taskline"
```

每个文件单一职责。

---

## 4. 核心数据结构

### 4.1 `NodeState`

```python
class NodeState(str, Enum):
    PENDING = "pending"     # 已 submit，尚未运行
    RUNNING = "running"     # 当前正在运行（同一时刻至多一个）
    DONE    = "done"        # 成功完成
```

只有三态。节点抛异常时进程崩溃，没有存活的观察者去看"失败态"，故不设 FAILED；恢复时已完成节点直接是 DONE，故不设 SKIPPED。

### 4.2 `Node`（内部）

```python
@dataclass
class Node:
    id: str                                  # 自动生成，如 "fetch#0"
    index: int                               # submit 顺序，持久化对齐用
    fn: Callable[..., Awaitable[Any]]
    parent_args: tuple["Node", ...]           # 位置父依赖
    parent_kwargs: dict[str, "Node"]          # 关键字父依赖
    state: NodeState = NodeState.PENDING
    result: Any = None                        # 必须 JSON 可序列化
    future: asyncio.Future | None = None      # 支撑 awaitable handle

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other
```

无 `exception` 字段（失败不在内存留痕）。无 `parents` frozenset（无图调度；to_dot 的边由 `parent_args` + `parent_kwargs` 推导）。

### 4.3 `NodeHandle`（对外）

```python
class NodeHandle(Generic[T]):
    __slots__ = ("_node",)
    def __init__(self, node: Node) -> None:
        self._node = node

    def __await__(self):
        return self._node.future.__await__()

    @property
    def state(self) -> NodeState: return self._node.state
    @property
    def id(self) -> str: return self._node.id

    def __hash__(self): return id(self._node)
    def __eq__(self, o): return isinstance(o, NodeHandle) and o._node is self._node
    def __repr__(self): return f"<NodeHandle {self._node.id} {self._node.state.value}>"
```

`await handle`：
- 节点 DONE（无论实际运行还是从持久化加载）→ 返回 `result`。
- 节点未完成 → 挂起，直到 driver 完成它。
- 因失败不被框架包装，正常路径下 `await handle` 只产出结果。若 driver 在别处崩溃，driver 会把异常塞进未决 future（见 §7），使 `await handle` 醒来抛出而非 hang。

### 4.4 `HookContext`

```python
@dataclass
class HookContext:
    phase: str             # "before" | "after"
    node_id: str
    index: int             # submit 顺序
    resuming: bool         # True 仅当这是崩溃恢复点节点
    result: Any = None     # 仅 "after" 阶段有值
```

### 4.5 `StateMismatchError`

```python
class StateMismatchError(RuntimeError):
    """持久化文件与当前程序的 submit 序列不一致。"""
```

放在 `taskline/errors.py`。这是本库唯一的自定义异常——`NodeFailed`/`NodeSkipped` 已删除。

---

## 5. 持久化与恢复

### 5.1 路径来源

- 持久化 JSON 文件路径通过 `Flow(state_path, ...)` 构造参数传入。
- **必需**：`state_path` 是位置-或-关键字第一参数，不传 → Python 自带 `TypeError: missing 1 required positional argument: 'state_path'`。
- 类型：实现签名标注为 `str`，但运行时也接受 `pathlib.Path`（构造时 `str(state_path)` 归一化）。

### 5.2 文件格式

```json
{
  "version": 1,
  "nodes": [
    {"id": "fetch#0", "result": {"url": "...", "body": "..."}},
    {"id": "parse#0", "result": [1, 2, 3]}
  ]
}
```

`nodes` 是**已完成节点的有序前缀**，按 submit 顺序排列。列表长度 N 即"已完成 N 个节点，应从第 N 个恢复"。

### 5.3 加载

`Flow.__init__` 时：
- 把传入的 `state_path` 归一化为 `str` 存到 `self._state_path`。
- 调 `load_state(self._state_path)`：文件不存在 → `_resume_data = []`（全新运行）；文件存在 → 解析 JSON，`_resume_data = data["nodes"]`。
- `_next_index`（driver 起点）初始化为 `len(_resume_data)`。

### 5.4 节点对齐与 id 校验

第 i 个 `submit` 的节点（`node.index == i`）：
- 若 `i < len(_resume_data)`：该节点已在上次运行中完成。
  - **id 校验**：`_resume_data[i]["id"]` 必须等于本次 `submit` 生成的 `node.id`。不等 → 抛 `StateMismatchError`（程序与持久化文件不匹配，例如 submit 序列变了）。
  - 校验通过 → `node.state = DONE`，`node.result = _resume_data[i]["result"]`，立即 `node.future.set_result(result)`。该节点**不会**被 driver 运行。
- 若 `i >= len(_resume_data)`：待运行节点，正常进队列由 driver 处理。

### 5.5 恢复点

"恢复点节点" = 索引恰为 `len(_resume_data)` 的那个节点，**且** `_resume_data` 非空。

- 它的 before-hook 收到 `resuming=True`。
- 其后所有节点 before-hook 收到 `resuming=False`。
- 全新运行（`_resume_data` 为空）：没有恢复点，所有节点 `resuming=False`。
- 若持久化文件已覆盖全部 submit 的节点（上次完整跑完）：所有节点加载为 DONE，driver 无事可做，`wait_all` 立即返回。无节点是恢复点。

### 5.6 写入

- **时机**：每个节点的 before-hook + fn + after-hook **全部成功**后，写一次。三步任一抛异常 → 不写、进程崩溃 → 该节点下次重新整体运行。
- **内容**：当前所有 `state is DONE` 的节点（按 index 顺序），各取 `id` 与 `result`。
- **原子写**：先写临时文件（如 `<path>.tmp`），再 `os.replace(tmp, path)` 重命名。避免写入过程中崩溃损坏文件。
- **成功后**：文件保留。下次启动全部节点加载为 DONE，幂等。

---

## 6. 执行模型

### 6.1 Flow 内部状态

```python
class Flow:
    def __init__(
        self,
        state_path: str,                 # 位置-或-关键字，必传；接受 str 或 Path
        *,
        before_hook: HookFn | None = None,
        after_hook: HookFn | None = None,
    ) -> None:
        self._state_path = str(state_path)            # 归一化（接受 pathlib.Path）
        self._before_hook = before_hook
        self._after_hook = after_hook
        self._nodes: list[Node] = []
        self._id_counter: dict[str, int] = {}
        self._driver: asyncio.Task | None = None
        self._resume_data: list[dict] = load_state(self._state_path)  # 不存在 → []
        self._next_index: int = len(self._resume_data)
```

### 6.2 submit

```python
def submit(self, fn, /, *parents, **named_parents) -> NodeHandle:
    # 1) 校验：所有位置/关键字参数必须是 NodeHandle
    for i, p in enumerate(parents):
        if not isinstance(p, NodeHandle):
            raise TypeError(
                f"submit: positional arg #{i} must be NodeHandle, got {type(p).__name__}. "
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
        # 待运行 → 确保 driver 在跑
        if self._driver is None or self._driver.done():
            self._driver = asyncio.create_task(self._drain())

    return NodeHandle(node)
```

`_next_id`：沿用 dagflow 的实现——`f"{fn.__name__ or repr(fn)}#{seq}"`，每个 name 一个递增序号。

### 6.3 driver：_drain

```python
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
        raise
```

driver **不** catch-and-swallow 节点异常。`except BaseException` 仅做异常转发后 `raise` 原异常。

### 6.4 _run_node

```python
async def _run_node(self, node: Node) -> None:
    node.state = NodeState.RUNNING
    resuming = bool(self._resume_data) and node.index == len(self._resume_data)

    if self._before_hook is not None:
        await self._before_hook(
            HookContext(phase="before", node_id=node.id, index=node.index, resuming=resuming)
        )

    real_args = tuple(p.result for p in node.parent_args)
    real_kwargs = {k: p.result for k, p in node.parent_kwargs.items()}
    value = await node.fn(*real_args, **real_kwargs)      # 抛异常则不 catch

    if self._after_hook is not None:
        await self._after_hook(
            HookContext(phase="after", node_id=node.id, index=node.index,
                        resuming=resuming, result=value)
        )

    # before + fn + after 全成功 → 落地状态并 checkpoint。
    # 置 DONE 与 set_result 之间无 await，避免“state 已 DONE 但 future 未 resolve”的窗口。
    node.result = value
    node.state = NodeState.DONE
    self._persist()
    node.future.set_result(value)
```

任何一步（before-hook / fn / after-hook）抛异常 → 异常上抛到 `_drain` → 进程崩溃。该节点未持久化 → 重启时整体重新运行。`after-hook` 在节点仍为 RUNNING 时调用（fn 已返回，结果经 `ctx.result` 传入）；置 `DONE`、`_persist`、`set_result` 三步紧挨、其间无 `await`。

注意：节点运行时，其父节点必然已 DONE（submit 顺序 + 串行保证），`parent.result` 必然有值。父结果可能来自实际运行，也可能来自持久化加载——对 `_run_node` 透明。

### 6.5 _persist

```python
def _persist(self) -> None:
    done = [
        {"id": n.id, "result": n.result}
        for n in self._nodes if n.state is NodeState.DONE
    ]
    save_state(self._state_path, {"version": 1, "nodes": done})
```

`save_state`（在 `persistence.py`）：写临时文件 + `os.replace` 原子重命名。

### 6.6 wait_all

```python
async def wait_all(self) -> None:
    """等到 driver 把当前队列排空。driver 内的节点异常经此重新抛出。"""
    while self._driver is not None and not self._driver.done():
        await self._driver
```

`await self._driver` 会在 driver 因节点异常崩溃时重新抛出该异常。控制器不 catch → 进程崩溃。这是规范的"等待完成"入口。

### 6.7 关键时序与保证

- `submit` 同步、无 await；可连续调用。第一个待运行节点的 submit 创建 driver task；driver 在控制器下一次让出事件循环（如 `await wait_all()`）时开始实际运行。
- driver 死亡（队列排空或崩溃）后，新的 submit 会重启一个 driver。
- 节点严格按 submit 顺序串行：同一时刻至多一个节点 RUNNING。
- `await handle` 在节点运行前调用会挂起，直到 driver 完成它；可用于控制器读取中间结果再 submit 后续节点（submit 后续节点会按需重启 driver）。
- driver 崩溃时，所有未决 future 被塞入异常，`await handle` 与 `await wait_all()` 都会抛出。

---

## 7. 失败处理与异常传播

- 节点 fn 或 hook 抛异常 → driver 不 catch（仅做异常转发）→ 异常从 `_drain()` task 上抛。
- `await flow.wait_all()` 在 await driver task，异常经此重新抛出 → 控制器不 catch → 进程崩溃。
- 崩溃时：之前每个完成的节点都已持久化，崩溃节点本身未持久化。
- 重启同一程序 → 加载 JSON → 崩溃节点重新整体运行（before-hook 拿到 `resuming=True`）。
- **异常管道**：driver 崩溃时把异常塞进所有未决 future（§6.3）。这是为了让 `await handle` 不 hang，属于必要的异常转发，不是错误恢复——driver 不会"消化"异常，仍原样 `raise`。
- 无 `NodeFailed`/`NodeSkipped` 包装：原始异常直接传播。

---

## 8. 公开 API

```python
# taskline/__init__.py
from .flow import Flow
from .handle import NodeHandle
from .node import NodeState
from .hooks import HookContext
from .errors import StateMismatchError

__all__ = ["Flow", "NodeHandle", "NodeState", "HookContext", "StateMismatchError"]
```

```python
class Flow:
    def __init__(self, state_path: str, *,
                 before_hook: HookFn | None = None,
                 after_hook: HookFn | None = None) -> None: ...
    def submit(self, fn: Callable[..., Awaitable[T]], /,
               *parents: NodeHandle, **named_parents: NodeHandle) -> NodeHandle[T]: ...
    async def wait_all(self) -> None: ...
    def to_dot(self) -> str: ...
    @property
    def nodes(self) -> tuple[NodeHandle, ...]: ...

# HookFn = Callable[[HookContext], Awaitable[None]]
```

### 8.1 典型用法

**(1) 基本流水线**
```python
from functools import partial
from taskline import Flow

async def fetch(url): ...
async def parse(raw): ...
async def save(parsed): ...

flow = Flow("/path/to/state.json")
h1 = flow.submit(partial(fetch, "https://example.com"))
h2 = flow.submit(parse, h1)
h3 = flow.submit(save, h2)
await flow.wait_all()
```

**(2) 带恢复 hook（场景 B）**
```python
async def before(ctx):
    if ctx.resuming:
        # 崩溃重启，恢复点节点即将重跑——做一次性外部状态恢复
        await restore_external_state(ctx.node_id)

async def after(ctx):
    log.info("node %s done -> %r", ctx.node_id, ctx.result)

flow = Flow("/path/to/state.json", before_hook=before, after_hook=after)
...
await flow.wait_all()
```

**(3) 崩溃恢复（场景 A）**
```python
# 第一次运行：节点 2 抛异常 → wait_all 抛出 → 进程崩溃
# 此时 JSON 文件已存节点 0、1 的结果
# 用户修复问题后重新启动同一程序，传入同一 state_path：
flow = Flow("/path/to/state.json")
h0 = flow.submit(step0)   # 加载为 DONE（不重跑）
h1 = flow.submit(step1)   # 加载为 DONE（不重跑）
h2 = flow.submit(step2)   # 恢复点：重新运行，before-hook resuming=True
h3 = flow.submit(step3)   # 正常运行
await flow.wait_all()
```

### 8.2 边界行为表

| 操作 | 行为 |
| --- | --- |
| `Flow()` 不传 `state_path` | 抛 `TypeError`（Python 自带 "missing 1 required positional argument"）|
| 持久化文件不存在 | 全新运行，`_resume_data = []` |
| 持久化 id 与 submit 序列不匹配 | 抛 `StateMismatchError` |
| `submit` 传非 NodeHandle 参数 | 抛 `TypeError` |
| `submit` 后连续 `submit`，不 await | 合法；节点入队，driver 自动接力 |
| 节点 fn / hook 抛异常 | 异常上抛、`wait_all` 重抛、进程崩溃；该节点未持久化 |
| 节点返回非 JSON 可序列化值 | `_persist` 时 `json.dump` 抛异常 → 进程崩溃（文档明确要求可序列化）|
| 持久化文件已覆盖全部节点 | 所有节点加载为 DONE，`wait_all` 立即返回 |
| `await handle`（节点尚未运行） | 挂起直到 driver 完成它 |
| 重复 `await` 同一 handle | 返回相同结果（Future 语义）|
| 加载为 DONE 的节点 | 不触发 before/after hook（未真正运行）|

### 8.3 不变量（测试据此验证）

1. 节点严格按 submit 顺序串行执行，同一时刻至多一个 RUNNING。
2. 节点运行时其所有父节点均为 DONE，`parent.result` 必然可用。
3. 每个 DONE 节点（运行或加载）在 `_persist` 后，其结果出现在 JSON 文件中；崩溃节点不出现。
4. `wait_all` 返回后，所有已 submit 节点均为 DONE。
5. before/after hook 仅对实际运行的节点触发；加载的节点不触发。
6. 恢复点节点的 before-hook `resuming=True`，其余为 False。

---

## 9. to_dot 可视化

```python
_DOT_COLOR = {
    NodeState.DONE: "palegreen",
    NodeState.RUNNING: "lightblue",
    NodeState.PENDING: "white",
}

def to_dot(self) -> str:
    lines = ["digraph flow {", '  node [shape=box, style=filled];']
    for n in self._nodes:
        label = f"{n.id}\\n{n.state.value}"
        lines.append(f'  "{n.id}" [label="{label}", fillcolor={_DOT_COLOR[n.state]}];')
    for n in self._nodes:
        for p in (*n.parent_args, *n.parent_kwargs.values()):
            lines.append(f'  "{p.id}" -> "{n.id}";')
    lines.append("}")
    return "\n".join(lines)
```

边由 `parent_args` + `parent_kwargs` 推导。同一父被位置和关键字同时引用时会产生重复边——可接受（N ≤ 50，graphviz 自行处理）。

---

## 10. 测试策略

`pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`）。所有测试通过共享 `state_path` fixture（`tmp_path / "state.json"`）拿一个隔离的临时路径，显式传入 `Flow(state_path, ...)`，确保互不干扰。

| 文件 | 测试要点 |
| --- | --- |
| `test_basic.py` | 单节点跑通 / 链式 / 父结果按位置注入 / 按参数名注入 / fn 返回 None |
| `test_serial_order.py` | 节点严格按 submit 顺序串行：各 fn 往共享 list 追加自己的标记，断言顺序；用 `asyncio.sleep` 错开也不乱序 |
| `test_persistence.py` | 跑完后 JSON 文件存在、`version`/`nodes` 结构正确、结果内容正确 / 无 `.tmp` 残留 / 每个节点完成后文件增量更新 |
| `test_resume.py` | run1：节点 2 抛异常 → `wait_all` 抛出，文件存了节点 0/1。run2：新 `Flow()` 同序列 submit（节点 2 换成不报错版）→ 节点 0/1 不重跑（fn 用计数器验证未被调用）、节点 2 是恢复点、节点 2/3 正常完成 / 持久化文件覆盖全部节点时整个 flow 跳过 |
| `test_hooks.py` | before/after 被调用、`HookContext` 各字段正确 / 加载为 DONE 的节点不触发 hook / hook 抛异常会上抛 / `resuming` 仅恢复点为 True |
| `test_failure.py` | 节点抛异常 → 异常穿过 `wait_all` 重新抛出（原始异常类型，未被包装）/ 崩溃前已完成节点已持久化 / 未决 handle 的 `await` 也拿到异常不 hang |
| `test_env.py` | `Flow()` 不传 `state_path` → `TypeError` / 持久化 id 与 submit 序列不匹配 → `StateMismatchError` / `submit` 传字面量 → `TypeError` |
| `test_visualize.py` | `to_dot` 含全部节点 id 与边、3 态着色出现；空 flow 无边 |
| `test_e2e_smoke.py` | 完整场景 A：第一次跑（节点 2 故意抛异常）→ 捕获异常、检查文件存了前缀；第二次新 Flow 同序列（节点 2 改为成功版）→ 从节点 2 恢复、全部 DONE、最终结果正确、节点 0/1 未重跑 |

### 10.1 模拟崩溃重启的测法

进程内无法真崩溃，用两段模拟：
- **run1**：构造 Flow，submit 一串节点，其中某节点 fn 抛异常；`with pytest.raises(...): await flow.wait_all()`。此后 JSON 文件含已完成前缀。
- **run2**：同一测试函数内，新建 `Flow(state_path, ...)`（同一路径 → 读到 run1 的文件），按相同顺序 submit（抛异常的节点换成成功版本或保持——取决于测试目的）；`await flow.wait_all()`；断言已完成节点未被重新调用（每个 fn 内对共享 dict 计数）、恢复点节点 `resuming=True`。

### 10.2 不写测试的事

- 性能 / 大 N 压测——N ≤ 50。
- 真实多进程崩溃——用 run1/run2 两段模拟。
- 静态类型检查（mypy）。

---

## 11. 环境与运行

- **开发**：Windows，`python`（= python3），已配 pip 镜像源。`pip install -e ".[test]"`。
- **生产**：Ubuntu + python3。
- **打包**：`pyproject.toml`，`name = "taskline"`，`requires-python = ">=3.10"`。
- 构造 `Flow` 必须显式传入 `state_path`（JSON 检查点路径）。
- 不引入额外依赖；JSON 用标准库 `json`，原子写用 `os.replace`。

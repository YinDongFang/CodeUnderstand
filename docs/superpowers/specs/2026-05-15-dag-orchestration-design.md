# DAG 异步任务编排框架 —— 设计文档

> 日期：2026-05-15
> 状态：Draft v2（已与用户对齐）

---

## 1. 目标与定位

提供一个**通用的、纯 asyncio、单进程**的有向无环图（DAG）任务编排库。每个图节点是一个 `async` 函数；子节点的入参由父节点的运行结果按**参数名/位置**注入。

库的形态是"延迟执行 + 显式依赖"：用户在外部代码（控制器）里调 `flow.submit(fn, *parents, **named_parents)`，**所有传入的参数必须是 `NodeHandle`**——它们就是父依赖；字面量配置在 submit 之前用 `functools.partial` / lambda 绑进 fn 自己。这样 submit 的语义干净到一句话：**fn 加上它的父节点**。

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
- submit 允许字面量参数（一律强制 `NodeHandle`；字面量请提前 partial 进 fn）

---

## 2. 设计约束（来自需求确认）

| 维度 | 决定 |
| --- | --- |
| 场景 | 通用异步任务编排（库性质） |
| 运行时 | 纯 asyncio，单进程 |
| 父→子结果传递 | `submit(fn, *parents, **named_parents)`；运行时按位置/参数名把父结果传给 fn；字面量由 fn 自己用 partial 绑 |
| 图构建方式 | 外部控制器在运行时动态 `submit`；每次 submit 时父必须已存在（天然无环） |
| 失败传染 | 节点抛异常 → 直接下游 SKIPPED → 一直传染；无关分支照常 |
| 终止 | 外部控制器 `await flow.wait_all()`；不需要 `close` |
| 并发上限 | 不设限，交给 asyncio |
| 节点规模 | ≤ 50 |
| 开发环境 | Windows + python3（启动命令 `python`），已配置 pip 镜像源 |
| 生产环境 | Ubuntu + python3 |

---

## 3. 架构与分层

```
┌─────────────────────────────────────────────────┐
│  Flow (orchestrator)                            │
│  - submit / wait_all / to_dot / nodes           │
│  - 维护 nodes 集合 + 就绪检测 + 状态机           │
│  - 父完成时唤醒下游                              │
└──────────────────┬──────────────────────────────┘
                   │ 拥有
                   ▼
┌─────────────────────────────────────────────────┐
│  Node (data)              NodeHandle (proxy)    │
│  - id, fn,                - 不可变、可哈希、     │
│    parent_args (tuple),     awaitable           │
│    parent_kwargs (dict)   - submit 的返回值     │
│  - parents (推导出)        - 控制器据此构图       │
│  - state, result, exc                           │
│  - asyncio.Future                               │
└─────────────────────────────────────────────────┘
```

- `Node` 是纯数据 + 一个 `asyncio.Future`，不知道"图"的存在。
- `NodeHandle` 是 `Node` 的对外不可变引用，可哈希、可 `await`，控制器和 submit 参数里用的都是它。
- `Flow` 是调度器：submit 时校验所有参数都是 NodeHandle、提取父依赖、跑就绪节点、传染失败、提供 `wait_all` 和 `to_dot`。

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
  test_wait_all.py
  test_concurrent.py
  test_visualize.py
  test_validation.py   # submit 参数校验：非 NodeHandle 立即抛 TypeError
  test_e2e_smoke.py    # 端到端冒烟：一个真实小流程，多分支 + 部分失败 + 跳过
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

`READY` 在实现中通常一闪而过——`_try_schedule` 判定父全 DONE 后立刻 `create_task` 进入 `RUNNING`。保留为状态机的显式步骤、便于推理和调试。

### 4.2 `Node`（内部）

```python
@dataclass
class Node:
    id: str                                    # 自动生成，例 "fetch#3"
    fn: Callable[..., Awaitable]
    parent_args: tuple["Node", ...]            # 位置父依赖，按顺序
    parent_kwargs: dict[str, "Node"]           # 关键字父依赖，按 fn 形参名
    parents: frozenset["Node"]                 # 推导自上面两个，用于状态机判定
    state: NodeState = NodeState.PENDING
    result: Any = None
    exception: BaseException | None = None
    future: asyncio.Future = field(init=False)
```

注意 `parent_args` / `parent_kwargs` 存的是 `Node`（不是 `NodeHandle`），因为 submit 时已经 unwrap 过；运行时直接读 `node.result`。

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
  | SKIPPED     | 抛 `NodeSkipped`（`__cause__` = 最近一个失败/跳过上游的异常） |
  | CANCELLED   | 抛 `asyncio.CancelledError` |
  | 未终态       | 挂起等终态 |

### 4.4 异常

```python
class NodeFailed(Exception):
    """节点 fn 抛异常时由框架抛出，__cause__ 指向 fn 抛的原异常"""

class NodeSkipped(Exception):
    """因上游 FAILED/CANCELLED 被跳过，__cause__ 指向最近的失败/跳过上游异常"""
```

`__cause__` 链可一路追溯到根因（A 失败 → B/C/D SKIPPED；在 D 上 `await` 抛 NodeSkipped，`__cause__` = C 的 NodeSkipped，`__cause__.__cause__` = B 的 NodeSkipped，最终能定位到 A 的原异常）。

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
def submit(self, fn, /, *parents, **named_parents) -> NodeHandle:
    # 1. 校验：所有位置/关键字参数必须是 NodeHandle
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

    # 2. 构造 Node
    parent_args   = tuple(h._node for h in parents)
    parent_kwargs = {k: v._node for k, v in named_parents.items()}
    node = Node(
        id            = self._next_id(fn),
        fn            = fn,
        parent_args   = parent_args,
        parent_kwargs = parent_kwargs,
        parents       = frozenset(parent_args) | frozenset(parent_kwargs.values()),
    )
    node.future = asyncio.get_event_loop().create_future()
    self._nodes.append(node)

    # 3. 尝试调度（父若已全终态则立刻 READY/SKIPPED）
    self._try_schedule(node)
    return NodeHandle(node)
```

**关键设计：submit 只接受 `NodeHandle`**
- 字面量参数（URL、超时、配置等）一律在 submit 之前 `partial(fn, literal1, literal2)` 绑好。
- 好处：依赖图与函数签名彻底解耦；submit 一眼能看清每条边；不会有"忘了把 NodeHandle 当依赖识别"这种坑。
- 代价：fan-out 写法稍变（见 §6.1）。

**辅助：`_next_id`**

```python
def _next_id(self, fn) -> str:
    name = getattr(fn, "__name__", None) or repr(fn)
    seq = self._id_counter.get(name, 0)
    self._id_counter[name] = seq + 1
    return f"{name}#{seq}"
```

`functools.partial` 没有 `__name__`，所以 fallback 到 `repr(fn)`——必要时可以 `partial(fn).__name__ = "..."` 自起名，但通常不需要。

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
        real_args   = tuple(p.result for p in node.parent_args)
        real_kwargs = {k: p.result for k, p in node.parent_kwargs.items()}
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

注意 args/kwargs 解析现在极简——`node.parent_args` 已经全是 `Node`，直接读 `.result`。

### 5.5 _mark_skipped

```python
def _mark_skipped(self, node: Node) -> None:
    node.state = NodeState.SKIPPED
    cause_node = next(
        (p for p in node.parents
         if p.state in (NodeState.FAILED, NodeState.CANCELLED, NodeState.SKIPPED)),
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

### 5.8 wait_all

```python
TERMINAL_STATES = {NodeState.DONE, NodeState.FAILED,
                   NodeState.SKIPPED, NodeState.CANCELLED}

async def wait_all(self) -> None:
    """等到所有已注册节点都进入终态后返回。

    若在 wait_all 等待期间有新的 submit 进来，新节点也会被等到。
    多次调用幂等：第二次若已全终态会立刻返回。
    """
    while any(n.state not in TERMINAL_STATES for n in self._nodes):
        if self._inflight:
            await asyncio.wait(self._inflight,
                               return_when=asyncio.FIRST_COMPLETED)
            self._inflight = {t for t in self._inflight if not t.done()}
        else:
            # 防御：READY 起 task 是同步的，路径上不会停在这里；
            # 但保留这一支防止状态机将来扩展时死循环。
            await asyncio.sleep(0)
```

### 5.9 关键时序保证

- `submit` 返回前，节点已 append 进 `_nodes`，且若父全已终态则状态已不再 PENDING（要么 READY/RUNNING/DONE，要么 SKIPPED）——所以 `await handle` 不会卡在"节点根本没被调度过"。
- 状态转换发生在 `_run` 的 try/finally 内，不依赖 task 的 done callback 顺序——`wait_all` 的判定永远基于已经写好的状态。
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
    def submit(self, fn: Callable[..., Awaitable[T]], /,
               *parents: NodeHandle,
               **named_parents: NodeHandle) -> NodeHandle[T]: ...
    async def wait_all(self) -> None: ...
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
from functools import partial

flow = Flow()

async def fetch(url): ...                  # 字面量在 partial 里
async def parse(html): ...                 # 一个父：html
async def merge(*parts): ...               # 多父：按位置

pages  = [flow.submit(partial(fetch, u)) for u in urls]   # 每个 fetch 无父
parsed = [flow.submit(parse, p) for p in pages]           # 一父：位置
final  = flow.submit(merge, *parsed)                      # N 父：位置展开
await flow.wait_all()
print(await final)
```

**(2) 控制器看到中间结果再决定下一步**
```python
from functools import partial

flow = Flow()
probe = flow.submit(partial(classify, doc))
kind  = await probe                                       # 直接 await，不调 wait_all

if kind == "simple":
    out = flow.submit(partial(quick_path, doc))
else:
    a = flow.submit(partial(deep_step_a, doc))
    b = flow.submit(deep_step_b, a)                       # 一父
    out = flow.submit(merge, a=a, b=b)                    # 关键字父

await flow.wait_all()
print(await out)
```

**(3) 忽略失败分支**
```python
from functools import partial

hs = [flow.submit(partial(process, item)) for item in items]
await flow.wait_all()
ok = []
for h in hs:
    try:
        ok.append(await h)
    except (NodeFailed, NodeSkipped):
        pass
```

**(4) 关键字父依赖（推荐：让父语义在 submit 处一目了然）**
```python
async def diff(left, right):                              # 形参名 left/right
    ...

h_old = flow.submit(partial(load, "old.json"))
h_new = flow.submit(partial(load, "new.json"))
h_diff = flow.submit(diff, left=h_old, right=h_new)       # 名字对应
```

### 6.2 边界行为表

| 操作 | 行为 |
| --- | --- |
| `submit` 传非 NodeHandle 参数（包括字面量、字符串、None） | 立即抛 `TypeError`，节点不会被注册 |
| `submit` 后立刻 `await handle`，不调 `wait_all` | 阻塞直到节点终态，支持 |
| 多次 `await flow.wait_all()` | 每次都等当前未终态节点跑完；全终态则立刻返回 |
| `wait_all` 返回后再 `submit` | 完全合法 |
| `wait_all` 期间从别的 task `submit` | 也会被等到（实现按实时扫描 `_nodes` 做循环条件） |
| 重复 `await` 同一 handle | 返回相同结果 / 抛相同异常（Future 语义） |
| 没 submit 任何节点就 `wait_all` | 立刻返回 |
| `await handle` 时节点 SKIPPED | 抛 `NodeSkipped`，`__cause__` 链可回溯根因 |
| `await handle` 时节点 CANCELLED | 抛 `asyncio.CancelledError` |
| 节点 fn 内部 `await` 别的 handle（绕过 submit 依赖） | 允许但风险自负——见 §7 |
| 节点 fn 是同步函数 | 不支持，`await fn(...)` 抛 TypeError → 节点 FAILED |
| 节点 fn 返回 None | 完全合法，`await handle` 返回 None |

### 6.3 不变量（测试据此验证）

1. `submit` 校验失败时不留下任何半成品节点（`_nodes` 不增长、`_id_counter` 不递增）。
2. `submit` 成功返回前：节点已登记在 `_nodes`；父全已终态 → state ∈ {READY, RUNNING, DONE, SKIPPED}；否则 PENDING。
3. `await flow.wait_all()` 返回时，`flow.nodes` 里**所有**节点都在终态（包括 wait_all 期间被加入的）。
4. `await handle` 在节点终态后是纯函数式：相同 handle 重复 await 得到相同结果/异常。

---

## 7. 风险点与显式权衡

| 风险 | 决定 |
| --- | --- |
| 节点 fn 内部 `await` 别的 handle 绕过依赖系统 | 允许但不推荐；若那条边不在调度图里，可能死锁。文档明确说明，不在框架里防御。 |
| `submit` 强制 NodeHandle，限制了 fan-out 字面量列表的便利 | 接受。换来"submit 一眼看清依赖"的清晰性；字面量用 `partial` 一行解决。 |
| `partial(fn, ...)` 没有 `__name__` → 生成的 node id 不直观 | `_next_id` fallback 用 `repr(fn)`；如果需要好看的 id，用户可以 `partial.func.__name__` 或显式 `lambda` 套一层。后续可考虑加 `submit(..., name=...)` 显式命名（v1 不做）。 |
| `to_dot` 在节点未终态时也能调 | 可以。会输出当前状态色，方便实时调试。 |
| asyncio 默认 event loop 在不同平台行为差异（Win vs Ubuntu） | 不做特殊处理；纯 asyncio 标准 API；Win 上 `python -m pytest` 默认 ProactorEventLoop，Ubuntu 默认 Selector，对本库行为无影响。 |
| 节点 fn 抛 `BaseException` 子类（如 `SystemExit`） | 统一按 FAILED 处理，包成 `NodeFailed`；但 `CancelledError` 单独走 CANCELLED 分支（asyncio 语义要求重新抛出）。 |
| `submit` 的 `parents` 里包含同一个 handle 多次（如 `submit(fn, h, h)`） | 合法。`parents` 是 `frozenset`，去重后只算一个父；运行时 fn 仍按位置/名字拿到两份相同结果。 |

---

## 8. 测试策略

全部 `pytest-asyncio` 功能测试，单元/集成不强分。每个文件聚焦一类断言。

### 8.1 覆盖矩阵

| 文件 | 测试要点 |
| --- | --- |
| `test_basic.py` | 单节点跑通 / 链式两节点 / 钻石依赖 / 多父位置传递 / 多父关键字传递 / 同一 handle 多次作父 / fn 返回 None / fn 返回大对象（引用同一性） |
| `test_validation.py` | submit 传字面量（int/str/None）→ `TypeError` 且 `_nodes` 未增长 / 位置和关键字两种位置都校验 / 抛错后下一个 submit 仍正常工作（计数器未污染） |
| `test_failure.py` | 单节点 fn 抛异常 → `await handle` 抛 `NodeFailed` 且 `__cause__` 是原异常 / 失败传染下游 SKIPPED / 多层（A→B→C→D）`__cause__` 链能追根因 / 一支失败不影响并行无关分支 / 节点返回正常值但下一个 await 多次仍是同一 result |
| `test_dynamic.py` | 控制器 await 中间结果后再 submit / submit 时父已 DONE 立刻 READY 并跑完 / submit 时父已 FAILED 立刻 SKIPPED 不起 task / `wait_all` 返回后继续 submit 再 `wait_all` |
| `test_wait_all.py` | 空 flow `wait_all` 立刻返回 / 多次 `wait_all` 幂等 / 节点跑到一半 `wait_all` 等待终态 / 全 SKIPPED 也是终态 / `wait_all` 等待期间从别的 task submit 也会被等到 |
| `test_concurrent.py` | 多独立分支真并行：用 `asyncio.sleep` + wall time 验证 ≈ 最长分支而非串行总和（N=5，每个 0.1s，断言总耗时 < 0.3s） |
| `test_visualize.py` | 3 节点构图调 `to_dot()`，断言出现各节点 id 与 `->` 边；包含成功/失败/跳过状态的小图，断言不同 fillcolor 出现 |
| `test_e2e_smoke.py` | 见 §8.3 |

### 8.2 trickier 测试要点

1. **失败传染层数**：A → B → C → D，A 抛 `ValueError("root")`。
   - assert B/C/D 全 `SKIPPED`
   - `await D` 抛 `NodeSkipped`
   - 沿 `__cause__` 一路回溯能拿到 `ValueError("root")`（写一个小 helper：`def root_cause(e): while e.__cause__: e = e.__cause__; return e`）

2. **失败之后 submit**：A 已 FAILED；控制器 `await A` 捕获 `NodeFailed` 后 submit 一个新节点 E，依赖 A——E 应在 submit 内部 `_try_schedule` 阶段就判定 SKIPPED，不起 task，不卡 `wait_all`。
3. **submit-然后立即 await**：`h = flow.submit(partial(slow_fn, 0.05)); result = await h`，不调 `wait_all` 也能拿结果。
4. **手动 cancel 触发跳过**：从外部 cancel 一个 RUNNING 节点的 task，下游应 SKIPPED，`__cause__` 指向 `CancelledError`。
5. **同 handle 多次作父**：`flow.submit(lambda a, b: a + b, h, h)`——结果等于 `result * 2`，且 `parents` 去重后只算一个父。
6. **submit 校验失败的事务性**：先成功 submit 一个节点；接着 `flow.submit(fn, "literal")` 抛 TypeError；再成功 submit；检查 `_nodes` 长度恰好为 2（中间失败那次没留下半成品）、id 没被那次失败提前占用。
7. **wait_all 期间并发 submit**：开一个 task 跑 `await flow.wait_all()`，另开一个 task `flow.submit(...)` → `await asyncio.sleep(0)` → 再 submit 一两个。最终 `wait_all` 返回后所有节点都在终态。
8. **节点 fn 内非法 await 兄弟 handle（不在 parents 里）**：写一个测试故意触发死锁场景，加 `asyncio.wait_for(..., timeout=0.2)` 期望超时——文档化"风险自负"的行为，确认框架不会"主动救你"。

### 8.3 端到端冒烟（`test_e2e_smoke.py`）

构造一个真实形状的小流程（6 个节点，覆盖所有终态）：

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

断言：
- `load_a`/`load_b`/`merge_ab`/`load_d`/`transform_d`/`save_d` 全 `DONE`
- `load_c` `FAILED`，`transform_c` 和 `report` 均 `SKIPPED`
- `await save_d` 返回预期值；`await report` 抛 `NodeSkipped`，根因是 `load_c` 的原异常
- `flow.to_dot()` 包含全部 6 个节点 id 和 5 条边
- `await flow.wait_all()` 在所有断言执行前就已经返回（即冒烟测试就是"按部就班一遍跑通")

### 8.4 不写测试的事

- 性能 / 大 N 压测 —— N ≤ 50，不必要。
- 跨平台行为差异 —— 纯 asyncio；CI 跑 Linux 即可。
- 静态类型检查（mypy）—— 不引入。

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
- **打包**：`pyproject.toml` 用 `setuptools`，`name = "dagflow"`，`requires-python = ">=3.10"`（用了 `frozenset[Node]`、`X | None`、PEP 604 联合类型等语法）。
- 不强制 mypy；不引入额外依赖（`graphviz` 包也不要）。

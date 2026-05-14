# 流程编排引擎设计规格（Python 库 + Vite/React 控制台）

## 1. 目标与范围

在**单机内网**环境，交付一个可嵌入业务代码的**流程编排引擎**（类比 LangGraph 的「库 + 宿主」关系），并提供简易 **Vite + React** 运维控制台：左侧任务列表，右侧任务元信息、串行节点状态可视化、日志。

**本期范围内：**

- 工作流由用户在 Python 中通过**库 API** 定义与注册；**不以 HTTP 注册 DAG**。
- 通过库 API 启动**控制面服务**（HTTP 仅用于任务生命周期、状态轮询、日志拉取、interrupt 提交、`rerun` 等运维操作）。
- 多任务**并行**（每任务独立 worker 进程）；单任务内节点**串行**。
- 每节点可配置**产物目录**与本节点结束时的**白名单打包（zip）**；用于「从中途节点重跑」时的前置快照复现。
- 节点支持 **interrupt（人工介入）**：worker 落库后干净退出；**resume 不清空工作目录**，下一轮 worker 在同一任务目录上继续。
- 用户**手工从某节点重新执行**（及后续）时：**清空可复现工作树**，再**按顺序解压**该节点之前各节点之 zip，然后自该节点继续执行。
- 状态与元数据持久化：**SQLite（WAL）**。

**本期明确不做（YAGNI）：**

- SSE/WebSocket 实时推送（仅 HTTP 轮询）。
- 分布式队列、多机 worker。
- 以 JSON/YAML 为权威工作流源（声明式导出若需，可作为后续增量）。

---

## 2. 领域对象

| 概念 | 说明 |
|------|------|
| **工作流（Workflow）** | 用户以 Python 描述的**串行**节点定义集合（顺序由注册顺序或显式列表表达）；含节点级产物路径、白名单、`interrupt` 标记等。 |
| **任务（Task）** | 某工作流的一次运行实例；具独立**任务根目录**、独立 worker 进程边界、独立 DB 记录；`status` 见 **§8.1**。 |
| **节点（Node）** | 最小执行单元；状态枚举见 **§8.2**（与任务状态 **§8.1** 配合）。 |
| **人工介入（Interrupt）** | 节点执行至中断点，按 **§3.4** 落库；经 `interrupt/resolve` 后同节点可重入继续；**不**在 interrupt 点生成该节点 zip。 |

---

## 3. 架构与进程模型

### 3.1 进程划分

- **控制面进程（长期存活）**  
  - 承载 HTTP API（FastAPI 等实现细节由实现计划选定）。  
  - 维护 `task_id → worker 子进程` 的 supervision（pid、存活检测）。  
  - **仅**负责 spawn/monitor、SQLite 读写、绝不执行重计算节点逻辑。

- **任务 Worker 进程（按轮次存活）**  
  - **同一 `task_id` 在任一时点至多一个活跃 worker**（由 DB 状态机 + supervisor 保障）。  
  - 在任务根目录下**串行**执行节点：调用用户注册的可调用对象 → 按白名单打 zip → 更新节点/任务状态。  
  - 遇 **interrupt**：写入「等待人工输入」及 schema/占位信息 → **干净退出**。  
  - **Resume**：新 worker **挂载同一任务根目录**，**不清空**，从 DB checkpoint 继续。  
  - **`rerun(from_node_id)`**：**清空**任务根下约定的工作树 → 将该节点**之前**各成功节点的 zip **依序解压** → 从该节点起执行。

### 3.2 与 LangGraph 对齐的使用方式（语义层）

- 用户在业务包中：`Workflow` 构建 → `engine.register_workflow(wf)` → `engine.serve(...)` 启动控制面。  
- Worker 子进程须能 `import` 用户模块：**单机内网共用一个 venv/安装环境**为默认前提。

### 3.3 持久化

- **SQLite**：任务、节点、interrupt 期待与已提交 payload、zip 路径、worker 世代、日志文件引用等。  
- **大段日志**：主要落盘文件；DB 存路径、offset 或分片索引（实现计划细化）。  
- **并发**：SQLite WAL；短事务；避免在 DB 存整段日志正文。

### 3.4 Interrupt（人工介入）：定稿设计与伪代码约束

本节固定 **interrupt → 落库 → worker 退出 → HTTP resolve → 新 worker resume** 的契约；实现 **必须** 遵守下列字段含义、顺序与伪代码控制流（可用等价实现，但不得削弱约束）。

#### 3.4.1 核心语义（与 LangGraph「interrupt」对齐的目标）

- interrupt **只发生在某一节点的执行轮次内**；触发后该节点 **不得** 进入 `success`，也 **不得** 生成该节点级 **zip 快照**（节点成功收尾流程尚未发生）。  
- interrupt **之前**节点已写入**任务根目录下**磁盘的文件 **视为已保留**；resume **不得**清空任务目录（与 `rerun` 区分）。  
- **跨 worker 世代不得依赖进程内内存**：resume 只能通过 **DB 中的 resolve payload + 磁盘状态** + **节点函数入口重入** 继续逻辑。  
- **同一任务在任一时间至多一个「未关闭的 interrupt」**：`task.status == waiting_human` 时，不得并发第二次 interrupt。

#### 3.4.2 持久化逻辑模型（实现可拆表或 JSON 列，语义须一致）

以下字段为 **逻辑必填**（名称可映射为蛇形列名或嵌套 JSON）：

| 字段 | 说明 |
|------|------|
| `interrupt_seq` | 单调递增整数（**每任务**）。每进入一次新的 `waiting_human` **打开**一轮 interrupt 时 `+1`；用于 resolve **幂等与防重放**。 |
| `interrupt_node_id` | 当前等待人工输入的节点 id。 |
| `interrupt_expected_schema` | `JSON Schema`（对象或 `null` 表示仅校验非空 JSON）。resolve body **必须通过**该 schema。 |
| `interrupt_request_extras` | 可选 JSON，仅作 UI 提示（标题、字段说明），**不参与**引擎校验逻辑。 |
| `interrupt_response_payload` | resolve 成功后写入的用户 JSON；worker **消费一次**后须清除或标记已消费（见伪代码）。 |
| `interrupt_response_consumed` | 布尔。新 worker 世代已将 `human_input` 注入上下文并成功**再次调用**节点入口后置 `true` 并清空 payload（或等价状态机）。 |

**约定：** `interrupt_seq` **初始**为 `0`；每发生一次「打开 `waiting_human`」的事务提交，`interrupt_seq` **增 1**；客户端可在 resolve 时携带当前 `seq` 做幂等。

**可选** `interrupt_checkpoint`（opaque JSON）：节点在调用 `interrupt()` 时传入、原样在 GET API 中回显给 UI；**引擎不解释**。若节点逻辑完全可由「目录 + `human_input`」恢复，可省略。

#### 3.4.3 工作流侧库 API（用户代码约束，伪代码）

引擎须提供 **可在节点可执行代码路径内调用** 的 `interrupt()`；其行为必须是「**把请求交给 runner 并结束本 worker 世代**」，**不得**假装异步等待 HTTP。

```text
// 约束 INV-A：interrupt 调用点之后直到函数返回的代码，在本 worker 世代内 **不得再执行**
//（因进程即将退出；后续逻辑必须在「携带 human_input 的重入路径」上完成）。

interrupt(
  *,
  expected_schema: object | null,
  ui: object | null = null,
  checkpoint: object | null = null,
) -> Never

NodeContext:
  task_id: str
  workflow_id: str
  node_id: str
  task_root: Path
  // 仅当本世代为 resolve 后的第一次节点调用、且目标节点匹配时：
  human_input: object | null
```

**约束 INV-B（节点可重入）**：对于声明了 interrupt 的节点，用户实现 **必须** 服从：

```text
PROC UserNode(node_ctx):
  // 推荐模式（同一进程内两次调用，第二次为 resume 重入）：
  IF node_ctx.human_input IS NOT NULL:
     // 继续路径：不得再次无条件 interrupt
     FinishWorkUsing(node_ctx.human_input)
     RETURN
  // 首跑路径：
  interrupt(expected_schema := ..., ui := ..., checkpoint := ...)
  // 不可达
```

实现 **允许**在一次节点执行中多次检查 `human_input`；但 **不得**在未消费 resolve 的世代里将节点标 `success`。

#### 3.4.4 Runner / Worker 伪代码（规范）

```text
PROC WorkerMain(task_id, worker_generation_id):
  ASSERT AcquireTaskWorkerLease(task_id) // 同一 task 仅一活跃 worker

  task := LoadTask(task_id)

  IF task.status == "waiting_human":
     FailFast("illegal spawn: waiting_human shall have no active worker")

  node_cursor := DetermineNextNode(task) // 串行：首个 pending；resume 时仍为 interrupt_node_id

  human := NULL
  IF task.interrupt_response_payload IS NOT NULL AND NOT task.interrupt_response_consumed:
     human := task.interrupt_response_payload

  FOR node IN WorkflowNodesFrom(workflow, start := node_cursor):
    SetTaskStatus("running")
    SetNodeStatus(node, "running")

    ctx := BuildNodeContext(task, node, human_input := human)

    TRY:
       RunUserCallable(node.fn, ctx)
    CATCH ControlledInterrupt AS c:
       // 由 interrupt() 触发，非用户未捕获异常
       OPEN_TX:
          task.interrupt_seq += 1
          task.interrupt_node_id := node.id
          task.interrupt_expected_schema := c.expected_schema
          task.interrupt_request_extras := c.ui
          task.interrupt_checkpoint := c.checkpoint  // 可选
          task.interrupt_response_payload := NULL
          task.interrupt_response_consumed := false
          SetNodeStatus(node, "waiting_human")
          SetTaskStatus("waiting_human")
          RecordWorkerExit(task_id, worker_generation_id, reason := "interrupt")
       COMMIT_TX
       ReleaseTaskWorkerLease(task_id)
       EXIT_PROCESS 0

    CATCH Any AS e:
       // 业务失败路径（非 interrupt）
       SetNodeStatus(node, "failed", error := e)
       SetTaskStatus("failed")
       ReleaseTaskWorkerLease(task_id)
       EXIT_PROCESS 1

    // 用户函数正常返回：本节点完成
    IF human IS NOT NULL AND node.id == task.interrupt_node_id:
       // resolve 已被用于完成该节点剩余工作
       SET interrupt_response_consumed := true
       CLEAR interrupt_response_payload
       SET human := NULL

    FinalizeNodeSuccess(node)  // 见下
    IF node IS LAST:
       SetTaskStatus("succeeded")

  ReleaseTaskWorkerLease(task_id)
  EXIT_PROCESS 0

PROC FinalizeNodeSuccess(node):
  // 约束 INV-C：仅在此 PROC 内生成该节点 zip 与 success
  AssertWorkspaceWhitelistOrFail(node)
  WriteNodeZipSnapshot(node)
  SetNodeStatus(node, "success")
```

**说明：** `RunUserCallable` 若检测到 `human` 已注入且节点已完成「继续路径」，应正常返回；随后 `FinalizeNodeSuccess` 才写 zip。若 `interrupt()` 在首跑路径被调用，控制流 **永不**到达 `FinalizeNodeSuccess` 同一世代内。

#### 3.4.5 HTTP `POST /tasks/{id}/interrupt/resolve`（请求体与伪代码）

**请求体 JSON 形状（固定）：**

```json
{
  "interrupt_seq": 3,
  "payload": { }
}
```

- `interrupt_seq`：**可选**，但若提供则 **必须** 与当前 `task.interrupt_seq` 一致，否则 **409**。  
- `payload`：**必须**；`interrupt_expected_schema` **仅约束 `payload`**。若 `expected_schema` 为 `null`，引擎 **仅校验** `payload` 为 JSON 对象且非 `null`（具体可放宽为「任意合法 JSON」，由实现计划二选一并在验收用例中固定）。

```text
PROC ApiInterruptResolve(task_id, request_body):
  task := LoadTask(task_id)
  ASSERT task.status == "waiting_human"
  seq := request_body.interrupt_seq  // OPTIONAL
  IF seq IS NOT NULL AND seq != task.interrupt_seq:
     RETURN 409 CONFLICT
  p := request_body.payload
  ASSERT JsonValidates(p, task.interrupt_expected_schema)

  OPEN_TX:
     task.interrupt_response_payload := p  // 注意：存入的是 payload，而非外包一层
     task.interrupt_response_consumed := false
     task.status := "running"
     BumpWorkerGeneration(task_id)
  COMMIT_TX

  SpawnWorker(task_id)
  RETURN 202 Accepted
```

**约束：** resolve **不得**在 `task.status != waiting_human` 时成功（除非实现明确支持幂等重复提交同一 payload，且语义等价 no-op；若不支持则返回 409）。

#### 3.4.6 Supervisor 不变式

- `waiting_human` 状态下：**无**活跃 worker pid 与 lease。  
- `running` 状态下：要么正在 spawn，要么存在合法 lease + 存活检测中的 worker。  
- resolve 事务提交 **`先于`** `SpawnWorker`；崩溃恢复时若 payload 已写但 worker 未起，由 **supervisor 补拉起**（实现计划中的可靠性条目）。

#### 3.4.7 与 `rerun` 的交互（约束）

- 当 `task.status == waiting_human`，**默认拒绝** `rerun`，或要求实现先 **取消** interrupt（显式 API，YAGNI 可先拒绝并 409）。避免「目录语义」与未闭合人工输入冲突。

### 3.5 实现前须锁定的补充约束（标识符、目录、租约、任务创建、rerun 安全）

以下条文为当前 spec 未在此前章节逐项写死、但实现与验收**应当**依赖的约束；若与上文冲突，以 **编号更小**的章节为准。

#### 3.5.1 标识符与注册表

- **`workflow_key`**：`register_workflow` 时传入的字符串键，在同一 **控制面进程** 内 **唯一**。重复注册：**拒绝**或 **显式覆盖**（二选一，须在实现计划中固定并在测试中覆盖）。  
- **`task_id`**：建议 **UUIDv4** 字符串（小写、带连字符）；HTTP 路径中须正确转义。  
- **`node_id`**：在同一 **Workflow** 定义内 **唯一**、**稳定**（同一工作流模板多次实例化任务时 id 不变），由用户代码指定；引擎 **不**自动生成。字符集建议：`[a-zA-Z0-9._-]`，长度上限 **64**（实现可收紧）。  
- **任务与工作流绑定**：创建任务时记录 `workflow_key` + **workflow 定义版本戳**（见 §3.5.5）；运行侧 **不得**因用户事后改掉 Python 代码而静默改变**已在跑或待恢复**任务的拓扑。

#### 3.5.2 目录布局（任务根与日志）

- **任务根路径**：`{engine.tasks_root}/{task_id}/`（`tasks_root` 由库配置或 `serve()` 参数给定；默认非相对 CWD 的模糊路径）。其下至少包含：  
  - `workspace/` — **可清空 / 可复现**的主工作树（`rerun` 清空对象默认指此目录；若实现采用「整棵任务根」策略须在计划中写死）；  
  - `logs/` — 该任务日志文件；  
  - `artifacts/zips/` — 每节点成功后的快照 zip 落盘（文件名须能映射 `node_id` 与**该次成功**的世代，避免覆盖；例如 `{ordinal}_{node_id}_{success_generation}.zip`）。  
- **路径遍历**：节点配置中出现的 `..`、绝对路径逃出 `workspace` 的行为：**拒绝注册**或在运行时报 **validation** 类失败（实现计划二选一，须一致）。

#### 3.5.3 Worker 租约（`running` 与僵尸进程）

- **租约（lease）**：每个活跃 worker 在 DB 中持有一条 **带过期时刻**的租约（或由 `worker_generation_id` + `lease_until` 表达）。刷新策略：**心跳**或「仅长事务边界更新」二选一，须在实现中固定。  
- **租约过期**：控制面可将任务标为 `stalled`（或先 `running` + **内部**恢复逻辑再 `stalled`，但对外状态须符合 §8），行为与 **worker 丢失**一致。  
- **控制面抢占**：在确认旧 worker 已死后（或达到 **硬超时**），允许清除 lease 并 spawn 新 worker；**禁止**两进程同时认为独占同一任务。  

#### 3.5.4 创建任务（`POST /tasks`）契约（最小）

Request body **至少**包含：

- `workflow_key`：string，已注册。  
- `input`：object，opaque 传给 workflow 入口 / 第一节点的上下文（schema 由业务约定；引擎 **可选**提供注册时 JSON Schema 仅供文档与校验）。  

**成功响应**：`201` + `task_id`；并 **隐式或显式**将任务置 `pending` 或 `running` 且 enqueue/spawn worker（行为固定一种）。  
**幂等**：本期 **不强制**客户端幂等键；重复创建即多个任务实例。

#### 3.5.5 `rerun(from_node_id)` 解压安全与原子性

- **Zip-slip**：解压时每条条目路径规范化后 **必须**位于 `workspace/`（或规定的沙箱根）之下；发现 `..` 或绝对路径：**中止** `rerun` 并任务进入 `failed`（或 `stalled`，二选一须固定）。  
- **清空与解压**：建议 **同一任务互斥锁** 内完成「truncate workspace → 按序解压 → 更新节点状态」；对外 HTTP 在操作完成前可返回 **202** 或阻塞至开始执行前（实现计划选定）。  
- **前置 zip 缺失**：某一先序节点应成功并有 zip，但文件丢失 → **拒绝 `rerun`**（4xx）或任务 `failed`（5xx）；须固定一种并写入验收用例。

#### 3.5.6 无产物目录 / 空白名单节点

- 若节点 **未配置**产物目录或白名单 **为空**：**不得**生成含数据的 zip；实现须 **跳过 zip** 仍标 `success`，**或**生成 **0 字节占位 zip**（二选一，须在计划中固定）。`rerun` 依赖前置 zip 的节点链须与选项一致（跳过 zip 的节点在复现时仅依赖「目录为空」语义）。

#### 3.5.7 时间与 API 表面

- HTTP JSON 中时间戳：**UTC**、**ISO 8601**（例如 `2026-05-14T12:34:56.789Z`）。  
- 错误响应（建议最小）：`{ "error": { "code": "...", "message": "..." } }`；`409` **须**用于非法状态迁移（含 spec 已列条件）。

#### 3.5.8 `stalled` / `failed` 后的 interrupt 字段

- 进入 `stalled` 或业务 `failed` 后：`interrupt_response_*` 与 `waiting_human` 相关列应处于 ** cleared 或语义无效**状态（实现须禁止在 `waiting_human` 以外对外暴露「可 resolve」的 interrupt）；恢复仅通过 **`rerun`** 或（若支持）**重置任务**类 API。

#### 3.5.9 观测性与资源上限（建议）

- **日志**：单任务日志文件 **轮转**（按大小或按日）；避免与 SQLite 同盘写爆时仅报 OS 错而无提示。  
- **zip / workspace**：可对单任务 `workspace` 最大字节数、zip 条目数设 **软上限**（超限 `failed` + 明确 `error.code`）；本期若不做，须在实现计划中列为技术债。

---

## 4. 产物目录、白名单与快照

- 每节点配置**产物根目录**（相对于任务根的路径或绝对路径策略由实现约定，须在实现计划中写死一种）。  
- 节点成功结束时：对该目录按 **白名单规则** 打 **zip**；路径写入 DB。  
- **软策略 B（多写文件）**：运行期出现在产物目录但**不在白名单**内的文件：**不进入 zip**，并打 **warning** 日志。  
- **白名单内期望路径缺失**：**硬失败**（节点 `failed`），以保证快照与 `rerun` 复现可信。  
- **正常 interrupt → resume**：**不**依赖解压历史 zip（目录保持中断前后一致）。  
- **仅 `rerun(from_node)`** 使用历史 zip 做前置复现。

---

## 5. HTTP 控制面 API（运维向；轮询）

不包含「工作流 CRUD」式 HTTP 注册。任务为**串行**，**不需要**独立图接口。

建议最小集合（路径名实现可微调，语义须保留）：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/tasks` | 任务列表（筛选/分页细节由实现计划定）。 |
| `GET` | `/tasks/{id}` | 任务元信息 + **`nodes: [...]`** 数组（**顺序即流程图**），元素含节点 id、状态、起止时间、耗时、zip 路径摘要等。 |
| `GET` | `/tasks/{id}/logs` | 日志流（支持 cursor 分页）。 |
| `POST` | `/tasks` | 创建并启动任务（body 指定已注册工作流 key、输入 payload 等）。 |
| `POST` | `/tasks/{id}/interrupt/resolve` | body：`{ "interrupt_seq"?: int, "payload": object }`（见 **§3.4.5**）；成功后 `task`→`running` 并 spawn worker。 |
| `POST` | `/tasks/{id}/rerun` | body：`{ "from_node_id": "K" }`，触发 **清空 + 前置 zip 解压 + 从 K 执行**。 |

**并发语义：`rerun` 与运行中 worker** 的互斥策略须在实现中写死一种（例如仅 `非 running` 允许，或先取消再 `rerun`），并在该文档的实现计划阶段落到验收用例。

**鉴权**：单机内网默认可为「可信网段」；若需 Token，列为实现计划可选项。

---

## 6. 前端（Vite + React）

- **布局**：参考 Notion / Flowus——**左**任务列表，**右**详情。  
- **详情区自上而下**：任务元信息 → **节点区**（见下）→ 日志面板。  
- **节点区（线性工作流）**：按 `nodes` 数组顺序绘制 **横向有序节点列表**（通常为左→右，与执行先后一致）；**每一节点**展示状态色与起止时间或耗时；**无需**边、连线或通用图布局算法。  
- **数据获取**：轮询 `GET /tasks` 与 `GET /tasks/{id}`（退避策略可置于前端）。  
- **开发联调**：单仓内 Vite `proxy` `/api` 至控制面（与 brainstorming 达成的「单仓一体」一致）。

---

## 7. 后端模块划分（包内责任）

1. **`workflow`（模型）** — 工作流与节点定义、白名单与 interrupt 元数据。  
2. **`runner`（单任务内核）** — 目录准备、调用节点、zip、状态迁移、interrupt 出口。  
3. **`supervisor`** — 子进程生命周期、`task_id` 互斥、resume/rerun 触发。  
4. **`store`（SQLite）** — 持久化与查询。  
5. **`server`（HTTP）** — 路由与控制面 DTO。  
6. **`logging`** — 文件日志 + DB 索引；白名单软策略告警。

---

## 8. 任务状态与节点状态（规范）

本节约束 **持久化与 HTTP DTO** 中使用的枚举值（字符串建议 **小写 snake_case**，与下表 `code` 一致）。**不得**在同字段混用别名（如 `SUCCESS` / `Success`）。

### 8.1 任务状态（`task.status`）

| `code` | 含义 | 是否终态 |
|--------|------|----------|
| `pending` | 任务已创建（DB 有记录），**尚未**进入可执行轮次（尚无 worker 世代认领或尚未从队列启动）。 | 否 |
| `running` | 任务处于某一 **worker 世代** 的执行过程中：推进节点、打 zip、或刚 spawn 即将从 checkpoint 继续。 | 否 |
| `waiting_human` | 任务停在 **interrupt** 点：已落库「期待输入」，**当前无**活跃 worker（上一世代已退出）；等待 `POST /tasks/{id}/interrupt/resolve`。 | 否 |
| `succeeded` | 全部节点均已 `success`，任务正常结束。 | **是** |
| `failed` | **业务或节点级**失败导致的终态：至少一个节点为 `failed`，且根因归类为 **业务/校验**（含白名单缺件、节点抛错等）。 | **是** |
| `stalled` | **运维/进程级**异常终态：已检测到 **worker 非正常消失**（崩溃、SIGKILL、失联等），引擎**不猜测**续跑方式；允许人工 `rerun` / 在适用时配合 resolve API（若实现选择支持）。**不等于**节点业务失败。 | **是** |

**并发不变式：** 对同一 `task_id`，**至多一个**「活跃 worker 世代」与 `running` 语义一致；`waiting_human`、`succeeded`、`failed`、`stalled` 下不得存在仍将任务视为可执行的活跃 worker（实现须以 DB + supervisor 一致为准）。

**任务状态迁移（允许边；未列出的迁移视为非法，须拒绝或返回 409）：**

```text
pending ──启动──► running
running ──最后一节点 success──► succeeded
running ──某节点 failed（业务/校验）──► failed
running ──interrupt 落库、worker 退出──► waiting_human
running ──检测到 worker 丢失──► stalled

waiting_human ──interrupt/resolve 成功，spawn 新世代──► running

stalled ──仅允许经明确的运维操作（如 rerun / 恢复策略，实现计划写死）──► running 或 pending
failed ──（可选）经人工 rerun 自某节点──► running；实现若不支持从 failed 直接 rerun，须在 API 层文档化
```

**说明：** 「从节点重跑」`rerun(from_node_id)` 在进入执行前须将任务置为可执行态：实现可选取 `running`（推荐，与「正在跑」统一）或先短暂 `pending` 再 `running`；无论哪种，**单任务互斥**仍须满足。

### 8.2 节点状态（`nodes[].status`）

| `code` | 含义 | 是否终态 |
|--------|------|----------|
| `pending` | 尚未开始执行本节点。 | 否 |
| `running` | 本节点逻辑或其后处理（含白名单 zip）进行中。 | 否 |
| `waiting_human` | 本节点触发 **interrupt**：已写期待输入与 checkpoint，**worker 已退出**；等待人工 resolve 后继续**本节点**（而非跳过）。 | 否 |
| `success` | 本节点成功完成；若配置产物目录，则 **zip 已生成且路径已写入 DB**；可作为后续 `rerun` 的前置快照源。 | **是** |
| `failed` | 本节点失败终态（业务异常、快照校验失败、或 **worker 丢失时当前节点**——见下）。 | **是** |

**节点与任务状态的对应关系（不变式）：**

- 任务 `succeeded` ⟹ 所有节点 `success`，且顺序与 workflow 定义一致。
- 任务 `failed` ⟹ **恰好一个**节点为「首例失败」语义（实现可记录 `first_failed_node_id`）；该节点 `failed`，其**左侧**（先序）节点均为 `success`，**右侧**均为 `pending`（不得出现 `success`）。
- 任务 `waiting_human` ⟹ **恰好一个**节点为 `waiting_human`（当前 interrupt 节点）；其先序节点均为 `success`，后续为 `pending`。
- 任务 `running` ⟹ **至多一个**节点为 `running` 或 `waiting_human`（二者互斥）；其余已完成者为 `success`，未开始为 `pending`。
- 任务 `stalled` ⟹ 通常由 **上一时刻** 的 `running` 节点触发 worker 丢失；实现须将该节点标为 `failed`，并设置 `error.category = "worker_lost"`（或等价枚举），以便 UI 与 `failed`（业务）区分。

**`rerun(from_node_id = K)`** 对节点数组的约束：

- 所有 **先于 K** 的节点：保持 `success`（及其 zip 元数据）；磁盘侧由引擎按 spec §4 **清空并解压复现**。
- **从 K 起至末尾**：在进入 `running` 前应重置为 `pending`（清除 K 及之后的完成标记、起止时间与 zip 路径等业务字段，具体列由实现计划定），然后按串行重新执行。

### 8.3 HTTP / JSON 字段约束

- `GET /tasks/{id}` 响应中：**必须**包含 `status`（任务，取值 §8.1）与 **`nodes` 数组**；每项 **必须**包含 `node_id`（或等价主键）与 `status`（取值 §8.2）。  
- 当 `status == "waiting_human"` 时 **必须**包含 **`interrupt`** 对象，字段至少包括：`seq`（等于 `interrupt_seq`）、`node_id`、`expected_schema`、`request_extras`（可空）、`checkpoint`（可空），语义见 **§3.4.2**。  
- 节点处于 `failed` 时 **建议**包含 `error: { "category": "business" | "validation" | "worker_lost", "message": string }`，便于控制台区分「业务失败」与 **stalled 链路上的节点失败**。  
- 任务处于 `failed` / `stalled` / `waiting_human` 时 **建议**包含人类可读 `message` 或结构化 `blocking_reason`（实现计划可选）。

### 8.4 错误处理与自动重试（与状态配合）

- **业务节点失败**：该节点 `failed`（`error.category` 为 `business` 或 `validation`），任务 `failed`；**默认无自动重试**。  
- **Worker 丢失**：任务 `stalled`；当前节点 `failed` 且 `error.category = "worker_lost"`；**不自动**续跑。  
- **interrupt**：任务 `waiting_human`，当前节点 `waiting_human`；`interrupt/resolve` 成功后任务回到 `running`，该节点从 `waiting_human` 进入 `running` 继续执行。

---

## 9. 测试策略

- **单元测试**：workflow 模型、runner 在白名单/zip/`rerun` 解压顺序、interrupt 状态机（可用 fake runner）。  
- **集成测试**：临时 SQLite + `TestClient`：多节点 dummy 工作流、interrupt→resolve、resume 不重扫解压、`rerun` 中段复现。  
- **前端**：关键组件测试可选；e2e 作为二期候选。

---

## 10. 已确认的决策摘要

| 项 | 决策 |
|----|------|
| 部署 | 单机内网 |
| 控制通道 | HTTP + 轮询（无 SSE/WS） |
| 工作流权威 | Python 库 API |
| 持久化 | SQLite WAL |
| 快照 | 每成功节点 zip；白名单；软忽略多写；**缺件硬失败** |
| 多任务 | 每任务独立进程 |
| interrupt | 语义、持久化、resolve 请求体、Runner/Supervisor 伪代码见 **§3.4** |
| 中途重跑 | 清空 + 顺序解压前置 zip |
| 任务拓扑 | 串行；`GET /tasks/{id}` 返回 `nodes` 数组，无单独 graph 接口 |
| 任务/节点状态 | 枚举、不变式、合法迁移与 DTO 约束见 **§8** |
| 目录、租约、创建任务、rerun 安全等 | **§3.5** |

---

## 11. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-05-14 | 初版：合并 brainstorming 第 1–3 节与用户修订（库注册、resume/rerun 目录语义、去掉 graph 接口）。 |
| 2026-05-14 | 增补 **§8**：任务/节点状态枚举、不变式、合法迁移、`rerun` 节点数组重置、HTTP 字段约束；**§2**/**§10** 与 §8 对齐。 |
| 2026-05-14 | 新增 **§3.5**：实现前须锁定之标识符、目录布局、worker 租约、`POST /tasks` 最小契约、rerun zip 安全、无产物节点、时间与错误 JSON、`stalled` 后 interrupt 字段清理、观测性建议；**§10** 索引更新。 |

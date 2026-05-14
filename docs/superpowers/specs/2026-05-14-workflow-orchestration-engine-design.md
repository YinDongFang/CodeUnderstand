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
| **人工介入（Interrupt）** | 节点执行至中断点时，持久化「期待输入」与 checkpoint；worker 退出；用户经 HTTP  Submit 后 **resume**。 |

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
| `POST` | `/tasks/{id}/interrupt/resolve` | 提交人工参数，触发 **resume**（spawn 新一代 worker）。 |
| `POST` | `/tasks/{id}/rerun` | body：`{ "from_node_id": "K" }`，触发 **清空 + 前置 zip 解压 + 从 K 执行**。 |

**并发语义：`rerun` 与运行中 worker** 的互斥策略须在实现中写死一种（例如仅 `非 running` 允许，或先取消再 `rerun`），并在该文档的实现计划阶段落到验收用例。

**鉴权**：单机内网默认可为「可信网段」；若需 Token，列为实现计划可选项。

---

## 6. 前端（Vite + React）

- **布局**：参考 Notion / Flowus——**左**任务列表，**右**详情。  
- **详情区自上而下**：任务元信息 → **按 `nodes` 数组顺序绘制串行流程**（状态色、耗时）→ 日志面板。  
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
| interrupt | 落库 + worker 退出；resume 同目录继续 |
| 中途重跑 | 清空 + 顺序解压前置 zip |
| 任务拓扑 | 串行；`GET /tasks/{id}` 返回 `nodes` 数组，无单独 graph 接口 |
| 任务/节点状态 | 枚举、不变式、合法迁移与 DTO 约束见 **§8** |

---

## 11. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-05-14 | 初版：合并 brainstorming 第 1–3 节与用户修订（库注册、resume/rerun 目录语义、去掉 graph 接口）。 |
| 2026-05-14 | 增补 **§8**：任务/节点状态枚举、不变式、合法迁移、`rerun` 节点数组重置、HTTP 字段约束；**§2**/**§10** 与 §8 对齐。 |

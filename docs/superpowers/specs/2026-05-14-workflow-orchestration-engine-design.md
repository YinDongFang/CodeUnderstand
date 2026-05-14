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
| **任务（Task）** | 某工作流的一次运行实例；具独立**任务根目录**、独立 worker 进程边界、独立 DB 记录。 |
| **节点（Node）** | 最小执行单元；状态：`pending` / `running` / `success` / `failed`，以及 `waiting_human`（任务级或节点级约定其一，实现时统一即可）。 |
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

## 8. 错误处理与状态

- **节点失败**：标 `failed`，任务进入失败终态；**默认无自动重试**。  
- **Worker 异常退出**：任务标为可恢复的「异常停止」类状态；**不自动猜**下一动作；允许人工 `rerun`。  
- **interrupt**：任务（或节点）进入 `waiting_human`；收到合法 payload 后迁移为可执行 resume。

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

---

## 11. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-05-14 | 初版：合并 brainstorming 第 1–3 节与用户修订（库注册、resume/rerun 目录语义、去掉 graph 接口）。 |

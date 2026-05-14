# wf_engine Web UI 与任务上下文（input / context）设计

**状态：** **定稿**（2026-05-15）  
**实现计划：** `docs/superpowers/plans/2026-05-15-wf-engine-web-context.md`  
**前置：** `docs/superpowers/specs/2026-05-14-workflow-orchestration-engine-design.md`、当前 e2e 已通过  
**关联实现：** 按计划 Task 执行；本稿不写死代码路径以外的强制文件名。

---

## 1. 目标

在**不削弱**现有编排、interrupt、rerun、租约与安全策略的前提下，增强 **Web 控制台** 与 **任务数据模型**：

1. 去掉 Web **topbar**，全屏左右布局。  
2. **创建任务**：按钮 + **模态框**——可选已注册 **workflow**、填写 **task name**、**input**（初始参数）、**context** 初始（可选）。  
3. 左侧列表：**主展示 task name**；不展示 workflow 名（workflow 仍可仅在详情或排障区出现）。  
4. 右侧任务元信息：**展示 name**；**input** 与 **context** 分区展示（context 风格接近「环境变量」只读键值 / JSON）。  
5. **双对象模型（已定）：**  
   - **`input`**：创建时写入 **快照，只读**（工作流「初始参数」）。  
   - **`context`**：运行期可变 JSON；节点可读写；**UI 轮询展示**。  
6. **日志**：支持按 **节点步骤** 折叠/展开（数据形态见 §5）。

---

## 2. 数据模型

### 2.1 `tasks` 表扩展

| 列 | 类型 | 说明 |
|----|------|------|
| `name` | `TEXT` | 任务展示名（建议 API 创建必填；存量可空，UI fallback 见 §6） |
| `input_json` | `TEXT` | 创建时写入；**引擎与节点均不得覆盖此列**（只读快照） |
| `context_json` | `TEXT` | 默认可为 `"{}"`；创建时可选预填；运行中持续反映当前上下文 |

**迁移：** `ALTER TABLE` 增加 `name`、`context_json`；老数据：`name` 空、`context_json` 为 `{}`。

### 2.2 `input` 与 `context` 语义

- **`input`**：仅来自 `POST /tasks` 的 `input`；之后 API **不提供** 修改 `input` 的常规路径（/admin 或二期再说）。  
- **`context`**：来自创建时可选 body `context` + 节点执行中的更新；**与节点成功/失败无关**——见 §3。

---

## 3. Context 持久化策略（**已确认**）

**原则：context 可随时落库，不因节点成功或失败而区别处理。**

**实现对实现者的约束（宽松但可测）：**

- 至少在以下时刻**必须**把内存中的当前 `context` 快照写入 `context_json`：  
  - 节点 Callable **正常返回**之后；  
  - 抛出 **`ControlledInterrupt`**（进入 `waiting_human`）之前或与其同一事务边界内，保证人工等待时 UI 已能拉到最新 context；  
  - 节点 Callable 抛出**其它异常**（业务/校验失败等）之后。  
- 若在单节点内**多次**修改同一 dict，实现可选用：  
  - **节点出口统一 flush**（推荐，实现简单、写库次数少）；或  
  - **每次赋值即 UPDATE**（更重，仅在确有需求时采用）。  

**不要求**「仅 success 才写 context」。

---

## 4. HTTP API

### 4.1 `POST /tasks`（扩展）

Body JSON 建议：

| 字段 | 必填 | 说明 |
|------|------|------|
| `workflow_key` | 是 | 与 `Engine` 注册一致 |
| `name` | 是* | *规格建议必填；若兼容旧客户端可暂允空，UI 必送 |
| `input` | 否 | 默认 `{}` → `input_json` |
| `context` | 否 | 默认 `{}` → 初始 `context_json` |

响应仍为 `201` + `task_id`。

### 4.2 `GET /tasks`（扩展）

摘要每条增加 **`name`**。可仍返回 `workflow_key` 供排障；**UI 列表仅用 `name`**。

### 4.3 `GET /tasks/{id}`（扩展）

在现有任务详情上增加：

- **`name`**  
- **`input`**：解析后的对象（只读语义文档化）  
- **`context`**：解析后的对象（可变）  

`nodes`、`interrupt`、`stalled` 等现有字段保持不变。

### 4.4 `GET /workflows`（新）

返回当前控制进程内已注册工作流列表，供模态框下拉，例如：

```json
[
  { "key": "demo_pipeline", "revision": "1" }
]
```

**一期**可仅 `key` + `revision`；`input` 的 JSON Schema 驱动表单可列为二期（或 workflow 元数据扩展）。

---

## 5. 日志与按节点折叠

**目标：** 右侧日志区可按 **节点** 折叠/展开，不显式依赖图布局算法。

**推荐一期：** 保持单机 `task.log`（或等价单文件），由 worker/runner 为每条日志行注入 **可解析的节点作用域**（例如 logger `name` 或统一前缀 `node={ordinal}:{node_id}`），`GET /tasks/{id}/logs` 可：

- **A**：仍返回平面 `lines[]`，由 **前端** 按前缀分组渲染折叠块；或  
- **B**：增加可选查询参数返回结构化分段（实现阶段二选一，本规格不锁定）。

**备选：** 每节点独立日志文件；一期优先避免文件碎片，除非前缀方案不足以区分。

---

## 6. Web UI 要点

| 项 | 要求 |
|----|------|
| 布局 | 无 topbar；左列表 + 右详情 |
| 创建 | 主按钮打开模态：`name`、workflow 下拉（`GET /workflows`）、`input` JSON、`context` JSON（可默认 `{}`） |
| 左栏 | 显示 **task name**；`name` 缺省时可用 `id` 短前缀 + 「未命名」等 fallback |
| 右栏元信息 | **name** 置顶或置于任务标题区；**input** / **context** 分块；context 表格化或语法高亮 JSON |
| 日志 | 按节点分段 UI，支持折叠/展开 |

---

## 7. `NodeContext` 与注册表（实现侧）

- `NodeContext` 暴露 **`input`**（只读映射到任务 `input_json` 快照）与 **`context`**（可变 dict， backed by `context_json`）。  
- Worker 须能加载 **`WF_ENGINE_REGISTRY_MODULE`**；与现有一致。  
- **`GET /workflows`** 数据来自 **`Engine`** 内注册表枚举（新方法 `list_workflows()` 或等价）。

---

## 8. 测试与验收

- **API：** `POST /tasks` 带 `name` / `input` / `context`；`GET` 详情可见且 `input` 不被后续节点改写。  
- **Runner：** 节点内修改 `context` 后，在 **失败**、**interrupt**、**success** 三种出口下，`GET` 均能看到预期 `context`（与 §3 一致）。  
- **UI：** 创建模态可用；列表只见 name；详情见 name + context；日志可折叠（与 §5 选定方案一致）。

---

## 9. 非目标（本期不做）

- 分布式任务队列、多机 worker。  
- 完整的「按 JSON Schema 自动生成创建表单」（可二期）。  
- `input` 的在线编辑与版本回滚。

---

## 10. 自审摘要

- **B 模型**（`input` 快照 + `context` 可变）与「环境变量式」展示一致。  
- **Context 落库**已与产品确认：**随时/每出口落库，不区分节点成功失败**。  
- 与旧客户端兼容点：`name` / `context_json` 缺省策略在 §2、§4 已留口。

# wf_engine 控制台全局配置（Ops + 系统）

**状态：** 定稿修订（2026-05-15）  
**前置：** `wf_engine` SQLite 控制面、现有任务 `context_json`、`NodeContext`、`run_once`。  
**实现计划：** `docs/superpowers/plans/2026-05-15-wf-console-global-ops-settings.md`

---

## 1. 目标与边界

1. 在 Web 控制台提供 **全局配置**：
   - **Ops（运维默认值）**：`cookie`、`authorization`（字符串；可多行）。
   - **系统**：**`tasks_root`（tasks 根目录路径）** 等可在 `settings` 中扩展的项（首版仅 `tasks_root`）。  
2. **与任务上下文分离**：**不**写入 `tasks.context_json`；Ops 语义同前版spec（**不是**浏览器访问 FastAPI 的 HTTP 头）。  
3. **任务执行可读 Ops**：节点通过 **`NodeContext.ops_globals`** 读取；**每次 `run_once` 入口** 从 DB 读最新 `ops_globals` 快照并注入。  
4. **任务目录语义**：
   - 服务启动时传入 **`ControlPlaneState.tasks_root`** 为**默认**根目录。
   - **控制台可覆盖**：持久化在 `settings` 的 `system_config`；**仅影响之后新建任务**在 `create_task` 时写入的 `tasks.tasks_root` 字段与磁盘布局。
   - **既有任务**：始终使用其行内已存储的 `tasks_root`（日志、rerun、worker `task_root` 均按该行解析）。覆盖项清空后，**新建**任务回到启动默认根目录。  
5. **UI**：**右栏**内 **「任务详情」/「系统设置」** 切换展示；**不使用 Modal** 承载设置页（新建任务仍可沿用 Modal）。

---

## 2. 数据模型

### 2.1 存储（SQLite）

- 表 **`settings`**：`key TEXT PRIMARY KEY`，`value_json TEXT NOT NULL`。  
- **key `ops_globals`**：JSON `{"cookie":"","authorization":""}`。  
- **key `system_config`**：JSON `{"tasks_root":""}`；`tasks_root` 为空串表示**不覆盖**，使用启动默认路径。非空时为已规范化（解析、`expanduser`、`resolve`）的目录路径字符串；写入前服务端创建目录（`mkdir -p`）。  
- 读损 / 非法 JSON：按空对象或空字段归一化（与 ops 行为一致）。

### 2.2 与 `tasks.context_json`

- **禁止**把上述全局配置合并进 `context_json` 作为唯一来源。  
- 节点可读 `ctx.ops_globals`；任务可变状态仍用 `ctx.context`。

---

## 3. HTTP API（控制面）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/settings` | 聚合：`{ "ops": {...}, "system": { "tasks_root", "server_tasks_root_default", "tasks_root_effective" } }` |
| `GET` | `/settings/ops` | `{ "cookie", "authorization" }` |
| `PUT` | `/settings/ops` | Body 同上，整对象替换。 |
| `GET` | `/settings/system` | 见下。 |
| `PUT` | `/settings/system` | Body：`{ "tasks_root": string }`；空串清除覆盖；非空则校验为目录路径、`resolve`、`mkdir`，持久化规范化路径。 |

**`GET /settings/system` 响应字段：**

- `tasks_root`：当前已持久化的覆盖值（可空串）。  
- `server_tasks_root_default`：进程启动时传入的默认路径字符串。  
- `tasks_root_effective`：新建任务实际使用的根目录（有覆盖则为覆盖解析结果，否则为默认）。

无效路径（非目录、无法创建等）：`422` + `error.code = invalid_tasks_root`。

---

## 4. 执行路径：`run_once` 与 `NodeContext`

- **Ops**：每次 `run_once` 在组装 `NodeContext` **之前**读取 `get_ops_globals()`；同 pass 内节点共享快照。  
- **`NodeContext`**：`ops_globals: dict[str, str]`，约定节点不写回。  
- **控制面路径**：`POST /tasks`、日志与 rerun 等使用的任务根目录为 **`Path(task_row["tasks_root"]) / task_id`**，与当前控制台覆盖无关（除非该任务当时按覆盖路径创建）。

---

## 5. Web UI

- **右栏顶部**：Tab **「任务详情」**、**「系统设置」**。  
- **系统设置页**：展示启动默认与当前生效根目录（只读提示）；可编辑 **覆盖 tasks 根**；Ops **Cookie** / **Authorization**（`textarea`）；**保存全部** 分别或并行调用 `PUT /settings/ops` 与 `PUT /settings/system`。  
- **不**使用 Modal 展示上述设置表单。

---

## 6. 安全与运维

- 数据库持久化敏感串与路径；限制 DB 与进程文件权限；生产建议控制面鉴权。  
- 首版不要求 At Rest 加密。

---

## 7. 测试要点

- Store：`system_config` roundtrip；缺行/坏 JSON 回退。  
- API：`GET /settings`、`GET/PUT /settings/system`；`PUT` 后 `POST /tasks` 落盘于新根。  
- 任务 API：`create_task` 使用 `effective_tasks_root`；已有任务路径仍读行内 `tasks_root`。  
- Runner：`ops_globals` 注入（既有用例）。

---

## 8. 自检

- 全局与 `context_json` 分离；Ops 读时机固定；非「浏览器请求头」语义；**既有任务目录不随控制台覆盖漂移**。  
- UI 在右栏、无设置 Modal。

---

## 9. 实现计划

历史执行记录：`docs/superpowers/plans/2026-05-15-wf-console-global-ops-settings.md`（后续增量可另开 plan）。

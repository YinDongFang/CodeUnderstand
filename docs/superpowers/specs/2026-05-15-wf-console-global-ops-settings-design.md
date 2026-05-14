# wf_engine 控制台全局设置（统一模型）

**状态：** 定稿修订（2026-05-15）  
**实现计划（历史）：** `docs/superpowers/plans/2026-05-15-wf-console-global-ops-settings.md` — 与当前实现有演进差异，以本 spec 为准。

---

## 1. 目标与边界

1. **单一语境**：所有控制台持久化项均为 **全局设置**，不设「系统 / Ops」等业务分区；语义上可在 **控制面进程内** 与 **任务执行（`run_once`）内** 读取。  
2. **持久化字段（首版）**  
   - `tasks_root`：tasks 根目录覆盖（空串 = 使用进程启动时 `ControlPlaneState.tasks_root`）。  
   - `cookie`、`authorization`：字符串，可多行（**非**浏览器访问 FastAPI 的请求头）。  
3. **与 `tasks.context_json` 分离**：不写入任务 context；节点可变状态仍用 `ctx.context`。  
4. **任务目录**  
   - **新建任务**：使用「有效根目录」×`task_id` 落盘，并将该根路径字符串记入 `tasks.tasks_root`。  
   - **已有任务**：日志 / rerun / worker 的 `task_root` 一律按该行 `tasks_root` 解析；**不随后续控制台改覆盖而漂移**。  
5. **UI**  
   - **入口**：左侧 **「系统设置」** 与 **「新建任务」** 并列；点击「系统设置」在右侧展示设置表单。  
   - **任务详情**：点击左侧任务行在右侧展示该任务；**右侧顶栏不设** 模式切换按钮。  
   - **设置表单**：**不用 Modal**（新建任务 Modal 可保留）。  
   - **文案**：设置页 **不出现** 说明性段落/帮助文案；仅字段名与只读键值。

---

## 2. 存储（SQLite）

- 表 `settings`：`key` PK，`value_json`。  
- **主键 `console_settings`**：JSON  
  `{"tasks_root":"","cookie":"","authorization":""}`  
- **迁移**：若仅有历史行 `ops_globals`、`system_config`，读时合并为第一方模型；**写**时统一落 `console_settings` 并删除历史两行。

---

## 3. HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/settings` | 扁平：`tasks_root`, `cookie`, `authorization`, `server_tasks_root_default`, `tasks_root_effective` |
| `PUT` | `/settings` | Body 同上前三项；`tasks_root` 空串清除覆盖；非空则 `resolve` + `mkdir` 后存规范化路径 |

---

## 4. `run_once` 与 `NodeContext`

- 每次进入执行路径（非 `WAITING_HUMAN` 早退）：在节点循环前读取 **`get_console_settings()`**，与 **本次任务的** `task_root` 派生信息合并为 **`ctx.settings: dict[str, str]`**（同一 pass 内共享引用）。  
- **建议键**（稳定契约）：  
  - `tasks_root`、`cookie`、`authorization`（与库内覆盖一致）  
  - `task_parent_dir`：`str(task_root.resolve().parent)`（本任务数据目录的父路径，即创建时采用的任务根）  
- 约定：节点 **不写回** `settings` 到 DB；写库仍走既有 store API。

---

## 继承与原 spec 差异

- 废弃分路径 `GET/PUT /settings/ops`、`/settings/system`（由单一 `/settings` 取代）。  
- 废弃 `NodeContext.ops_globals`，改为 **`NodeContext.settings`**。

---

## 5. 测试要点

- Store：`console_settings` 往返；历史 `ops`+`system` 行合并与写后清理。  
- API：`GET/PUT /settings`；`PUT` 后 `POST /tasks` 使用新根。  
- Runner：`ctx.settings` 含 cookie/authorization 与 `task_parent_dir`。

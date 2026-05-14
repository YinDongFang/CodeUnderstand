# wf_engine Web UI、task name 与 input/context 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**交付状态（2026-05-15 修订）：** 该计划主体已实现，当前仓库以后续代码与 `docs/superpowers/specs/2026-05-15-wf-engine-web-context-design.md` 为准。下方未勾选步骤保留为执行日志，不代表当前未完成项。后续维护重点：创建任务 UI 必须同时支持 schema 字段表单、raw `input` JSON 与 raw `context` JSON。

**Goal:** 落实定稿规格 `docs/superpowers/specs/2026-05-15-wf-engine-web-context-design.md`：任务 `name` + 只读 `input` + 可变 `context`（节点出口落库）、`GET /workflows`、日志节点前缀供前端折叠、Web 去 topbar + 创建任务模态 + 列表/详情展示调整。

**Architecture:** SQLite 迁移增列；`NodeContext` 持 `input`（快照）与 `context`（可变 dict）；`run_once` 每节点 Callable 结束后及 `ControlledInterrupt`/普通异常路径上 **`save_task_context`**；worker 日志用 `contextvars` + `logging.Filter` 注入 `wf_node` 供 Formatter 前缀；API 扩展 `POST/GET /tasks` 与新增 `GET /workflows`；前端按前缀解析日志行分组折叠。

**Tech stack:** Python 3.12、FastAPI、SQLite、pytest、httpx；Vite/React/TypeScript。

**Spec:** `docs/superpowers/specs/2026-05-15-wf-engine-web-context-design.md`（定稿）。

---

## File map

| 路径 | 变更 |
|------|------|
| `wf_engine/store/sqlite.py` | `CREATE TABLE` 增列、`migrate`、 `create_task` 签名字段、`save_task_context`、`get_task` 解析 `context_json` |
| `wf_engine/engine.py` | `list_workflows()` → `list[dict]` |
| `wf_engine/context.py` | `NodeContext` 增 `input`、`context` |
| `wf_engine/runner.py` | 加载/回写 context；每节点出口 flush；构造 `NodeContext` |
| `wf_engine/worker_main.py` | 日志 Filter/Formatter 注入节点前缀（`contextvars`） |
| `wf_engine/server/routes_tasks.py` | `CreateTaskBody`、`GET /workflows`、序列化 `name`/`input`/`context` |
| `wf_engine/server/app.py` | 挂新路由（若拆文件则 `routes_workflows.py`，一期可写在 `routes_tasks`） |
| `tests/wf_engine/test_sqlite_store.py` | 迁移 + `save_task_context` |
| `tests/wf_engine/test_api_tasks.py` | `POST` body、`GET` 字段、工作流列表 |
| `tests/wf_engine/test_runner_serial.py` 等 | `create_task` 新参数；可加 `test_runner_context.py` |
| `web/src/api.ts` | 类型、`fetchWorkflows`、`createTask` |
| `web/src/App.tsx` | 无 topbar、模态、列表/详情、日志折叠 |
| `web/src/App.css` | 模态与日志节样式 |
| `run_wf_engine.py` | 无需改逻辑（registry 已有）；自测时走新 UI |
| `workflow/demo.py` | 可选：某节点写 `ctx.context["step"] = ...` 便于肉眼验收 |

---

### Task 1: SQLite：`name`、`context_json` 与 `save_task_context`

**Files:**

- Modify: `wf_engine/store/sqlite.py`
- Test: `tests/wf_engine/test_sqlite_store.py`

- [ ] **Step 1: 写失败测试 — 新建库含 `name`/`context_json`，`save_task_context` 可读**

```python
def test_create_task_with_name_and_context_roundtrip(tmp_path):
    from pathlib import Path
    from wf_engine.store.sqlite import SqliteStore
    from wf_engine import status as S

    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        name="我的任务",
        workflow_key="w",
        workflow_revision="1",
        input_obj={"a": 1},
        context_obj={"env": "dev"},
        tasks_root=str(tmp_path / "runs"),
    )
    row = store.get_task(tid)
    assert row["name"] == "我的任务"
    assert row["input_json"] == {"a": 1}
    assert row["context_json"] == {"env": "dev"}

    store.save_task_context(tid, {"env": "dev", "k": 2})
    row2 = store.get_task(tid)
    assert row2["context_json"] == {"env": "dev", "k": 2}
```

（在实现前 `create_task` / `save_task_context` / 列不存在会导致 ImportError 或 AssertionError。）

- [ ] **Step 2: 运行测试**

```bash
py -3 -m pytest tests/wf_engine/test_sqlite_store.py::test_create_task_with_name_and_context_roundtrip -v
```

Expected: FAIL。

- [ ] **Step 3: 实现**

1. 在 `init_schema` 的 `CREATE TABLE tasks` 中增加：`name TEXT`、`context_json TEXT NOT NULL DEFAULT '{}'`（若目标 SQLite 对 `DEFAULT` 敏感，则用可空列 + `get_task` 默认 `{}`）。  
2. 在 `init_schema` 末尾调用 `_migrate_tasks_table(c)`：  
   - `PRAGMA table_info(tasks)`，若无 `name` 则 `ALTER TABLE tasks ADD COLUMN name TEXT;`  
   - 若无 `context_json` 则 `ALTER TABLE tasks ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';`（或 nullable + `UPDATE tasks SET context_json = '{}' WHERE context_json IS NULL`）。  
3. `create_task(..., name: str | None = None, context_obj: dict | None = None, ...)`：`name` 存库允许 `NULL` 兼容旧数据；`context_obj` 默认 `{}`。INSERT 列清单同步更新。  
4. `get_task`：解析 `context_json` 为 Python dict（缺列或空串时用 `{}`）；保持 `input_json` 键名或后续在路由层映射为 `input`（与现实现一致）。  
5. `def save_task_context(self, task_id: str, obj: dict) -> None:`：`UPDATE tasks SET context_json=?, updated_at=? WHERE id=?`。

- [ ] **Step 4: 再跑 Step 1 测试 — PASS**

- [ ] **Step 5: 老库迁移测试**

```python
def test_migrate_adds_name_and_context_columns(tmp_path):
    import sqlite3
    from pathlib import Path
    from wf_engine.store.sqlite import SqliteStore

    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE tasks (id TEXT PRIMARY KEY, workflow_key TEXT, workflow_revision TEXT,
        status TEXT, input_json TEXT, tasks_root TEXT, created_at TEXT, updated_at TEXT,
        interrupt_seq INTEGER DEFAULT 0, interrupt_response_consumed INTEGER DEFAULT 0,
        worker_generation INTEGER DEFAULT 0)"""
    )
    # 省略其它列若需要最小复现：可直接复制当前 DDL 再删掉 name/context_json
    conn.close()
    store = SqliteStore(db)
    store.init_schema()  # 触发 migrate
    with store.connect() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(tasks)")]
    assert "name" in cols and "context_json" in cols
```

- [ ] **Step 6: Commit**  
  `git commit -m "feat(store): task name, context_json, save_task_context + migrate"`

---

### Task 2: `Engine.list_workflows()`

**Files:**

- Modify: `wf_engine/engine.py`
- Test: `tests/wf_engine/test_engine_registry.py`（新建用例或追加）

- [ ] **Step 1: 失败测试**

```python
def test_engine_lists_registered_workflows():
    from wf_engine.engine import Engine
    from wf_engine.workflow import Workflow

    eng = Engine()
    wf = Workflow(key="a", revision="2")

    def n(ctx):
        pass

    wf.add_node("x", n)
    eng.register_workflow(wf)
    rows = eng.list_workflows()
    assert rows == [{"key": "a", "revision": "2"}]
```

- [ ] **Step 2: `py -3 -m pytest tests/wf_engine/test_engine_registry.py::test_engine_lists_registered_workflows -v`** — FAIL

- [ ] **Step 3: 实现**

```python
def list_workflows(self) -> list[dict[str, str]]:
    return [{"key": wf.key, "revision": wf.revision} for wf in self._workflows.values()]
```

（若需稳定排序：`sorted(..., key=lambda x: x["key"])`。）

- [ ] **Step 4: PASS + commit**  
  `git commit -m "feat(engine): list_workflows for control plane"`

---

### Task 3: HTTP：`GET /workflows` 与扩展 `POST/GET /tasks`

**Files:**

- Modify: `wf_engine/server/routes_tasks.py`（或 `routes_workflows.py`）
- Modify: 所有调用 `store.create_task(...)` 的测试与 `run_wf_engine` 若直接调 store——搜索 `create_task(`  
- Test: `tests/wf_engine/test_api_tasks.py`

- [ ] **Step 1: 扩展 `CreateTaskBody`**

```python
class CreateTaskBody(BaseModel):
    workflow_key: str
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 2: `create_task` 路由**  
  `cp.store.create_task(..., name=body.name or None, input_obj=body.input, context_obj=body.context, ...)`（名称存空串或 `NULL` 由 store 约定：建议 **`body.name.strip() or None`** 存 `NULL`，UI 显示「未命名」）。

- [ ] **Step 3: `_serialize_task_detail`**  
  增加 `"name": row.get("name")`，`"input": row["input_json"]`（已是 dict），`"context": row["context_json"]`（`get_task` 提供解析后键名：可在 store 里用 `context` 键别名避免与列名混淆，或在路由里 `_loads` — 推荐 **store.get_task 统一输出 `input`/`context` 键** 并保留内部列名兼容，减少路由重复；实现时二选一但全仓一致。）

- [ ] **Step 4: `list_tasks` 摘要**  
  每条增加 `"name": r.get("name")`（或 `display_name` 字段名与 spec 一致用 `name`）。

- [ ] **Step 5: 新路由**

```python
@router.get("/workflows")
def list_workflows(request: Request) -> list[dict[str, str]]:
    cp = _cp(request)
    return cp.engine.list_workflows()
```

- [ ] **Step 6: 测试**  
  - `POST /tasks` JSON 含 `name`、`context`，`GET /tasks/{id}` 断言 `name`、`context`。  
  - `GET /workflows` 返回非空（fixture 注册 `api_wf`）。  

- [ ] **Step 7: Commit**  
  `git commit -m "feat(api): name+context on tasks, GET /workflows"`

---

### Task 4: `NodeContext` + `run_once` 加载/回写 `context`

**Files:**

- Modify: `wf_engine/context.py`
- Modify: `wf_engine/runner.py`
- Test: 新建 `tests/wf_engine/test_runner_context.py`

- [ ] **Step 1: 扩展 `NodeContext`**

```python
@dataclass
class NodeContext:
    task_id: str
    node_id: str
    workflow_key: str
    task_root: Path
    workspace: Path
    node_workdir: Path
    human_input: dict[str, Any] | None
    input: dict[str, Any]
    context: dict[str, Any]
```

- [ ] **Step 2: 失败测试（节点改 context，失败也落库）**

```python
def test_runner_persists_context_on_business_failure(tmp_path):
    # 建 store + workflow：n1 写 context；n2 raise；断言 get_task context 已有 n1 写入；n2 失败后仍有
    ...
```

（具体：Task 1 `create_task` 签名为准；`run_once` 后 `store.get_task(tid)["context_json"` or `["context"]` 断言。）

- [ ] **Step 3: `run_once` 开头**  
  `task = store.get_task(task_id)`，`base_input = copy.deepcopy(task["input_json"])` 或 `task["input"]`（以 store 输出为准），`ctx_data = copy.deepcopy(task.get("context") or task.get("context_json") or {})`。

- [ ] **Step 4: 每个节点**  
  构造 `NodeContext(..., input=base_input, context=ctx_data)`；在 **`try`/`except ControlledInterrupt`/`except Exception` 的共通通路** 调用 `store.save_task_context(task_id, ctx_data)`（或使用 `finally` 针对该节点块，注意 **不要在未执行节点前写盘**——仅在该节点 `spec.fn` 已调用之后）。  
  **interrupt：** 在 `open_interrupt` **之前** `save_task_context`。  
  **return 前成功路径：** zip 成功后亦 save（与 finally 合并一次即可避免重复）。

- [ ] **Step 5: 保证 `input` 不被覆盖**  
  全文件搜索 `input_json` 的 `UPDATE`，禁止在 runner 写 `input_json`。

- [ ] **Step 6: 跑 `pytest tests/wf_engine/test_runner_context.py tests/wf_engine/test_runner_serial.py`** — PASS

- [ ] **Step 7: Commit**  
  `git commit -m "feat(runner): NodeContext input+context and persist context per node exit"`

---

### Task 5: 日志节点前缀（供前端折叠）

**Files:**

- Modify: `wf_engine/worker_main.py`
- Modify（如需）: `wf_engine/runner.py` 设置 `contextvars`

- [ ] **Step 1: 在 `runner.py` 顶部** `import contextvars`：`wf_log_node = contextvars.ContextVar[str]("wf_log_node", default="")`

- [ ] **Step 2: 每个节点执行前**  
  `wf_log_node.set(f"[{ordinal}:{spec.id}]")`；`finally`/`except` 后 `wf_log_node.set("")`。

- [ ] **Step 3: `worker_main._configure_task_file_logging`**  
  - 给 root handler 加 `logging.Filter`，在 `filter` 里 `record.wf_node = wf_log_node.get("")`。  
  - `Formatter`: `"%(asctime)s | %(levelname)s | %(wf_node)s | %(name)s | %(message)s"`（`wf_node` 缺省属性时用 `setattr` 或在 Formatter 里处理）。

- [ ] **Step 4: 手工或集成测试**  
  跑一个短任务，读 `task.log` 断言含 `[0:fetch]` 类前缀（测试可放在 `test_supervisor` 或轻量 subprocess，若太重则文档化手工验收）。

- [ ] **Step 5: Commit**  
  `git commit -m "feat(worker): prefix task.log lines with node ordinal:id"`

---

### Task 6: 全量回归 `tests/wf_engine`

- [ ] **Step 1:** 全局替换 `create_task(` 调用处补 `name=`、`context_obj=` 或使用默认关键字。  
- [ ] **Step 2:** `py -3 -m pytest tests/wf_engine/ -q` — 全 PASS

- [ ] **Step 3: Commit**  
  `git commit -m "test(wf_engine): align create_task with name/context"`

---

### Task 7: Web — 无 topbar、创建模态、列表/详情、日志折叠

**Files:**

- Modify: `web/src/api.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.css`

- [ ] **Step 1: `api.ts`**  
  - `TaskSummary` 增 `name?: string | null`  
  - `TaskDetail` 增 `name`、**`context: Record<string, unknown>`**（`input` 已与后端对齐）  
  - `export function fetchWorkflows(): Promise<{ key: string; revision: string }[]>`  
  - `export function createTask(body: { workflow_key: string; name: string; input: Record<string, unknown>; context: Record<string, unknown> }): Promise<{ task_id: string }>`

- [ ] **Step 2: 去掉 `.topbar`** 及 `shell` 布局改为全高双栏；左上角或左栏顶放 **「新建任务」**。  

- [ ] **Step 3: 模态**  
  - 打开时 `fetchWorkflows()` 填充 `<select>`。  
  - 字段：`name`（必填 UI）、workflow、`textarea` 两个 JSON（`input` / `context`，默认 `{}`），校验 JSON 后 `POST`。  
  - 成功后关闭模态、`fetchTasks()`、可选 `setSelectedId`。

- [ ] **Step 4: 左侧列表**  
  主标题：`task.name?.trim() || \`未命名 (${id.slice(0, 8)}…)\``；**不显示** `workflow_key`（可收进 `title` tooltip）。

- [ ] **Step 5: 右侧元信息**  
  `name` 醒目展示；**input** / **context** 分块（context 用 `<table>` 键值或 `pre`）。  

- [ ] **Step 6: 日志折叠**  
  - 按行扫描：若匹配 `^\s*\[\d+:[^\]]+\]`（与 worker 前缀一致），新开一节；否则并入当前节。  
  - 每节 `<details>` 或自定义展开，标题为节点标签 + 行数。  
  - 无前缀行归入 **「全局」** 一节。

- [ ] **Step 7: `npm run build`** — PASS

- [ ] **Step 8: Commit**  
  `git commit -m "feat(web): create-task modal, name+context UI, log sections by node"`

---

### Task 8: 文档与 demo 可选增强

- [ ] **Step 1:** 更新 `wf_engine/README.md` 与 `docs/superpowers/specs/2026-05-15-wf-engine-web-context-design.md` 若 API 示例有变（*名称/示例 JSON*）。  
- [ ] **Step 2:** `workflow/demo.py` 在 `step_fetch` 写 `ctx.context["last_step"] = "fetch"` 之类。  
- [ ] **Step 3: Commit**  
  `git commit -m "docs+workflow: demo context + README API fields"`

---

## Self-review（对照定稿 spec）

| Spec § | 覆盖 Task |
|--------|-----------|
| 目标 1 无 topbar | Task 7 |
| 目标 2 创建模态 | Task 7 + Task 3 |
| 目标 3 列表 name | Task 7 |
| 目标 4 详情 name + input/context | Task 3 + 7 |
| 目标 5 双对象 | Task 1 + 4 |
| 目标 6 日志折叠 | Task 5 + 7 |
| §3 context 落库与成败无关 | Task 4 |
| §4 GET /workflows | Task 2 + 3 |

**占位符扫描：** 无 TBD。  
**类型一致：** `create_task` 关键字在全仓与测试统一为 `name=`、`context_obj=`（或 plan 实施时选用之最终名，全文一致）。

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-wf-engine-web-context.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration  

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints  

Which approach?

---

**定稿 spec 已链接：** 若你本地还要把「定稿」与「计划路径」写进 `2026-05-14-workflow-orchestration-engine-design.md` 的索引，可另开一句脚注（非本计划强制）。

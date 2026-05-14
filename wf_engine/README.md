# wf_engine

单机 Python 工作流编排库：串行节点、子进程隔离执行、SQLite 持久化、人工闸门（interrupt/resume）与最小 FastAPI 控制面。

详细设计见：[工作流编排引擎设计说明](../docs/superpowers/specs/2026-05-14-workflow-orchestration-engine-design.md)。

## 定义工作流

在 `workflow/` 目录下创建任意 `.py` 文件（非 `_` 前缀），暴露 `register_all(engine)` 函数：

```python
# workflow/my_pipeline.py
from wf_engine import Engine, Workflow, NodeContext

def register_all(engine: Engine) -> None:
    wf = Workflow(key="my_pipeline")
    wf.add_node("step1", my_fn, workdir=".", whitelist=["out.txt"])
    engine.register_workflow(wf)
```

启动时 `Engine` 自动扫描 `workflow/` 目录，调用每个模块的 `register_all`。无需手动导入。

## 启动控制面

```python
from pathlib import Path
from wf_engine import Engine

engine = Engine()
engine.serve(
    host="127.0.0.1",
    port=8000,
    db_path=Path("./data/engine.sqlite"),
    tasks_root=Path("./data/tasks"),
)
```

`serve()` 内自动执行 `discover_workflows()`，扫描 `workflow/` 目录。Worker 子进程启动时同样自动发现，无需传参。

## Worker 模型

每个任务在独立子进程中执行（`python -m wf_engine.worker_main`）。子进程启动后自动调用 `engine.discover_workflows()` 重建工作流注册，然后执行 `run_once`。进程隔离保证了任务间状态独立、崩溃不互相影响。

## 示例：人工闸门（`interrupt`）

节点首次运行可抛出受控中断以等待人工输入；解析后同一节点带着 `ctx.human_input` 再次执行：

```python
from wf_engine import Engine, Workflow, NodeContext, interrupt

def human_gate(ctx: NodeContext):
    if ctx.human_input is not None:
        (ctx.node_workdir / "out.txt").write_text(ctx.human_input["text"], encoding="utf-8")
        return
    interrupt(expected_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})

def register_all(engine: Engine):
    wf = Workflow(key="demo")
    wf.add_node("g", human_gate, workdir=".", whitelist=["out.txt"])
    engine.register_workflow(wf)
```

## 本地开发：API + Web UI

1. 在项目根目录启动 API（按你的入口脚本调用 `Engine().serve(...)` 或使用项目文档中的 uvicorn 命令），监听例如 `8000`。
2. 前端开发服务器：

```bash
cd web && npm run dev
```

Vite 开发时将 `/api` 代理到本地控制面（例如 `http://127.0.0.1:8000`），便于联调任务列表、详情与 interrupt 解析界面。

## HTTP 控制面：任务与工作流

以下为与 Web UI 联调常用的 JSON 字段（路径相对控制面根，例如 `http://127.0.0.1:8000`）。

### `POST /tasks`（201）

创建任务并启动 worker。请求体（JSON）主要字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `workflow_key` | string | **必填**，须在控制进程 `Engine` 中已注册。 |
| `name` | string | 可选；若为空或仅空白，存库为「未命名」（`null`），前端可展示默认标题。 |
| `input` | object | 可选，默认 `{}`；任务级只读快照，节点通过 `NodeContext.input` 读取。 |
| `context` | object | 可选，默认 `{}`；可变上下文，节点可写 `ctx.context[...]`，runner 在节点结束后持久化（详见设计与 runner 行为）。 |

响应：`{"task_id": "<uuid>"}`。

### `GET /tasks/{task_id}`

任务详情。除 `id`、`status`、`workflow_key`、`nodes` 等外，还包括：

| 字段 | 说明 |
|------|------|
| `name` | 创建时传入的名称，可能为 `null`。 |
| `input` | 创建时的 `input` 快照（object）。 |
| `context` | 当前持久化后的可变上下文（object），随节点执行更新。 |

若任务处于人工闸门，响应中可能含 `interrupt` 等扩展字段（与现实现一致）。

### `GET /workflows`

返回已注册工作流列表，JSON 数组，每项为 `{"key": "<workflow_key>"}`，供创建任务时下拉选择。

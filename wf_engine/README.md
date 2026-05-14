# wf_engine

单机 Python 工作流编排库：串行节点、子进程隔离执行、SQLite 持久化、人工闸门（interrupt/resume）与最小 FastAPI 控制面。

详细设计见：[工作流编排引擎设计说明](../docs/superpowers/specs/2026-05-14-workflow-orchestration-engine-design.md)。

## 在控制进程里注册工作流

在同一进程内构造 `Engine`，用 `Workflow` 描述节点并调用 `register_workflow`。重复的 `workflow_key` 会抛出 `ValueError`。

```python
from wf_engine import Engine, Workflow

def register_all(engine: Engine) -> None:
    wf = Workflow(key="demo", revision="1")
    # wf.add_node("step1", fn, workdir=".", whitelist=("*.txt",))
    engine.register_workflow(wf)

engine = Engine()
register_all(engine)
```

启动 HTTP 服务前，控制进程里必须有与工作流键一致的注册信息（通常与下面 worker 使用的模块共用同一个 `register_all`）。

## Worker：`WF_ENGINE_REGISTRY_MODULE` 与 `register_all(engine)`

Worker 子进程只携带 `workflow_key` 等环境变量，不会序列化用户代码。子进程会导入环境变量 **`WF_ENGINE_REGISTRY_MODULE`** 指向的 Python 模块（例如 `yourapp.registry`），并调用该模块上的 **`register_all(engine: Engine)`**，从而在空 `Engine` 上重建与工作流键相同的注册。

模块必须暴露：

```python
def register_all(engine: Engine) -> None:
    ...
```

控制面在创建任务并 `spawn_worker` 时，会把你在 `Engine.serve(..., registry_module=...)` 传入的模块路径传给子进程（对应设置 `WF_ENGINE_REGISTRY_MODULE`）。本地调试 worker 时也可手动导出该环境变量后再运行 `python -m wf_engine.worker_main`。

## 启动控制面：`Engine().serve(...)`

使用单进程 Uvicorn（`workers=1`），示例：

```python
from pathlib import Path
from wf_engine import Engine

engine = Engine()
# 与 worker 共用：在引擎上注册工作流，并传入同一 registry 模块路径
register_all(engine)

engine.serve(
    host="127.0.0.1",
    port=8000,
    db_path=Path("./data/engine.sqlite"),
    tasks_root=Path("./data/tasks"),
    registry_module="yourapp.registry",  # register_all 所在模块的 import 路径；可选，不传则 worker 无法加载用户工作流
)
```

参数含义：`db_path` 为 SQLite 文件路径；`tasks_root` 为任务根目录（每条任务在其下拥有独立目录）；`registry_module` 为 worker 导入注册逻辑的模块名（与 `WF_ENGINE_REGISTRY_MODULE` 一致）。

## 示例：人工闸门（`interrupt`）

节点首次运行可抛出受控中断以等待人工输入；解析后同一节点带着 `ctx.human_input` 再次执行：

```python
from wf_engine import Engine, Workflow, NodeContext, interrupt

def human_gate(ctx: NodeContext):
    if ctx.human_input is not None:
        (ctx.node_workdir / "out.txt").write_text(ctx.human_input["text"], encoding="utf-8")
        return
    interrupt(expected_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})

def build(engine: Engine):
    wf = Workflow(key="demo")
    wf.add_node("g", human_gate, workdir=".", whitelist=["out.txt"])
    engine.register_workflow(wf)
```

将 `build` 的逻辑并入你的 `register_all(engine)` 即可在控制进程与 worker 中共用。

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

返回已注册工作流列表，JSON 数组，每项为 `{"key": "<workflow_key>", "revision": "<revision>"}`，供创建任务时下拉选择。

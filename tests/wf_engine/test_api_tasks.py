"""FastAPI task API integration tests (ASGI transport)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from wf_engine import status as S
from wf_engine.context import NodeContext
from wf_engine.engine import Engine
from wf_engine.interrupt import interrupt
from wf_engine.paths import task_layout
from wf_engine.runner import run_once
from wf_engine.server.app import create_app
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow


def _sync_spawn(engine: Engine, store: SqliteStore):
    def _spawn(*, db_path, task_id, task_root, workflow_key, registry_module=None, extra_env=None, cwd=None, **kwargs):
        wf = engine.get_workflow(workflow_key)
        run_once(store=store, workflow=wf, task_id=task_id, task_root=Path(task_root))

    return _spawn


@pytest.fixture
def api_setup(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()

    eng = Engine()
    wf = Workflow(key="api_wf")

    def n1(ctx):
        (ctx.node_workdir / "out.txt").write_text("x", encoding="utf-8")

    wf.add_node("step1", n1, whitelist=["out.txt"])
    eng.register_workflow(wf)

    tasks_root = tmp_path / "runs"
    app = create_app(
        eng,
        store,
        tasks_root,
        registry_module=None,
        spawn_worker_fn=_sync_spawn(eng, store),
    )
    return {"app": app, "store": store, "tasks_root": tasks_root}


def test_post_tasks_returns_201_and_get_shows_succeeded_nodes(api_setup):
    app = api_setup["app"]
    tasks_root: Path = api_setup["tasks_root"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "api_wf", "input": {}})
            assert r.status_code == 201
            task_id = r.json()["task_id"]

            r2 = await client.get(f"/tasks/{task_id}")
            assert r2.status_code == 200
            body = r2.json()
            assert body["status"] == S.TASK_SUCCEEDED
            assert body["name"] is None
            assert body["input"] == {}
            assert body["context"] == {}
            assert len(body["nodes"]) == 1
            assert body["nodes"][0]["node_id"] == "step1"
            assert body["nodes"][0]["status"] == S.NODE_SUCCESS

            tr = tasks_root / task_id
            assert (task_layout(tr).workspace / "out.txt").read_text(encoding="utf-8") == "x"

    asyncio.run(_run())


def test_post_tasks_duplicate_name_returns_409(api_setup):
    app = api_setup["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            body = {"workflow_key": "api_wf", "name": " same-name ", "input": {}}
            r = await client.post("/tasks", json=body)
            assert r.status_code == 201
            r2 = await client.post("/tasks", json=body)
            assert r2.status_code == 409
            out = r2.json()
            assert out["error"]["code"] == "duplicate_task_name"

    asyncio.run(_run())


def test_list_tasks_returns_summary(api_setup):
    app = api_setup["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "api_wf", "input": {"k": 1}})
            assert r.status_code == 201
            task_id = r.json()["task_id"]

            listed = await client.get("/tasks")
            assert listed.status_code == 200
            rows = listed.json()
            assert len(rows) == 1
            assert rows[0]["id"] == task_id
            assert rows[0]["workflow_key"] == "api_wf"
            assert rows[0]["status"] == S.TASK_SUCCEEDED
            assert rows[0]["name"] is None
            assert "created_at" in rows[0]

    asyncio.run(_run())


def test_post_tasks_with_name_and_context_roundtrip(api_setup):
    app = api_setup["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/tasks",
                json={
                    "workflow_key": "api_wf",
                    "name": "  my job  ",
                    "input": {"a": 1},
                    "context": {"env": "test"},
                },
            )
            assert r.status_code == 201
            task_id = r.json()["task_id"]

            detail = await client.get(f"/tasks/{task_id}")
            assert detail.status_code == 200
            body = detail.json()
            assert body["name"] == "my job"
            assert body["input"] == {"a": 1}
            assert body["context"] == {"env": "test"}

            listed = await client.get("/tasks")
            assert listed.json()[0]["name"] == "my job"

    asyncio.run(_run())


def test_get_workflows_lists_registered(api_setup):
    app = api_setup["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/workflows")
            assert r.status_code == 200
            assert r.json() == [{"key": "api_wf", "revision": "1", "input_schema": None}]

    asyncio.run(_run())


def test_task_logs_with_cursor(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()

    eng = Engine()
    wf = Workflow(key="api_log")

    def n1(ctx):
        (ctx.node_workdir / "out.txt").write_text("z", encoding="utf-8")

    wf.add_node("only", n1, whitelist=["out.txt"])
    eng.register_workflow(wf)

    tasks_root = tmp_path / "runs"
    app = create_app(
        eng,
        store,
        tasks_root,
        spawn_worker_fn=_sync_spawn(eng, store),
    )

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "api_log", "input": {}})
            task_id = r.json()["task_id"]

            log_path = task_layout(tasks_root / task_id).logs / "task.log"
            log_path.write_text("alpha\nbeta\n", encoding="utf-8")

            r2 = await client.get(f"/tasks/{task_id}/logs")
            assert r2.status_code == 200
            payload = r2.json()
            assert payload["lines"] == ["alpha", "beta"]

            mid = payload["next_cursor"] // 2
            r3 = await client.get(f"/tasks/{task_id}/logs?cursor={mid}")
            rest = r3.json()
            assert rest["next_cursor"] == payload["next_cursor"]
            assert isinstance(rest["lines"], list)

    asyncio.run(_run())


def test_interrupt_resolve_roundtrip(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()

    eng = Engine()
    wf = Workflow(key="api_interrupt")

    def n1(ctx: NodeContext):
        (ctx.node_workdir / "done").write_text("ok", encoding="utf-8")

    def n2(ctx: NodeContext):
        if ctx.human_input is not None:
            (ctx.node_workdir / "h.txt").write_text(ctx.human_input["text"], encoding="utf-8")
            return
        interrupt(
            expected_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        )

    wf.add_node("a", n1, whitelist=["done"])
    wf.add_node("b", n2, whitelist=["h.txt"])

    eng.register_workflow(wf)

    tasks_root = tmp_path / "runs"
    app = create_app(
        eng,
        store,
        tasks_root,
        spawn_worker_fn=_sync_spawn(eng, store),
    )

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "api_interrupt", "input": {}})
            task_id = r.json()["task_id"]

            detail = await client.get(f"/tasks/{task_id}")
            assert detail.status_code == 200
            body = detail.json()
            assert body["status"] == S.TASK_WAITING_HUMAN
            assert "interrupt" in body
            assert body["interrupt"]["node_id"] == "b"
            seq = body["interrupt"]["seq"]

            bad = await client.post(
                f"/tasks/{task_id}/interrupt/resolve",
                json={"interrupt_seq": seq + 99, "payload": {"text": "no"}},
            )
            assert bad.status_code == 409

            bad_payload = await client.post(
                f"/tasks/{task_id}/interrupt/resolve",
                json={"payload": {"text": 123}},
            )
            assert bad_payload.status_code == 422

            ok = await client.post(
                f"/tasks/{task_id}/interrupt/resolve",
                json={"interrupt_seq": seq, "payload": {"text": "hi"}},
            )
            assert ok.status_code == 202

            done = await client.get(f"/tasks/{task_id}")
            assert done.json()["status"] == S.TASK_SUCCEEDED

    asyncio.run(_run())


def test_get_task_404_returns_top_level_error(api_setup):
    app = api_setup["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/tasks/does-not-exist-uuid")
            assert r.status_code == 404
            body = r.json()
            assert "error" in body
            assert body["error"]["code"] == "not_found"

    asyncio.run(_run())

"""Integration tests for POST /tasks/{id}/rerun."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from wf_engine import status as S
from wf_engine.context import NodeContext
from wf_engine.engine import Engine
from wf_engine.interrupt import interrupt
from wf_engine.lease_util import utc_iso_after
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
def three_node_setup(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()

    eng = Engine()
    wf = Workflow(key="rerun_wf")

    def n1(ctx):
        (ctx.node_workdir / "s1.txt").write_text("one", encoding="utf-8")

    def n2(ctx):
        (ctx.node_workdir / "s2.txt").write_text("two", encoding="utf-8")

    def n3(ctx):
        (ctx.node_workdir / "s3.txt").write_text("three", encoding="utf-8")

    wf.add_node("a", n1, whitelist=["s1.txt"])
    wf.add_node("b", n2, whitelist=["s2.txt"])
    wf.add_node("c", n3, whitelist=["s3.txt"])
    eng.register_workflow(wf)

    tasks_root = tmp_path / "runs"
    app = create_app(eng, store, tasks_root, spawn_worker_fn=_sync_spawn(eng, store))
    return {"app": app, "store": store, "tasks_root": tasks_root}


def test_rerun_from_middle_restores_workspace_and_completes(three_node_setup):
    app = three_node_setup["app"]
    tasks_root: Path = three_node_setup["tasks_root"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "rerun_wf", "input": {}})
            assert r.status_code == 201
            task_id = r.json()["task_id"]
            tr = tasks_root / task_id
            ws = task_layout(tr).workspace

            d0 = await client.get(f"/tasks/{task_id}")
            assert d0.json()["status"] == S.TASK_SUCCEEDED
            gen_before = d0.json()["worker_generation"]

            assert (ws / "s1.txt").read_text(encoding="utf-8") == "one"
            assert (ws / "s2.txt").read_text(encoding="utf-8") == "two"
            _clear_workspace_for_test(ws)

            rr = await client.post(f"/tasks/{task_id}/rerun", json={"from_node_id": "b"})
            assert rr.status_code == 202

            d1 = await client.get(f"/tasks/{task_id}")
            body = d1.json()
            assert body["status"] == S.TASK_SUCCEEDED
            assert body["worker_generation"] == gen_before + 1
            assert (ws / "s1.txt").read_text(encoding="utf-8") == "one"
            assert (ws / "s2.txt").read_text(encoding="utf-8") == "two"
            assert (ws / "s3.txt").read_text(encoding="utf-8") == "three"
            for n in body["nodes"]:
                assert n["status"] == S.NODE_SUCCESS

    asyncio.run(_run())


def _clear_workspace_for_test(ws: Path) -> None:
    for p in ws.iterdir():
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)


@pytest.fixture
def nested_workdir_setup(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()

    eng = Engine()
    wf = Workflow(key="nested_rerun")

    def n1(ctx: NodeContext):
        (ctx.node_workdir / "out.txt").write_text("from-n1", encoding="utf-8")

    def n2(ctx: NodeContext):
        upstream = ctx.workspace / "n1" / "out.txt"
        merged = upstream.read_text(encoding="utf-8") + "-n2"
        (ctx.node_workdir / "s2.txt").write_text(merged, encoding="utf-8")

    wf.add_node("n1", n1, workdir="n1", whitelist=["out.txt"])
    wf.add_node("n2", n2, workdir="n2", whitelist=["s2.txt"])
    eng.register_workflow(wf)

    tasks_root = tmp_path / "runs"
    app = create_app(eng, store, tasks_root, spawn_worker_fn=_sync_spawn(eng, store))
    return {"app": app, "tasks_root": tasks_root}


def test_rerun_restores_subdir_node_snapshots(nested_workdir_setup):
    """Snapshots are rooted at node workdir; rerun unpacks under workspace/n1/ etc."""
    app = nested_workdir_setup["app"]
    tasks_root: Path = nested_workdir_setup["tasks_root"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "nested_rerun", "input": {}})
            assert r.status_code == 201
            task_id = r.json()["task_id"]
            ws = task_layout(tasks_root / task_id).workspace

            d0 = await client.get(f"/tasks/{task_id}")
            assert d0.json()["status"] == S.TASK_SUCCEEDED
            assert (ws / "n1" / "out.txt").read_text(encoding="utf-8") == "from-n1"
            assert (ws / "n2" / "s2.txt").read_text(encoding="utf-8") == "from-n1-n2"

            _clear_workspace_for_test(ws)

            rr = await client.post(f"/tasks/{task_id}/rerun", json={"from_node_id": "n2"})
            assert rr.status_code == 202

            done = await client.get(f"/tasks/{task_id}")
            assert done.json()["status"] == S.TASK_SUCCEEDED
            assert (ws / "n1" / "out.txt").read_text(encoding="utf-8") == "from-n1"
            assert (ws / "n2" / "s2.txt").read_text(encoding="utf-8") == "from-n1-n2"

    asyncio.run(_run())


def test_rerun_missing_snapshot_returns_409(three_node_setup):
    app = three_node_setup["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "rerun_wf", "input": {}})
            task_id = r.json()["task_id"]
            detail = await client.get(f"/tasks/{task_id}")
            assert detail.json()["status"] == S.TASK_SUCCEEDED

            nodes = detail.json()["nodes"]
            zip0 = nodes[0]["zip_path"]
            assert zip0
            Path(zip0).unlink()

            resp = await client.post(f"/tasks/{task_id}/rerun", json={"from_node_id": "b"})
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "missing_snapshot"

    asyncio.run(_run())


def test_rerun_rejected_when_waiting_human(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    eng = Engine()
    wf = Workflow(key="rh")

    def n1(ctx: NodeContext):
        (ctx.node_workdir / "done").write_text("ok", encoding="utf-8")

    def n2(ctx: NodeContext):
        interrupt(expected_schema=None)

    wf.add_node("a", n1, whitelist=["done"])
    wf.add_node("b", n2, whitelist=["x.txt"])
    eng.register_workflow(wf)
    tasks_root = tmp_path / "runs"
    app = create_app(eng, store, tasks_root, spawn_worker_fn=_sync_spawn(eng, store))

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "rh", "input": {}})
            task_id = r.json()["task_id"]
            d = await client.get(f"/tasks/{task_id}")
            assert d.json()["status"] == S.TASK_WAITING_HUMAN
            resp = await client.post(f"/tasks/{task_id}/rerun", json={"from_node_id": "b"})
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "invalid_task_status"

    asyncio.run(_run())


def test_rerun_rejected_when_worker_lease_active(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    eng = Engine()
    wf = Workflow(key="busy_wf")

    def n1(ctx: NodeContext):
        (ctx.node_workdir / "keep.txt").write_text("x", encoding="utf-8")

    wf.add_node("a", n1, whitelist=["keep.txt"])
    eng.register_workflow(wf)

    tid = store.create_task(
        workflow_key="busy_wf",
        workflow_revision="1",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    store.init_task_nodes(tid, ["a"])
    store.set_task_status(tid, S.TASK_RUNNING)
    store.acquire_lease(tid, os.getpid(), utc_iso_after(seconds=3600))

    tasks_root = tmp_path / "runs"
    app = create_app(eng, store, tasks_root, spawn_worker_fn=_sync_spawn(eng, store))

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/tasks/{tid}/rerun", json={"from_node_id": "a"})
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "worker_busy"

    asyncio.run(_run())


def test_rerun_after_node_failure_completes(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    eng = Engine()
    wf = Workflow(key="fail_once")

    calls = {"n2": 0}

    def n1(ctx: NodeContext):
        (ctx.node_workdir / "s1.txt").write_text("one", encoding="utf-8")

    def n2(ctx: NodeContext):
        calls["n2"] += 1
        if calls["n2"] < 2:
            raise RuntimeError("first run fails")
        (ctx.node_workdir / "s2.txt").write_text("two", encoding="utf-8")

    wf.add_node("a", n1, whitelist=["s1.txt"])
    wf.add_node("b", n2, whitelist=["s2.txt"])
    eng.register_workflow(wf)

    tasks_root = tmp_path / "runs"
    app = create_app(eng, store, tasks_root, spawn_worker_fn=_sync_spawn(eng, store))

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/tasks", json={"workflow_key": "fail_once", "input": {}})
            task_id = r.json()["task_id"]
            d = await client.get(f"/tasks/{task_id}")
            assert d.json()["status"] == S.TASK_FAILED

            rr = await client.post(f"/tasks/{task_id}/rerun", json={"from_node_id": "b"})
            assert rr.status_code == 202

            done = await client.get(f"/tasks/{task_id}")
            assert done.json()["status"] == S.TASK_SUCCEEDED

    asyncio.run(_run())

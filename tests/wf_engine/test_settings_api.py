"""Settings HTTP API integration tests (/settings, /settings/ops, /settings/system)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from wf_engine.engine import Engine
from wf_engine.server.app import create_app
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow


@pytest.fixture
def settings_api_app(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()

    eng = Engine()
    wf = Workflow(key="noop")

    def n1(ctx):
        pass

    wf.add_node("s", n1)
    eng.register_workflow(wf)

    app = create_app(eng, store, tmp_path / "runs", registry_module=None)
    return {"app": app, "store": store}


def test_settings_ops_get_default(settings_api_app):
    app = settings_api_app["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/settings/ops")
            assert r.status_code == 200
            assert r.json() == {"cookie": "", "authorization": ""}

    asyncio.run(_run())


def test_settings_bundle_and_system(settings_api_app):
    app = settings_api_app["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/settings")
            assert r.status_code == 200
            body = r.json()
            assert body["ops"] == {"cookie": "", "authorization": ""}
            assert "tasks_root" in body["system"]
            assert "server_tasks_root_default" in body["system"]
            assert "tasks_root_effective" in body["system"]

            r2 = await client.get("/settings/system")
            assert r2.status_code == 200
            assert r2.json() == body["system"]

    asyncio.run(_run())


def test_settings_system_put_creates_dir(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    eng = Engine()
    wf = Workflow(key="noop")

    def n1(ctx):
        pass

    wf.add_node("s", n1)
    eng.register_workflow(wf)
    app = create_app(eng, store, tmp_path / "runs_default", registry_module=None)
    custom = tmp_path / "nested" / "custom_tasks"

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.put("/settings/system", json={"tasks_root": str(custom)})
            assert r.status_code == 200
            j = r.json()
            assert Path(j["tasks_root"]).resolve() == custom.resolve()
            assert j["tasks_root_effective"] == j["tasks_root"]
            assert custom.is_dir()

    asyncio.run(_run())


def test_settings_ops_put_roundtrip(settings_api_app):
    app = settings_api_app["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.put(
                "/settings/ops",
                json={"cookie": "sid=1", "authorization": "Bearer z"},
            )
            assert r.status_code == 200
            assert r.json() == {"cookie": "sid=1", "authorization": "Bearer z"}

            r2 = await client.get("/settings/ops")
            assert r2.status_code == 200
            assert r2.json() == {"cookie": "sid=1", "authorization": "Bearer z"}

    asyncio.run(_run())

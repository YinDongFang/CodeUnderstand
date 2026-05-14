"""Settings HTTP API: GET/PUT /settings (flat console settings)."""

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

    app = create_app(eng, store, tmp_path / "runs", )
    return {"app": app, "store": store}


def test_settings_get_default(settings_api_app):
    app = settings_api_app["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/settings")
            assert r.status_code == 200
            j = r.json()
            assert j["cookie"] == ""
            assert j["authorization"] == ""
            assert j == {
                "root": ".wf_engine/tasks",
                "cookie": "",
                "authorization": "",
            }

    asyncio.run(_run())


def test_settings_put_roundtrip(settings_api_app):
    app = settings_api_app["app"]

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.put(
                "/settings",
                json={
                    "root": ".wf_engine/tasks",
                    "cookie": "sid=1",
                    "authorization": "Bearer z",
                },
            )
            assert r.status_code == 200
            assert r.json()["cookie"] == "sid=1"
            assert r.json()["authorization"] == "Bearer z"

            r2 = await client.get("/settings")
            assert r2.status_code == 200
            assert r2.json()["cookie"] == "sid=1"

    asyncio.run(_run())


def test_settings_put_relative_root_creates_home_relative_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    eng = Engine()
    wf = Workflow(key="noop")

    def n1(ctx):
        pass

    wf.add_node("s", n1)
    eng.register_workflow(wf)
    app = create_app(eng, store, tmp_path / "runs_default", )
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    custom_relative = "nested/custom_tasks"
    custom_abs = home / custom_relative

    async def _run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.put(
                "/settings",
                json={
                    "root": custom_relative,
                    "cookie": "",
                    "authorization": "",
                },
            )
            assert r.status_code == 200
            j = r.json()
            assert j == {
                "root": custom_relative,
                "cookie": "",
                "authorization": "",
            }
            assert custom_abs.is_dir()

    asyncio.run(_run())

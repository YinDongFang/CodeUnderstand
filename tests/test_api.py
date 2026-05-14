"""FastAPI 路由测试 — TestClient（基于 httpx）。"""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import cu.events as eve
from cu import orchestrator as orch
from cu.api import create_app
from cu.models import JOB_STAGES


URL = "https://github.com/u/demo-repo/archive/refs/heads/main.zip"


def _fake_runner(ctx):
    from cu.paths import sandbox_home
    os.makedirs(sandbox_home(ctx.job_id), exist_ok=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "cu"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".claude").mkdir()
    with orch._ACTIVE_LOCK:
        orch._ACTIVE.clear()
    with eve._lock:
        eve._Subscribers.clear()
    monkeypatch.setenv("CU_JOB_WORKER_INLINE", "1")
    return TestClient(create_app())


def test_create_and_get_job(client):
    r = client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j1"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job_id"] == "api-j1"
    assert body["repo"] == "demo-repo"
    assert body["status"] == "pending"
    assert len(body["stages"]) == 4
    assert [s["stage"] for s in body["stages"]] == list(JOB_STAGES)

    r2 = client.get("/api/v1/jobs/api-j1")
    assert r2.status_code == 200
    assert r2.json()["job_id"] == "api-j1"


def test_get_nonexistent_job_404(client):
    r = client.get("/api/v1/jobs/ghost")
    assert r.status_code == 404


def test_create_invalid_url_400(client):
    r = client.post("/api/v1/jobs", json={"zip_url": "not-a-url"})
    assert r.status_code == 400


def test_list_jobs(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j2"})
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j3"})
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200
    items = r.json()
    ids = {j["job_id"] for j in items}
    assert {"api-j2", "api-j3"} <= ids


def test_list_filter_by_status(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-pend"})
    r = client.get("/api/v1/jobs?status=pending")
    assert r.status_code == 200
    assert any(j["job_id"] == "api-pend" for j in r.json())
    r2 = client.get("/api/v1/jobs?status=success")
    assert all(j["job_id"] != "api-pend" for j in r2.json())


def test_run_stage_dependency_409(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-dep"})
    r = client.post("/api/v1/jobs/api-j-dep/stages/conversation/run")
    assert r.status_code == 409


def test_run_stage_unknown_job_404(client):
    r = client.post("/api/v1/jobs/ghost/stages/bootstrap/run")
    assert r.status_code == 404


def test_run_stage_invalid_stage_400(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-bad"})
    r = client.post("/api/v1/jobs/api-j-bad/stages/nonsense/run")
    assert r.status_code == 400


def test_delete_job(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-del"})
    r = client.delete("/api/v1/jobs/api-j-del")
    assert r.status_code == 204
    r2 = client.get("/api/v1/jobs/api-j-del")
    assert r2.status_code == 404


def test_delete_unknown_job_404(client):
    r = client.delete("/api/v1/jobs/ghost")
    assert r.status_code == 404


def test_cancel_no_active_404(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-cncl"})
    r = client.post("/api/v1/jobs/api-j-cncl/cancel")
    assert r.status_code == 404


def test_run_stage_and_show_completion(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-run"})
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_runner for s in JOB_STAGES},
        clear=False,
    ):
        r = client.post("/api/v1/jobs/api-j-run/stages/bootstrap/run")
        assert r.status_code == 200
        with orch._ACTIVE_LOCK:
            t = orch._ACTIVE["api-j-run"].thread
        t.join(timeout=5)

    detail = client.get("/api/v1/jobs/api-j-run").json()
    assert detail["status"] == "success"
    statuses = {s["stage"]: s["status"] for s in detail["stages"]}
    assert statuses["bootstrap"] == "success"
    assert statuses["conversation"] == "success"
    assert statuses["compile"] == "success"
    assert statuses["build"] == "success"


def test_rerun_missing_snapshot_409(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-noSnap"})
    # Fake bootstrap success without an actual snapshot tar
    orch._update_stage("api-j-noSnap", "bootstrap", status="success")
    r = client.post("/api/v1/jobs/api-j-noSnap/stages/conversation/rerun")
    assert r.status_code == 409


def test_meta(client):
    r = client.get("/api/v1/meta")
    assert r.status_code == 200
    j = r.json()
    assert j["version"] == "0.3.0"
    assert isinstance(j["web_ui"], bool)


def test_job_dto_has_artifact_zip_field(client):
    r = client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-zipfld"})
    assert r.status_code == 201
    assert "artifact_zip_path" in r.json()


def test_rewrite_questions_409_until_compile_success(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j-rwq"})
    r = client.get("/api/v1/jobs/api-j-rwq/rewrite/questions")
    assert r.status_code == 409


def test_job_events_sse_404_for_unknown_job(client):
    assert client.get("/api/v1/jobs/ghost-job/events").status_code == 404


def test_root_serves_bundle_when_present(client):
    r = client.get("/")
    if r.status_code == 200:
        assert b"CodeUnderstand" in r.content

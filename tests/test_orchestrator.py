"""Orchestrator 集成测试：mock STAGE_RUNNERS，验证状态流转、快照、重跑、删除。"""
import os
from unittest.mock import patch

import pytest

from cu import orchestrator as orch
from cu.models import JOB_STAGES


URL = "https://github.com/u/demo-repo/archive/refs/heads/main.zip"


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "cu"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".claude").mkdir()
    orch.ensure_db()
    monkeypatch.setenv("CU_JOB_WORKER_INLINE", "1")
    with orch._ACTIVE_LOCK:
        orch._ACTIVE.clear()
    return tmp_path


def _fake_stage_runner(ctx):
    """伪 stage：仅确保 sandbox home 存在以满足 save_snapshot。"""
    from cu.paths import sandbox_home
    home = sandbox_home(ctx.job_id)
    os.makedirs(home, exist_ok=True)


def test_create_job_inserts_records(db_env):
    rec = orch.create_job(URL, job_id="j1")
    assert rec.job_id == "j1"
    assert rec.repo == "demo-repo"
    assert rec.status == "pending"
    stages = orch.get_stages("j1")
    assert set(stages.keys()) == set(JOB_STAGES)
    for s in JOB_STAGES:
        assert stages[s].status == "pending"
        assert stages[s].attempt == 0


def test_create_job_invalid_url(db_env):
    with pytest.raises(ValueError):
        orch.create_job("not-a-url")


def test_run_all_stages_success(db_env):
    orch.create_job(URL, job_id="j2")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        t = orch.run_stage("j2", "bootstrap")
        t.join(timeout=5)
    job = orch.get_job("j2")
    assert job.status == "success"
    stages = orch.get_stages("j2")
    for s in JOB_STAGES:
        assert stages[s].status == "success", f"{s} not success: {stages[s]}"
        assert stages[s].attempt == 1

    from cu.snapshot import snapshot_exists
    assert snapshot_exists("j2", "bootstrap")
    assert snapshot_exists("j2", "conversation")
    assert snapshot_exists("j2", "compile")
    assert not snapshot_exists("j2", "build")


def test_stage_failure_stops_chain(db_env):
    orch.create_job(URL, job_id="j3")

    def boom(ctx):
        raise RuntimeError("kaboom")

    runners = {s: _fake_stage_runner for s in JOB_STAGES}
    runners["conversation"] = boom
    with patch.dict(orch.STAGE_RUNNERS, runners, clear=False):
        t = orch.run_stage("j3", "bootstrap")
        t.join(timeout=5)
    job = orch.get_job("j3")
    assert job.status == "failed"
    stages = orch.get_stages("j3")
    assert stages["bootstrap"].status == "success"
    assert stages["conversation"].status == "failed"
    assert stages["compile"].status == "pending"
    assert stages["build"].status == "pending"
    assert "kaboom" in stages["conversation"].log_tail


def test_run_stage_dependency_violation_raises(db_env):
    orch.create_job(URL, job_id="j-dep")
    with pytest.raises(ValueError, match="dependencies not met"):
        orch.run_stage("j-dep", "conversation")


def test_run_stage_unknown_job_raises(db_env):
    with pytest.raises(KeyError):
        orch.run_stage("ghost", "bootstrap")


def test_rerun_restores_snapshot(db_env):
    orch.create_job(URL, job_id="j4")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.run_stage("j4", "bootstrap").join(timeout=5)

    from cu.paths import sandbox_home
    home = sandbox_home("j4")
    dirty = os.path.join(home, "dirty.txt")
    with open(dirty, "w") as f:
        f.write("noise")

    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.rerun_from("j4", "compile").join(timeout=5)

    assert not os.path.exists(dirty), "rerun_from compile should restore home from post-conversation"
    stages = orch.get_stages("j4")
    assert stages["bootstrap"].attempt == 1, "bootstrap not re-run"
    assert stages["conversation"].attempt == 1, "conversation not re-run"
    assert stages["compile"].attempt == 2, "compile should be re-run"
    assert stages["build"].attempt == 2, "build should be re-run"


def test_rerun_bootstrap_wipes_sandbox(db_env):
    orch.create_job(URL, job_id="j-boot")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.run_stage("j-boot", "bootstrap").join(timeout=5)
    from cu.paths import sandbox_home, snapshots_dir
    assert os.path.isdir(sandbox_home("j-boot"))
    assert os.path.isdir(snapshots_dir("j-boot"))

    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.rerun_from("j-boot", "bootstrap").join(timeout=5)
    # sandbox should still exist (re-created by fake_stage_runner)
    # but the snapshots dir should have been wiped and rebuilt
    job = orch.get_job("j-boot")
    assert job.status == "success"


def test_rerun_missing_snapshot_raises(db_env):
    orch.create_job(URL, job_id="j-nosnap")
    # mark bootstrap success in DB but no actual snapshot file
    orch._update_stage("j-nosnap", "bootstrap", status="success")
    with pytest.raises(FileNotFoundError):
        orch.rerun_from("j-nosnap", "conversation")


def test_delete_job(db_env):
    orch.create_job(URL, job_id="j5")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.run_stage("j5", "bootstrap").join(timeout=5)
    from cu.paths import job_dir
    d = job_dir("j5")
    assert os.path.isdir(d)
    assert orch.delete_job("j5") is True
    assert not os.path.isdir(d)
    assert orch.get_job("j5") is None
    # stage rows should also be cascaded
    assert orch.get_stages("j5") == {}


def test_delete_unknown_job_returns_false(db_env):
    assert orch.delete_job("nope") is False


def test_list_jobs(db_env):
    orch.create_job(URL, job_id="a")
    orch.create_job(URL, job_id="b")
    rows = orch.list_jobs()
    ids = [r.job_id for r in rows]
    assert "a" in ids and "b" in ids
    # both should be pending
    rows_pending = orch.list_jobs(status="pending")
    assert len(rows_pending) == 2
    rows_success = orch.list_jobs(status="success")
    assert rows_success == []


def test_run_stage_twice_raises(db_env):
    """同一作业不可同时启动两次运行。"""
    orch.create_job(URL, job_id="dup")

    import threading
    barrier = threading.Event()

    def slow_runner(ctx):
        from cu.paths import sandbox_home
        os.makedirs(sandbox_home(ctx.job_id), exist_ok=True)
        barrier.wait(timeout=5)

    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: slow_runner for s in JOB_STAGES},
        clear=False,
    ):
        t = orch.run_stage("dup", "bootstrap")
        try:
            with pytest.raises(RuntimeError, match="已在运行"):
                orch.run_stage("dup", "bootstrap")
        finally:
            barrier.set()
            t.join(timeout=5)


def test_run_partial_bootstrap_only_job_pending(db_env):
    orch.create_job(URL, job_id="j-part")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.run_stage("j-part", "bootstrap", end_stage="bootstrap").join(timeout=5)
    job = orch.get_job("j-part")
    assert job.status == "pending"
    stages = orch.get_stages("j-part")
    assert stages["bootstrap"].status == "success"
    assert stages["conversation"].status == "pending"
    assert stages["compile"].status == "pending"
    assert stages["build"].status == "pending"
    from cu.snapshot import snapshot_exists
    assert snapshot_exists("j-part", "bootstrap")


def test_run_stage_end_before_start_raises(db_env):
    orch.create_job(URL, job_id="j-ord")
    with pytest.raises(ValueError, match="before start"):
        orch.run_stage("j-ord", "compile", end_stage="bootstrap")

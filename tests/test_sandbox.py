import os
from cu.sandbox import bootstrap_sandbox


def test_bootstrap_creates_dirs(tmp_path, monkeypatch):
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / ".claude").mkdir()
    (real_home / ".claude" / "settings.json").write_text('{"key":"val"}')
    (real_home / ".claude" / "projects").mkdir()
    (real_home / ".claude" / "projects" / "some-old-session").mkdir()

    monkeypatch.setenv("HOME", str(real_home))
    cu_root = tmp_path / "cu_data"
    monkeypatch.setenv("CU_DATA_ROOT", str(cu_root))

    job_id = "test-job-001"
    bootstrap_sandbox(job_id)

    home = cu_root / "jobs" / job_id / "home"
    assert home.is_dir()
    assert (home / ".claude").is_dir()
    assert (home / ".claude" / "settings.json").read_text() == '{"key":"val"}'
    assert not (home / ".claude" / "projects" / "some-old-session").exists()
    assert (home / ".claude" / "projects").is_dir()
    assert (cu_root / "jobs" / job_id / "snapshots").is_dir()


def test_bootstrap_idempotent(tmp_path, monkeypatch):
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / ".claude").mkdir()

    monkeypatch.setenv("HOME", str(real_home))
    cu_root = tmp_path / "cu_data"
    monkeypatch.setenv("CU_DATA_ROOT", str(cu_root))

    bootstrap_sandbox("job-x")
    marker = cu_root / "jobs" / "job-x" / "home" / "marker.txt"
    marker.write_text("keep")
    bootstrap_sandbox("job-x")
    assert not marker.exists(), "re-bootstrap should reset the sandbox"

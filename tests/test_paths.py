import os
from cu.paths import data_root, job_dir, sandbox_home, snapshots_dir, artifact_root


def test_data_root_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CU_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = data_root()
    assert result == str(tmp_path / ".code-understand")


def test_data_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "custom"))
    result = data_root()
    assert result == str(tmp_path / "custom")


def test_job_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = job_dir("abc123")
    assert result == str(tmp_path / "jobs" / "abc123")


def test_sandbox_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = sandbox_home("abc123")
    assert result == str(tmp_path / "jobs" / "abc123" / "home")


def test_snapshots_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = snapshots_dir("abc123")
    assert result == str(tmp_path / "jobs" / "abc123" / "snapshots")


def test_artifact_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = artifact_root("abc123", "react")
    assert result == str(tmp_path / "jobs" / "abc123" / "home" / "code-understand-react")

import os

import pytest

from cu.snapshot import (
    save_snapshot,
    restore_snapshot,
    snapshot_exists,
    delete_snapshots,
)


def test_save_and_restore_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    job_id = "snap-001"

    home = tmp_path / "jobs" / job_id / "home"
    home.mkdir(parents=True)
    (home / "file.txt").write_text("hello")
    (home / "sub").mkdir()
    (home / "sub" / "data.bin").write_bytes(b"\x00\x01\x02")

    save_snapshot(job_id, "bootstrap")
    assert snapshot_exists(job_id, "bootstrap")

    snap = tmp_path / "jobs" / job_id / "snapshots" / "post-bootstrap.tar"
    assert snap.is_file() and snap.stat().st_size > 0

    (home / "file.txt").write_text("changed")
    (home / "new.txt").write_text("extra")

    restore_snapshot(job_id, "bootstrap")
    assert (home / "file.txt").read_text() == "hello"
    assert (home / "sub" / "data.bin").read_bytes() == b"\x00\x01\x02"
    assert not (home / "new.txt").exists()


def test_save_snapshot_missing_home_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        save_snapshot("nope", "bootstrap")


def test_restore_missing_snapshot_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    (tmp_path / "jobs" / "j" / "home").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        restore_snapshot("j", "bootstrap")


def test_snapshot_exists_false_initially(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    assert snapshot_exists("nope", "bootstrap") is False


def test_delete_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    job_id = "snap-002"
    home = tmp_path / "jobs" / job_id / "home"
    home.mkdir(parents=True)
    (home / "f").write_text("x")
    save_snapshot(job_id, "bootstrap")
    save_snapshot(job_id, "conversation")
    snap_dir = tmp_path / "jobs" / job_id / "snapshots"
    assert snap_dir.is_dir()
    delete_snapshots(job_id)
    assert not snap_dir.exists()
    assert snapshot_exists(job_id, "bootstrap") is False

"""cu.pipeline.cleanup_outputs"""
from __future__ import annotations

from pathlib import Path

from cu.pipeline.cleanup_outputs import run_cleanup


def test_cleanup_zip_only(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = tmp_path / "outputs"
    monkeypatch.setenv("OUTPUTS_DIR", str(out))
    z = out / "code-understand-demo.zip"
    out.mkdir(parents=True)
    z.write_bytes(b"z")
    run_cleanup("demo", ["zip"])
    assert not z.is_file()


def test_cleanup_output_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = tmp_path / "outputs"
    monkeypatch.setenv("OUTPUTS_DIR", str(out))
    d = out / "code-understand-demo"
    d.mkdir(parents=True)
    run_cleanup("demo", ["output"])
    assert not d.is_dir()

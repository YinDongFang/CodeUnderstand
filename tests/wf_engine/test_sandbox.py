"""Tests for workspace sandbox helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from wf_engine.utils.sandbox import resolve_node_workdir


def test_resolve_rejects_double_dot_segments() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        with pytest.raises(ValueError, match=r"\.\."):
            resolve_node_workdir(ws, "..")


def test_resolve_accepts_subdir() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        got = resolve_node_workdir(ws, "sub")
        assert got == (ws / "sub").resolve()

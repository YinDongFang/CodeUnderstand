"""cu.pipeline.classify（mock Claude）。"""
from __future__ import annotations

import os
from pathlib import Path

from cu.pipeline.classify import classify_main_type

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_classify_mock_returns_learning_tutorial(tmp_path, monkeypatch):
    monkeypatch.setenv("CLASSIFY_USE_MOCK_CLAUDE", "1")
    root = tmp_path / "code"
    root.mkdir()
    (root / "README.md").write_text("# Hi\n", encoding="utf-8")
    mt = classify_main_type(
        str(root),
        "https://github.com/o/r",
        env=os.environ.copy(),
        repo_root=str(REPO_ROOT),
    )
    assert mt == "Learning/Tutorial"

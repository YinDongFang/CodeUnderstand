"""cu.pipeline.evaluate"""
from __future__ import annotations

import pytest

from cu.pipeline.evaluate import evaluate_repo_difficulty


def test_evaluate_empty_tree_easy(tmp_path):
    """0 行 0 文件：<16000 且 <45 → easy。"""
    root = tmp_path / "proj"
    root.mkdir()
    assert evaluate_repo_difficulty(str(root)) == "easy"


def test_evaluate_small_repo_easy(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("# x\n", encoding="utf-8")
    assert evaluate_repo_difficulty(str(root)) == "easy"


def test_evaluate_skips_node_modules(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    nm = root / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("x\n" * 1000, encoding="utf-8")
    (root / "real.py").write_text("# ok\n", encoding="utf-8")
    assert evaluate_repo_difficulty(str(root)) == "easy"


def test_evaluate_medium_band(tmp_path):
    """命中中等区间：16000≤lines≤50000 且 45≤files≤450。"""
    root = tmp_path / "proj"
    root.mkdir()
    line_chunk = "# line\n"
    body = line_chunk * 400  # 400 lines per file
    for i in range(45):
        (root / f"f{i}.py").write_text(body, encoding="utf-8")
    assert evaluate_repo_difficulty(str(root)) == "medium"


def test_evaluate_difficult_threshold(tmp_path):
    """LOC > 160000 且 files > 450。"""
    root = tmp_path / "proj"
    root.mkdir()
    line_chunk = "# line\n"
    body = line_chunk * 400
    for i in range(451):
        (root / f"f{i}.py").write_text(body, encoding="utf-8")
    assert evaluate_repo_difficulty(str(root)) == "difficult"


def test_evaluate_missing_dir_raises(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError):
        evaluate_repo_difficulty(str(missing))

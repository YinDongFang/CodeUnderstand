"""从 session JSONL 抽取 / 写回单行用户提问（与 rewrite.clean 规则一致）。"""
from __future__ import annotations

import json
from typing import Any

# 仓库根 rewrite.py（与 cu 并行）
import importlib.util
import pathlib


def _rewrite_module():
    repo_root = pathlib.Path(__file__).resolve().parents[1] / "rewrite.py"
    spec = importlib.util.spec_from_file_location("rewrite_mod", repo_root)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_rw = None


def _rw_mod():
    global _rw
    if _rw is None:
        _rw = _rewrite_module()
    return _rw


def extract_question_lines(session_jsonl_path: str) -> list[str]:
    rw = _rw_mod()
    with open(session_jsonl_path, "r", encoding="utf-8") as f:
        raw = f.readlines()
    objects: list[Any | None] = rw._load_jsonl_objects(raw)  # noqa: SLF001
    return rw.collect_user_questions(objects)


def apply_question_lines(session_jsonl_path: str, lines: list[str]) -> None:
    """就地写回；条数必须与当前会话中抽取到的真实单行提问一致。"""
    rw = _rw_mod()
    with open(session_jsonl_path, "r", encoding="utf-8") as f:
        raw = f.readlines()
    objects = rw._load_jsonl_objects(raw)  # noqa: SLF001
    old_lines = rw.collect_user_questions(objects)
    if len(lines) != len(old_lines):
        raise ValueError(
            f"题目条数不匹配：会话中可编辑条目 {len(old_lines)}，请求 {len(lines)}"
        )
    source_to_new: dict[str, str] = {
        old: new for old, new in zip(old_lines, lines) if new != old
    }
    if not source_to_new:
        return
    rw.write_jsonl_with_text_mapping(session_jsonl_path, raw, source_to_new)

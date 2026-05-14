"""从 session JSONL 抽取 / 写回单行用户提问（与 ``cu.pipeline.rewrite`` 规则一致）。"""
from __future__ import annotations

from typing import Any

from cu.pipeline import rewrite as rw
from cu.session_jsonl_io import load_jsonl_objects


def extract_question_lines(session_jsonl_path: str) -> list[str]:
    with open(session_jsonl_path, "r", encoding="utf-8") as f:
        raw = f.readlines()
    objects: list[Any | None] = load_jsonl_objects(raw)
    return rw.collect_user_questions(objects)


def apply_question_lines(session_jsonl_path: str, lines: list[str]) -> None:
    """就地写回；条数必须与当前会话中抽取到的真实单行提问一致。"""
    with open(session_jsonl_path, "r", encoding="utf-8") as f:
        raw = f.readlines()
    objects = load_jsonl_objects(raw)
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

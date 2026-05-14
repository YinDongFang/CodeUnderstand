"""JSONL 会话文件的通用解析（供 ``cu.pipeline.rewrite`` / ``session_questions`` 等共用）。"""
from __future__ import annotations

import json
from typing import Any


def load_jsonl_objects(raw_lines: list[str]) -> list[Any | None]:
    """与 ``raw_lines`` 下标一一对应（含空行、坏 JSON），用于会话遍历。"""
    out: list[Any | None] = []
    for line in raw_lines:
        s = line.strip()
        if not s:
            out.append(None)
            continue
        try:
            out.append(json.loads(s))
        except json.JSONDecodeError:
            out.append(None)
    return out

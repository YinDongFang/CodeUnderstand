"""cu.pipeline.build_docs 单元测试。"""
from __future__ import annotations

import json

from cu.pipeline.build_docs import normalize_session_jsonl


def test_normalize_session_jsonl_sets_message_model(tmp_path):
    p = tmp_path / "session.jsonl"
    row = {"type": "user", "message": {"model": "fancy-v1", "content": "hi"}}
    p.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    changed = normalize_session_jsonl(p)
    assert changed == 1
    out = json.loads(p.read_text(encoding="utf-8").strip())
    assert out["message"]["model"] == "model"


def test_normalize_nested_data_message_model(tmp_path):
    p = tmp_path / "session.jsonl"
    row = {
        "data": {
            "message": {
                "message": {"model": "x", "foo": 1},
            }
        }
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    changed = normalize_session_jsonl(p)
    assert changed == 1
    out = json.loads(p.read_text(encoding="utf-8").strip())
    assert out["data"]["message"]["message"]["model"] == "model"

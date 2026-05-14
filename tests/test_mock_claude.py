"""cu.testing.mock_claude CLI 冒烟。"""
from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import redirect_stdout

from cu.testing import mock_claude as mc


def test_mock_classify_returns_json():
    q = """You are an expert software analyst.
Classify the project into exactly one category.
Return ONLY a single JSON object with exactly one key: main_type (string).
Example JSON: {\"main_type\":\"Framework/Tools\"}"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mc.main(["-p", "--model", "x", "--effort", "y", q])
    assert rc == 0
    obj = json.loads(buf.getvalue().strip())
    assert obj["main_type"] == "Learning/Tutorial"


def test_mock_build_doc_exit_zero():
    q = (
        "Generate exactly 4 Markdown documents: overview.md — "
        "IMPORTANT: Write all doc/ output files to this absolute path:"
    )
    assert mc.main(["-p", q]) == 0


def test_mock_json_prompt_entry():
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".seq") as tf:
        tf.write("0\n")
        seq_path = tf.name
    os.environ["LOOP_MOCK_SEQ_FILE"] = seq_path
    try:
        prompt = "整体架构 foo 只返回最终版本的英文 bar"
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mc.main(["--output-format", "json", "-p", prompt])
        assert rc == 0
        doc = json.loads(buf.getvalue())
        assert "session_id" in doc and "result" in doc
        assert "ENTRY-Q-" in doc["result"]
    finally:
        os.environ.pop("LOOP_MOCK_SEQ_FILE", None)
        os.unlink(seq_path)

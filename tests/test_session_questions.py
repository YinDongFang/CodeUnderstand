"""session_questions 纯函数。"""
from __future__ import annotations

import json

from cu.session_questions import apply_question_lines, extract_question_lines


def test_extract_and_apply_roundtrip(tmp_path):
    fp = tmp_path / "sess.jsonl"
    q1 = "first question?"
    q2 = "second one"
    fp.write_text(
        "\n".join(
            json.dumps({"type": "user", "message": {"content": q}})
            for q in (q1, q2)
        )
        + "\n",
        encoding="utf-8",
    )

    xs = extract_question_lines(str(fp))
    assert xs == [q1, q2]

    new2 = "changed second"
    apply_question_lines(str(fp), [q1, new2])

    ys = extract_question_lines(str(fp))
    assert ys == [q1, new2]
    assert q2 not in fp.read_text()

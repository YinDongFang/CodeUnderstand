"""cu.pipeline.loop_render"""
from __future__ import annotations

from pathlib import Path

from cu.pipeline.loop_render import render_prompt_template


def test_render_prompt_template_replaces_keys(tmp_path):
    tpl = tmp_path / "t.md"
    tpl.write_text("H:{question}:{views}", encoding="utf-8")
    q = tmp_path / "q.txt"
    q.write_text("Q?\n", encoding="utf-8")
    v = tmp_path / "v.txt"
    v.write_text("V1", encoding="utf-8")
    out = tmp_path / "o.md"
    render_prompt_template(tpl, out, {"question": q, "views": v})
    assert out.read_text(encoding="utf-8") == "H:Q?\n:V1"

#!/usr/bin/env python3
"""Render prompt templates with multi-line replacements.

Usage:
  loop_render_prompt.py <template.md> <output.md> key=path [key=path ...]

Each ``key=path`` is one argv token; value is read from ``path``. Placeholders
``{key}`` in the template are replaced (UTF-8).
"""
from __future__ import annotations

import pathlib
import sys


def main() -> int:
    if len(sys.argv) < 4:
        sys.stderr.write(
            "usage: loop_render_prompt.py template.md out.md key=path [key=path ...]\n"
        )
        return 2
    template_path = pathlib.Path(sys.argv[1])
    out_path = pathlib.Path(sys.argv[2])
    pairs = sys.argv[3:]
    repl: dict[str, str] = {}
    for spec in pairs:
        if "=" not in spec:
            sys.stderr.write(f"bad pair (need key=path): {spec!r}\n")
            return 2
        key, pth = spec.split("=", 1)
        key = key.strip()
        repl[key] = pathlib.Path(pth).read_text(encoding="utf-8")
    text = template_path.read_text(encoding="utf-8")
    for key, val in repl.items():
        text = text.replace("{" + key + "}", val)
    out_path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Claude Code Stop hook for the Reader pane: after each assistant reply, inject the
message into the Learner pane via tmux-bridge.

Configure in the Reader project .claude/settings.json:
  "Stop": [{ "type": "command", "command": "python3 <absolute-path>/reader_hook.py" }]

每次触发时将 stdin 原文追加写入 ~/CodeUnderstand/test/reader_hook_input.txt（不做解析）。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from relay_stop import relay_stop_forward

_LOG_DIR = Path("~/CodeUnderstand/test").expanduser()
_LOG_FILE = _LOG_DIR / "reader_hook_input.txt"


def _append_reader_hook_log(raw: str) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(raw)
        if raw and not raw.endswith("\n"):
            f.write("\n")


if __name__ == "__main__":
    raw = sys.stdin.read()
    _append_reader_hook_log(raw)
    sys.stdin = io.StringIO(raw)
    sys.exit(relay_stop_forward("learner"))

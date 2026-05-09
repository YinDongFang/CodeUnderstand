#!/usr/bin/env python3
"""
Shared Stop-hook relay: read Claude Code JSON from stdin, forward last_assistant_message
to the opposite tmux pane via tmux-bridge (smux read → type → read → keys Enter).
"""

import json
import subprocess
import sys
from pathlib import Path


def _bridge(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux-bridge", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def forward_last_assistant_to_pane(dest_label: str, *, read_lines: str = "30") -> int:
    """
    dest_label: tmux-bridge pane label (e.g. 'reader' or 'learner').
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[relay_stop] stdin JSON parse error: {e}", file=sys.stderr)
        return 1

    text = data.get("last_assistant_message")
    if not text or not str(text).strip():
        return 0

    text = str(text)

    r = _bridge(["read", dest_label, read_lines])
    if r.returncode != 0:
        print(
            f"[relay_stop] tmux-bridge read {dest_label} failed: {r.stderr or r.stdout}",
            file=sys.stderr,
        )
        return r.returncode

    r = _bridge(["type", dest_label, text])
    if r.returncode != 0:
        print(
            f"[relay_stop] tmux-bridge type {dest_label} failed: {r.stderr or r.stdout}",
            file=sys.stderr,
        )
        return r.returncode

    r = _bridge(["read", dest_label, read_lines])
    if r.returncode != 0:
        print(
            f"[relay_stop] tmux-bridge read (verify) failed: {r.stderr or r.stdout}",
            file=sys.stderr,
        )
        return r.returncode

    r = _bridge(["keys", dest_label, "Enter"])
    if r.returncode != 0:
        print(
            f"[relay_stop] tmux-bridge keys Enter failed: {r.stderr or r.stdout}",
            file=sys.stderr,
        )
        return r.returncode

    return 0


def hook_root() -> Path:
    return Path(__file__).resolve().parent

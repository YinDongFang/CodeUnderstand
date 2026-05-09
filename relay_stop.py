#!/usr/bin/env python3
"""
Shared Stop-hook relay: read Claude Code JSON from stdin, forward last_assistant_message
to the opposite tmux pane via tmux-bridge (smux read → type → read → keys Enter).

终止条件：根据 hook 载荷里的 transcript_path 读取对话 JSONL，统计本会话 assistant
条目数；超过 MAX_ASSISTANT_TURNS 则不再转发。

若存在 agent_id（子 agent 上下文），整次 hook 直接忽略、不转发。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAX_ASSISTANT_TURNS = 38


def _bridge(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux-bridge", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def is_subagent_payload(data: dict) -> bool:
    """主会话为 Stop 且无 agent_id；子 agent 为 SubagentStop 且通常带 agent_id。"""
    if data.get("hook_event_name") == "SubagentStop":
        return True
    aid = data.get("agent_id")
    return aid is not None and str(aid).strip() != ""


def _line_is_assistant_row(obj: dict) -> bool:
    if obj.get("type") == "assistant":
        return True
    msg = obj.get("message")
    if isinstance(msg, dict) and msg.get("role") == "assistant":
        return True
    return False


def count_assistant_turns_in_transcript(transcript_path: str | None) -> int:
    """Claude Code 会话为 JSONL，每行一条；统计 assistant 行数。"""
    if not transcript_path or not str(transcript_path).strip():
        return 0
    path = Path(transcript_path).expanduser()
    if not path.is_file():
        return 0
    n = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and _line_is_assistant_row(obj):
                    n += 1
    except OSError as e:
        print(f"[relay_stop] 无法读取 transcript: {path}: {e}", file=sys.stderr)
        return 0
    return n


def relay_stop_forward(dest_label: str) -> int:
    """
    从 stdin 读入 Claude Stop hook JSON，将 last_assistant_message 转发到 dest_label 对应 pane。

    dest_label: tmux-bridge 标签，如 learner_hook 使用 \"reader\"，reader_hook 使用 \"learner\"。
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[relay_stop] stdin JSON parse error: {e}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        return 0

    if is_subagent_payload(data):
        return 0

    turns = count_assistant_turns_in_transcript(data.get("transcript_path"))
    if turns > MAX_ASSISTANT_TURNS:
        return 0

    text = data.get("last_assistant_message")
    if not text or not str(text).strip():
        return 0

    text = str(text)
    read_lines = "30"

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

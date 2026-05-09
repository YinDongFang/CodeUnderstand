#!/usr/bin/env python3
"""
Claude Stop hook：读 stdin JSON，按 cwd 判断 Learner / Reader，将 last_assistant_message
经 tmux-bridge 转到对侧 pane。

Learner：cwd 落在本脚本所在目录（仓库根）的 agent/ 树下 → 转发到 reader。
否则视为 Reader → 转发到 learner。

终止（仅 Reader 侧、转发到 learner 时）：transcript_path JSONL 中 type=user 条数
超过 MAX_USER_MESSAGES 则不再转发，并在 stderr 打印全部用户输入及序号。
子 agent（SubagentStop 或带 agent_id）忽略。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAX_USER_MESSAGES = 38

LEARNER_ROOT = Path(__file__).resolve().parent / "agent"


def is_learner_cwd(cwd: str) -> bool:
    p = Path(cwd).expanduser().resolve()
    root = LEARNER_ROOT.resolve()
    return p == root or root in p.parents


def _bridge(args: list[str]) -> None:
    subprocess.run(
        ["tmux-bridge", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def is_subagent_payload(data: dict) -> bool:
    if data.get("hook_event_name") == "SubagentStop":
        return True
    aid = data.get("agent_id")
    return aid is not None and str(aid).strip() != ""


def _line_is_user_row(obj: dict) -> bool:
    if obj.get("type") == "user":
        return True
    msg = obj.get("message")
    if isinstance(msg, dict) and msg.get("role") == "user":
        return True
    return False


def _extract_user_text(obj: dict) -> str:
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                parts.append(str(block["text"]))
        return "\n".join(parts)
    return ""


def load_user_message_bodies(transcript_path: str) -> list[str]:
    path = Path(transcript_path).expanduser()
    bodies: list[str] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict) and _line_is_user_row(obj):
                bodies.append(_extract_user_text(obj))
    return bodies


def _print_all_user_inputs(bodies: list[str]) -> None:
    print(
        f"[relay_hook] 已达结束条件（user 条数 {len(bodies)} > {MAX_USER_MESSAGES}），"
        "全部用户输入如下：",
        file=sys.stderr,
    )
    for i, text in enumerate(bodies, start=1):
        print(f"\n===== 用户输入 #{i} =====\n{text}", file=sys.stderr)


def relay_forward_from_data(dest_label: str, data: dict) -> int:
    if is_subagent_payload(data):
        return 0

    if dest_label == "learner":
        bodies = load_user_message_bodies(data["transcript_path"])
        if len(bodies) > MAX_USER_MESSAGES:
            _print_all_user_inputs(bodies)
            return 0

    text = data.get("last_assistant_message")
    if not text or not str(text).strip():
        return 0

    text = str(text)
    read_lines = "30"

    _bridge(["read", dest_label, read_lines])
    _bridge(["type", dest_label, text])
    _bridge(["read", dest_label, read_lines])
    _bridge(["keys", dest_label, "Enter"])

    return 0


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    data = json.loads(raw)
    cwd = data["cwd"]
    dest = "reader" if is_learner_cwd(cwd) else "learner"
    return relay_forward_from_data(dest, data)


if __name__ == "__main__":
    sys.exit(main())

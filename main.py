#!/usr/bin/env python3
"""
Launch learner + reader Claude agents in one tmux session (two panes), using
tmux-bridge (smux) to cd into each workspace, start claude, then send a preset
prompt into the learner pane.

Stop hooks in each project should call learner_hook.py / reader_hook.py so that
last_assistant_message is relayed to the other pane via tmux-bridge.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

SESSION_NAME = "agent-loop"
PANE_LEARNER = "learner"
PANE_READER = "reader"


def tmux(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=check)


def bridge(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux-bridge", *args], capture_output=True, text=True, check=check)


def _require_ok(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{what} failed (exit {proc.returncode}): {err}")


def bridge_interact(label: str, text: str, *, read_lines: str = "40") -> None:
    """One typed line + Enter with smux read-guard (read → type → read → keys)."""
    _require_ok(bridge("read", label, read_lines), f"tmux-bridge read {label}")
    _require_ok(bridge("type", label, text), f"tmux-bridge type {label}")
    _require_ok(bridge("read", label, read_lines), f"tmux-bridge read {label} (after type)")
    _require_ok(bridge("keys", label, "Enter"), f"tmux-bridge keys {label} Enter")


def name_pane(target: str, label: str) -> None:
    _require_ok(bridge("name", target, label), f"tmux-bridge name {target} {label}")


def shell_cd_command(path: Path) -> str:
    return "cd " + shlex.quote(str(path.resolve()))


def setup_session(
    *,
    session: str,
    learner_dir: Path,
    reader_dir: Path,
    learner_cmd: str,
    reader_cmd: str,
    startup_wait: float,
    preset: str,
) -> None:
    base = f"{session}:0"
    pane_learner = f"{base}.0"
    pane_reader = f"{base}.1"

    tmux("kill-session", "-t", session)
    time.sleep(0.3)
    tmux("new-session", "-d", "-s", session, "-n", "main")
    tmux("split-window", "-h", "-t", f"{session}:0")

    name_pane(pane_learner, PANE_LEARNER)
    name_pane(pane_reader, PANE_READER)

    bridge_interact(PANE_LEARNER, shell_cd_command(learner_dir))
    bridge_interact(PANE_LEARNER, learner_cmd)

    bridge_interact(PANE_READER, shell_cd_command(reader_dir))
    bridge_interact(PANE_READER, reader_cmd)

    time.sleep(startup_wait)

    if preset.strip():
        bridge_interact(PANE_LEARNER, preset.rstrip("\n"))


def print_hook_hint(repo_root: Path) -> None:
    learner_py = repo_root / "learner_hook.py"
    reader_py = repo_root / "reader_hook.py"
    learner_block = {
        "hooks": {
            "Stop": [
                {"type": "command", "command": f"python3 {learner_py}"},
            ]
        }
    }
    reader_block = {
        "hooks": {
            "Stop": [
                {"type": "command", "command": f"python3 {reader_py}"},
            ]
        }
    }
    print()
    print("[launcher] 在 Learner 项目目录的 .claude/settings.json 中为 Stop 配置（示例）:")
    print(json.dumps(learner_block, indent=2, ensure_ascii=False))
    print("[launcher] 在 Reader 项目目录的 .claude/settings.json 中为 Stop 配置（示例）:")
    print(json.dumps(reader_block, indent=2, ensure_ascii=False))
    print()
    print("[launcher] 需要已安装 smux 提供的 tmux-bridge，并保证其在 PATH 中。")


def main() -> None:
    parser = argparse.ArgumentParser(description="tmux 双 pane + tmux-bridge 启动 Learner/Reader Claude")
    parser.add_argument(
        "--learner-dir",
        type=Path,
        required=True,
        help="Learner 侧工作目录（先 cd 再启动 claude）",
    )
    parser.add_argument(
        "--reader-dir",
        type=Path,
        required=True,
        help="Reader 侧工作目录",
    )
    parser.add_argument(
        "--preset",
        default="",
        help="启动完成后注入到 Learner 窗格的第一条输入（不含自动换行以外的处理）",
    )
    parser.add_argument(
        "--preset-file",
        type=Path,
        default=None,
        help="从文件读取预设内容并注入 Learner（优先于 --preset）",
    )
    parser.add_argument("--learner-cmd", default="claude", help="Learner 窗格中在 cd 之后执行的命令")
    parser.add_argument("--reader-cmd", default="claude", help="Reader 窗格中在 cd 之后执行的命令")
    parser.add_argument("--startup-wait", type=float, default=5.0, help="启动 claude 后等待秒数再注入预设")
    parser.add_argument("--session", default=SESSION_NAME, help="tmux 会话名")
    args = parser.parse_args()

    preset = args.preset
    if args.preset_file is not None:
        preset = args.preset_file.read_text(encoding="utf-8")

    repo_root = Path(__file__).resolve().parent

    try:
        setup_session(
            session=args.session,
            learner_dir=args.learner_dir,
            reader_dir=args.reader_dir,
            learner_cmd=args.learner_cmd,
            reader_cmd=args.reader_cmd,
            startup_wait=args.startup_wait,
            preset=preset,
        )
    except FileNotFoundError:
        print(
            "[launcher] 未找到 tmux 或 tmux-bridge，请先安装 tmux 与 smux（https://github.com/ShawnPana/smux）。",
            file=sys.stderr,
        )
        raise
    except RuntimeError as e:
        print(f"[launcher] {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[launcher] 会话 '{args.session}' 已创建（双 pane：{PANE_LEARNER} | {PANE_READER}）。")
    print(f"[launcher] 连接: tmux attach -t {args.session}")
    print_hook_hint(repo_root)


if __name__ == "__main__":
    main()

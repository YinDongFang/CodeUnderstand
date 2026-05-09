#!/usr/bin/env python3
"""
Launch learner + reader Claude agents in one tmux session (two panes), using
tmux-bridge (smux) to cd into each workspace, start claude, then inject the
reader 仓库的 README.md 全文到 Learner 并提示开始提问。

Learner 目录固定为 ~/CodeUnderstand/agent；Reader 目录为 ~/CodeUnderstand/projects/<相对路径>。

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

LEARNER_DIR = Path("~/CodeUnderstand/agent").expanduser()
PROJECTS_ROOT = Path("~/CodeUnderstand/projects").expanduser()
README_NAME = "README.md"


def tmux(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=check)


def bridge(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux-bridge", *args], capture_output=True, text=True, check=check)


def _require_ok(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{what} failed (exit {proc.returncode}): {err}")


def bridge_interact(label: str, text: str, *, read_lines: str = "40") -> None:
    """One typed block + Enter with smux read-guard (read → type → read → keys)."""
    _require_ok(bridge("read", label, read_lines), f"tmux-bridge read {label}")
    _require_ok(bridge("type", label, text), f"tmux-bridge type {label}")
    _require_ok(bridge("read", label, read_lines), f"tmux-bridge read {label} (after type)")
    _require_ok(bridge("keys", label, "Enter"), f"tmux-bridge keys {label} Enter")


def name_pane(target: str, label: str) -> None:
    _require_ok(bridge("name", target, label), f"tmux-bridge name {target} {label}")


def shell_cd_command(path: Path) -> str:
    return "cd " + shlex.quote(str(path.resolve()))


def resolve_reader_dir(reader_rel: str) -> Path:
    rel = reader_rel.strip().replace("\\", "/").strip("/")
    if not rel or rel == ".":
        raise ValueError("Reader 相对路径不能为空")
    if rel.startswith("..") or "/../" in f"/{rel}/":
        raise ValueError("Reader 相对路径不允许包含 '..'")
    root = PROJECTS_ROOT.expanduser().resolve()
    reader_dir = (root / rel).resolve()
    try:
        reader_dir.relative_to(root)
    except ValueError as e:
        raise ValueError(f"Reader 路径必须位于 {root} 之下") from e
    return reader_dir


def load_readme_for_learner(reader_dir: Path) -> str:
    readme_path = reader_dir / README_NAME
    if not readme_path.is_file():
        raise FileNotFoundError(f"未找到 {readme_path}，请在 Reader 仓库根目录放置 {README_NAME}")
    return readme_path.read_text(encoding="utf-8")


def build_learner_readme_prompt(reader_dir: Path, readme_body: str) -> str:
    return (
        "下面是目标仓库（Reader 侧）根目录中的 README.md 文档全文。"
        "文件名：README.md。\n\n"
        "----- README.md -----\n"
        f"{readme_body.rstrip()}\n"
        "----- 结束 -----\n\n"
        "请先阅读以上内容，然后向 Reader 开始你的第一个提问（按你在 CLAUDE.md 中的角色与流程执行）。"
    )


def setup_session(
    *,
    session: str,
    learner_dir: Path,
    reader_dir: Path,
    learner_cmd: str,
    reader_cmd: str,
    startup_wait: float,
    learner_seed: str,
) -> None:
    base = f"{session}:0"
    pane_learner = f"{base}.0"
    pane_reader = f"{base}.1"

    time.sleep(0.3)
    tmux("split-window", "-h", "-t", f"{session}:0")

    name_pane(pane_learner, PANE_LEARNER)
    name_pane(pane_reader, PANE_READER)

    bridge_interact(PANE_LEARNER, shell_cd_command(learner_dir))
    bridge_interact(PANE_LEARNER, learner_cmd)

    bridge_interact(PANE_READER, shell_cd_command(reader_dir))
    bridge_interact(PANE_READER, reader_cmd)

    time.sleep(startup_wait)

    if learner_seed.strip():
        bridge_interact(PANE_LEARNER, learner_seed.rstrip("\n"))


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
        "reader_rel",
        help="Reader 仓库相对 ~/CodeUnderstand/projects/ 的路径，例如 myrepo 或 org/myrepo",
    )
    parser.add_argument("--learner-cmd", default="claude -y", help="Learner 窗格中在 cd 之后执行的命令")
    parser.add_argument("--reader-cmd", default="claude -y", help="Reader 窗格中在 cd 之后执行的命令")
    parser.add_argument(
        "--startup-wait",
        type=float,
        default=5.0,
        help="启动 claude 后等待秒数，再向 Learner 注入 readme 与开始提问说明",
    )
    parser.add_argument("--session", default=SESSION_NAME, help="tmux 会话名")
    args = parser.parse_args()

    learner_dir = LEARNER_DIR.expanduser().resolve()
    reader_dir = resolve_reader_dir(args.reader_rel)

    if not learner_dir.is_dir():
        print(f"[launcher] Learner 目录不存在: {learner_dir}", file=sys.stderr)
        sys.exit(1)
    if not reader_dir.is_dir():
        print(f"[launcher] Reader 目录不存在: {reader_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        readme_body = load_readme_for_learner(reader_dir)
    except FileNotFoundError as e:
        print(f"[launcher] {e}", file=sys.stderr)
        sys.exit(1)

    learner_seed = build_learner_readme_prompt(reader_dir, readme_body)
    repo_root = Path(__file__).resolve().parent

    try:
        setup_session(
            session=args.session,
            learner_dir=learner_dir,
            reader_dir=reader_dir,
            learner_cmd=args.learner_cmd,
            reader_cmd=args.reader_cmd,
            startup_wait=args.startup_wait,
            learner_seed=learner_seed,
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

    print(f"[launcher] Learner: {learner_dir}")
    print(f"[launcher] Reader:  {reader_dir}（相对 projects: {args.reader_rel!r}）")
    print(f"[launcher] 会话 '{args.session}' 已创建（双 pane：{PANE_LEARNER} | {PANE_READER}）。")
    print(f"[launcher] 连接: tmux attach -t {args.session}")
    print_hook_hint(repo_root)


if __name__ == "__main__":
    main()

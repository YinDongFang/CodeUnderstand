"""沙箱 HOME 创建与 .claude 配置复制。

不复制真实 ~/.claude/projects（避免共享会话）。重入会清空沙箱再重建，
等价于重跑 bootstrap 宏阶段。
"""
from __future__ import annotations

import os
import shutil

from cu.paths import sandbox_home, snapshots_dir


_CLAUDE_COPY_EXCLUDES = {"projects"}


def _real_home() -> str:
    return os.environ.get("HOME", os.path.expanduser("~"))


def _copy_claude_config(real_claude: str, sandbox_claude: str) -> None:
    if not os.path.isdir(real_claude):
        os.makedirs(sandbox_claude, exist_ok=True)
        return
    for entry in os.listdir(real_claude):
        if entry in _CLAUDE_COPY_EXCLUDES:
            continue
        src = os.path.join(real_claude, entry)
        dst = os.path.join(sandbox_claude, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    projects = os.path.join(sandbox_claude, "projects")
    os.makedirs(projects, exist_ok=True)


def bootstrap_sandbox(job_id: str) -> str:
    """创建或重置沙箱 HOME，返回 home 绝对路径。"""
    home = sandbox_home(job_id)
    snaps = snapshots_dir(job_id)

    if os.path.exists(home):
        shutil.rmtree(home)

    os.makedirs(home, exist_ok=True)
    os.makedirs(snaps, exist_ok=True)

    real_claude = os.path.join(_real_home(), ".claude")
    sandbox_claude = os.path.join(home, ".claude")
    os.makedirs(sandbox_claude, exist_ok=True)
    _copy_claude_config(real_claude, sandbox_claude)

    for sub in ("logs", "loop_logs", "tmp"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)

    return home

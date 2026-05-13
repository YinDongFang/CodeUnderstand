"""为各宏阶段子进程组装环境变量字典。

策略（spec §2）：继承当前进程环境 → 覆盖作业级变量。
"""
from __future__ import annotations

import os

from cu.paths import sandbox_home, artifact_root
from cu.test_mode import test_mode_subprocess_overlay


def stage_env(
    *,
    job_id: str,
    repo: str,
    session_id: str = "",
    github_url: str = "",
    claude_project_dir: str = "",
) -> dict[str, str]:
    """返回完整的子进程环境变量（基于当前进程 + 作业覆盖）。"""
    home = sandbox_home(job_id)
    art = artifact_root(job_id, repo)
    env = os.environ.copy()

    env["HOME"] = home
    env["PROJECTS_DIR"] = os.path.join(art, "code")
    env["OUTPUTS_DIR"] = home
    env["CODE_UNDERSTAND_STATE_ROOT"] = home

    if session_id:
        env["SESSION_ID"] = session_id
    if github_url:
        env["GITHUB_URL"] = github_url
    if claude_project_dir:
        env["CLAUDE_PROJECT_DIR"] = claude_project_dir

    env.update(test_mode_subprocess_overlay())
    return env

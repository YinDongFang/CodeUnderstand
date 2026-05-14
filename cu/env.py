"""为各宏阶段子进程组装环境变量字典。

策略（spec §2）：继承当前进程环境 → 覆盖作业级变量。

作业覆盖键由 ``job_env_overrides`` 集中声明（含 ``GH_ARCHIVE_PROXY_PREFIX``、
``CU_JOB_PIPELINE``）；``stage_env`` 在其基础上拷贝整份 ``os.environ`` 供子进程使用。
"""
from __future__ import annotations

import os

from cu.paths import sandbox_home, artifact_root
from cu.pipeline_env import normalized_gh_archive_proxy_prefix
from cu.constants import ENV_CU_JOB_PIPELINE
from cu.test_mode import test_mode_subprocess_overlay


def job_env_overrides(
    *,
    job_id: str,
    repo: str,
    session_id: str = "",
    github_url: str = "",
    claude_project_dir: str = "",
) -> dict[str, str]:
    """仅包含作业须写入的环境变量（不含整份 ``os.environ``）。

    编排线程 **inline** 模式会将此 dict ``update`` 进 ``os.environ``；
    ``stage_env`` 亦基于此构造完整子进程环境。
    """
    home = sandbox_home(job_id)
    art = artifact_root(job_id, repo)
    out: dict[str, str] = {
        "HOME": home,
        "PROJECTS_DIR": os.path.join(art, "code"),
        "OUTPUTS_DIR": home,
        "CODE_UNDERSTAND_STATE_ROOT": home,
        "GH_ARCHIVE_PROXY_PREFIX": normalized_gh_archive_proxy_prefix(),
        ENV_CU_JOB_PIPELINE: "1",
    }
    if session_id:
        out["SESSION_ID"] = session_id
    if github_url:
        out["GITHUB_URL"] = github_url
    if claude_project_dir:
        out["CLAUDE_PROJECT_DIR"] = claude_project_dir

    out.update(test_mode_subprocess_overlay())
    return out


def stage_env(
    *,
    job_id: str,
    repo: str,
    session_id: str = "",
    github_url: str = "",
    claude_project_dir: str = "",
) -> dict[str, str]:
    """返回完整的子进程环境变量（基于当前进程 + 作业覆盖）。"""
    env = os.environ.copy()
    env.update(
        job_env_overrides(
            job_id=job_id,
            repo=repo,
            session_id=session_id,
            github_url=github_url,
            claude_project_dir=claude_project_dir,
        )
    )
    return env

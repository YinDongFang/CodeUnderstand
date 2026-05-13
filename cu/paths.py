"""路径常量与作业目录解析。

布局：见模块顶部 docstring 与 spec §3.4。
"""
from __future__ import annotations

import os


def _real_home() -> str:
    return os.environ.get("HOME", os.path.expanduser("~"))


def data_root() -> str:
    override = os.environ.get("CU_DATA_ROOT", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(_real_home(), ".code-understand")


def job_dir(job_id: str) -> str:
    return os.path.join(data_root(), "jobs", job_id)


def sandbox_home(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "home")


def snapshots_dir(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "snapshots")


def artifact_root(job_id: str, repo: str) -> str:
    return os.path.join(sandbox_home(job_id), f"code-understand-{repo}")


def code_dir(job_id: str, repo: str) -> str:
    return os.path.join(artifact_root(job_id, repo), "code", repo)


def db_path() -> str:
    return os.path.join(data_root(), "db.sqlite")

"""作业级流水线相关环境变量的解析（宿主默认值 vs 编排注入）。

编排路径下 ``cu.env.job_env_overrides`` 会在入口处写入 ``PROJECTS_DIR`` /
``OUTPUTS_DIR`` / ``CODE_UNDERSTAND_STATE_ROOT`` 等；业务代码应通过这些解析函数读取，
不在各处手写 ``Path.home()/…`` 默认值。
"""
from __future__ import annotations

import os
from pathlib import Path

from cu.constants import DEFAULT_GH_ARCHIVE_PROXY_PREFIX


def normalized_gh_archive_proxy_prefix() -> str:
    """返回以 ``/`` 结尾的代理前缀，供 ``prefix + https://…`` 拼接。"""
    raw = os.environ.get("GH_ARCHIVE_PROXY_PREFIX", "").strip()
    base = raw or DEFAULT_GH_ARCHIVE_PROXY_PREFIX
    return base.rstrip("/") + "/"


def resolve_projects_dir() -> Path:
    raw = os.environ.get("PROJECTS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / "projects"


def resolve_outputs_dir() -> Path:
    raw = os.environ.get("OUTPUTS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / "outputs"


def resolve_state_root() -> Path:
    raw = os.environ.get("CODE_UNDERSTAND_STATE_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home().resolve()


def normalize_session_id_filename(session_id: str) -> str:
    sid = session_id.strip()
    if sid.lower().endswith(".jsonl"):
        sid = sid[:-6]
    return sid


def github_web_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"

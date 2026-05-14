"""仓库级常量（可被环境与规格覆盖）。"""
from __future__ import annotations

# GitHub archive / API 经代理访问时的默认前缀（可被 ``GH_ARCHIVE_PROXY_PREFIX`` 覆盖）
DEFAULT_GH_ARCHIVE_PROXY_PREFIX = "https://gh-proxy.org/"

# 标记当前进程处于编排作业流水线上下文（``stage_env`` / ``job_env_overrides`` 已写入）
ENV_CU_JOB_PIPELINE = "CU_JOB_PIPELINE"

"""作业流水线核心循环：数据库状态迁移 + 调用 ``STAGE_RUNNERS``。

- 生产环境由 ``cu.job_worker`` 子进程调用（``HOME`` 已为沙箱）。
- 测试在同进程运行时设置 ``CU_JOB_WORKER_INLINE=1``，见 ``cu.orchestrator._run_thread``。
  此时在每个宏阶段前将 ``job_env_overrides`` 写入 ``os.environ``，结束时复原，
  以便进程内流水线模块与编排路径共用同一套 ``PROJECTS_DIR`` / ``OUTPUTS_DIR`` 等。
"""
from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from cu.models import JOB_STAGES
from cu.snapshot import save_snapshot

if TYPE_CHECKING:
    from cu.stages.context import JobContext


def _inline_worker() -> bool:
    raw = os.environ.get("CU_JOB_WORKER_INLINE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def run_stages_in_process(
    job_id: str,
    stages_to_run: list[str],
    ctx: JobContext,
    *,
    api_web_build_rewrite: bool,
    cancel_flag: threading.Event | None = None,
    stage_runners: dict | None = None,
) -> int:
    """顺序执行 ``stages_to_run``。

    Returns:
        0 成功；1 失败；2 取消（仅当传入 ``cancel_flag`` 且在阶段间隙被取消）。
    """
    from cu.env import job_env_overrides
    from cu.orchestrator import _get_stages, _now, _update_job, _update_stage
    from cu.stages import STAGE_RUNNERS as default_runners

    runners = stage_runners if stage_runners is not None else default_runners

    touched: set[str] = set()
    saved_vals: dict[str, str | None] = {}
    inline = _inline_worker()

    def _apply_job_env() -> None:
        ov = job_env_overrides(
            job_id=ctx.job_id,
            repo=ctx.repo,
            session_id=ctx.session_id or "",
            github_url=ctx.github_url or "",
            claude_project_dir=ctx.claude_project_dir or "",
        )
        if not inline:
            return
        for k in ov:
            if k not in touched:
                touched.add(k)
                saved_vals[k] = os.environ.get(k)
        os.environ.update(ov)

    try:
        for stage in stages_to_run:
            _apply_job_env()

            if cancel_flag is not None and cancel_flag.is_set():
                _update_stage(job_id, stage, status="cancelled", ended_at=_now())
                _update_job(job_id, status="cancelled")
                return 2

            attempt = _get_stages(job_id)[stage].attempt + 1
            _update_stage(
                job_id, stage,
                status="running", started_at=_now(),
                ended_at=None, exit_code=None, log_tail="",
                attempt=attempt,
            )

            try:
                ctx.build_with_rewrite = api_web_build_rewrite and stage == "build"
                runners[stage](ctx)
            except Exception as e:
                log_tail = str(e)[-2000:]
                _update_stage(
                    job_id, stage,
                    status="failed", ended_at=_now(),
                    log_tail=log_tail,
                )
                _update_job(job_id, status="failed", notes=log_tail[:500])
                return 1

            _update_stage(job_id, stage, status="success", ended_at=_now())
            _update_job(
                job_id,
                session_id=ctx.session_id,
                claude_project_dir=ctx.claude_project_dir,
            )

            if stage != "build":
                save_snapshot(job_id, stage)

        idx_last = JOB_STAGES.index(stages_to_run[-1])
        if idx_last >= len(JOB_STAGES) - 1:
            _update_job(job_id, status="success")
        else:
            _update_job(job_id, status="pending")
        return 0
    finally:
        if inline:
            for k in touched:
                old = saved_vals.get(k)
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

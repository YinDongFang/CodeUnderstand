"""作业子进程入口：整段流水线在同一 Python 进程中运行（``HOME`` 已为沙箱）。

由 ``cu.orchestrator`` 通过 ``python -m cu.job_worker <spec.json>`` 启动；
阶段事件经 ``CU_JOB_EVENT_FD``（``socket.socketpair`` 一端）发往父进程以转发 SSE。
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from cu.job_runner_core import run_stages_in_process
from cu.logging_config import configure_logging
from cu.stages.context import JobContext


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    argv = argv or sys.argv
    spec_path = argv[1]

    fd_raw = os.environ.get("CU_JOB_EVENT_FD", "").strip()
    if not fd_raw:
        print("missing CU_JOB_EVENT_FD", file=sys.stderr)
        return 2
    fd = int(fd_raw)
    evt_sock = socket.socket(fileno=fd)

    def emit(stage: str, message: str) -> None:
        line = json.dumps({"stage": stage, "message": message}, ensure_ascii=False) + "\n"
        evt_sock.sendall(line.encode("utf-8"))

    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))

        ctx_kwargs = dict(spec["ctx"])
        ctx = JobContext(
            job_id=ctx_kwargs["job_id"],
            repo=ctx_kwargs["repo"],
            zip_url=ctx_kwargs["zip_url"],
            github_url=ctx_kwargs["github_url"],
            session_id=ctx_kwargs.get("session_id") or "",
            claude_project_dir=ctx_kwargs.get("claude_project_dir") or "",
            on_event=emit,
            pid_sink=None,
            build_with_rewrite=False,
        )

        job_id = spec["job_id"]
        stages_to_run = spec["stages_to_run"]
        api_web_build_rewrite = bool(spec.get("api_web_build_rewrite", False))

        return run_stages_in_process(
            job_id,
            stages_to_run,
            ctx,
            api_web_build_rewrite=api_web_build_rewrite,
            cancel_flag=None,
        )
    finally:
        try:
            evt_sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        evt_sock.close()


if __name__ == "__main__":
    raise SystemExit(main())

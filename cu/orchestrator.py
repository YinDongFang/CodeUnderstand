"""Orchestrator：作业生命周期与状态机持久化。

约束：
- 模块级单例（_ACTIVE 字典管理线程与 PID）
- 每作业一个守护线程顺序跑 4 阶段
- 每阶段：状态置 running → 调 STAGE_RUNNERS[stage](ctx) → 成功置 success + 拍快照（除 build）；失败置 failed
- 失败/取消时停止后续阶段
- DB 在 cu.paths.db_path() 处，由 init_db() 幂等创建
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cu.db import get_connection, init_db
from cu.models import JobRecord, StageRunRecord, JOB_STAGES
from cu.paths import job_dir
from cu.snapshot import (
    save_snapshot,
    restore_snapshot,
    snapshot_exists,
)
from cu.stages import JobContext, STAGE_RUNNERS
from cu.state import can_run_stage, stages_after


_ZIP_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip$"
)


@dataclass
class _ActiveJob:
    thread: threading.Thread
    cancel_flag: threading.Event
    current_pid: list[int] = field(default_factory=list)


_ACTIVE: dict[str, _ActiveJob] = {}
_ACTIVE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_github_url(zip_url: str) -> tuple[str, str]:
    m = _ZIP_URL_RE.match(zip_url)
    if not m:
        raise ValueError(f"URL 不符合 GitHub archive ZIP 格式: {zip_url}")
    return m.group(1), m.group(2)


# ---------- DB helpers ----------

def _insert_job(rec: JobRecord) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO jobs(job_id, repo, zip_url, github_url, session_id,
                                claude_project_dir, status, created_at, updated_at, notes)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (rec.job_id, rec.repo, rec.zip_url, rec.github_url, rec.session_id,
             rec.claude_project_dir, rec.status, rec.created_at, rec.updated_at, rec.notes),
        )
        for stage in JOB_STAGES:
            conn.execute(
                "INSERT INTO stage_runs(job_id, stage) VALUES(?,?)",
                (rec.job_id, stage),
            )


def _get_job(job_id: str) -> JobRecord | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return JobRecord.from_row(row) if row else None


def _get_stages(job_id: str) -> dict[str, StageRunRecord]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM stage_runs WHERE job_id=?", (job_id,)
        ).fetchall()
        return {row["stage"]: StageRunRecord.from_row(row) for row in rows}


def _update_job(job_id: str, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", vals)


def _update_stage(job_id: str, stage: str, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id, stage]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE stage_runs SET {cols} WHERE job_id=? AND stage=?",
            vals,
        )


def _stage_status_map(job_id: str) -> dict[str, str]:
    return {s: r.status for s, r in _get_stages(job_id).items()}


def _reset_stage(job_id: str, stage: str) -> None:
    _update_stage(
        job_id, stage,
        status="pending",
        started_at=None, ended_at=None,
        exit_code=None, log_tail="",
    )


# ---------- Public API ----------

def ensure_db() -> None:
    init_db()


def create_job(zip_url: str, *, job_id: str | None = None, notes: str = "") -> JobRecord:
    ensure_db()
    gh_user, repo = _parse_github_url(zip_url)
    job_id = job_id or f"{repo}-{uuid.uuid4().hex[:8]}"
    rec = JobRecord(
        job_id=job_id, repo=repo, zip_url=zip_url,
        github_url=f"https://github.com/{gh_user}/{repo}",
        session_id="", claude_project_dir="",
        status="pending",
        created_at=_now(), updated_at=_now(),
        notes=notes,
    )
    _insert_job(rec)
    return rec


def list_jobs(status: str | None = None) -> list[JobRecord]:
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [JobRecord.from_row(r) for r in rows]


def get_job(job_id: str) -> JobRecord | None:
    return _get_job(job_id)


def get_stages(job_id: str) -> dict[str, StageRunRecord]:
    return _get_stages(job_id)


def run_stage(job_id: str, stage: str) -> threading.Thread:
    """启动指定阶段执行（后台线程）。依赖未满足抛 ValueError，作业不存在抛 KeyError。"""
    rec = _get_job(job_id)
    if rec is None:
        raise KeyError(f"job not found: {job_id}")
    status_map = _stage_status_map(job_id)
    if not can_run_stage(stage, status_map):
        raise ValueError(f"dependencies not met for stage {stage}: {status_map}")
    return _start_job_thread(job_id, start_from=stage)


def rerun_from(job_id: str, stage: str) -> threading.Thread:
    """从 stage 重跑：恢复 post-prev 快照（若非 bootstrap）→ 把 stage..build 置 pending → 启动。"""
    rec = _get_job(job_id)
    if rec is None:
        raise KeyError(f"job not found: {job_id}")
    status_map = _stage_status_map(job_id)
    if not can_run_stage(stage, status_map):
        raise ValueError(f"dependencies not met for rerun {stage}: {status_map}")

    if stage == "bootstrap":
        d = job_dir(job_id)
        for sub in ("home", "snapshots"):
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                shutil.rmtree(p)
    else:
        prev = JOB_STAGES[JOB_STAGES.index(stage) - 1]
        if not snapshot_exists(job_id, prev):
            raise FileNotFoundError(f"missing snapshot post-{prev} for rerun {stage}")
        restore_snapshot(job_id, prev)

    for s in stages_after(stage):
        _reset_stage(job_id, s)
    return _start_job_thread(job_id, start_from=stage)


def cancel(job_id: str) -> bool:
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(job_id)
    if not active:
        return False
    active.cancel_flag.set()
    for pid in list(active.current_pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _update_job(job_id, status="cancelled")
    return True


def delete_job(job_id: str) -> bool:
    """删除作业：取消运行 + join 线程 + 删沙箱/快照目录 + DELETE DB 记录。

    Returns:
        True 当且仅当 DB 中确实删了一条记录；否则 False。
    """
    cancel(job_id)
    with _ACTIVE_LOCK:
        active = _ACTIVE.pop(job_id, None)
    if active is not None:
        active.thread.join(timeout=5)
    d = job_dir(job_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
        return cur.rowcount > 0


def is_running(job_id: str) -> bool:
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(job_id)
        return bool(active and active.thread.is_alive())


# ---------- Thread runner ----------

def _start_job_thread(job_id: str, *, start_from: str) -> threading.Thread:
    with _ACTIVE_LOCK:
        existing = _ACTIVE.get(job_id)
        if existing and existing.thread.is_alive():
            raise RuntimeError(f"job {job_id} 已在运行")
        cancel_flag = threading.Event()
        pid_holder: list[int] = []
        t = threading.Thread(
            target=_run_thread,
            args=(job_id, start_from, cancel_flag, pid_holder),
            daemon=True,
            name=f"cu-job-{job_id}",
        )
        _ACTIVE[job_id] = _ActiveJob(
            thread=t, cancel_flag=cancel_flag, current_pid=pid_holder,
        )
        t.start()
        return t


def _run_thread(
    job_id: str,
    start_from: str,
    cancel_flag: threading.Event,
    pid_holder: list[int],
) -> None:
    rec = _get_job(job_id)
    if rec is None:
        return
    _update_job(job_id, status="running")

    ctx = JobContext(
        job_id=rec.job_id,
        repo=rec.repo,
        zip_url=rec.zip_url,
        github_url=rec.github_url,
        session_id=rec.session_id,
        claude_project_dir=rec.claude_project_dir,
        pid_sink=lambda pid: pid_holder.append(pid),
    )

    idx_start = JOB_STAGES.index(start_from)
    try:
        for stage in JOB_STAGES[idx_start:]:
            if cancel_flag.is_set():
                _update_stage(job_id, stage, status="cancelled", ended_at=_now())
                _update_job(job_id, status="cancelled")
                return

            attempt = _get_stages(job_id)[stage].attempt + 1
            _update_stage(
                job_id, stage,
                status="running", started_at=_now(),
                ended_at=None, exit_code=None, log_tail="",
                attempt=attempt,
            )
            pid_holder.clear()
            try:
                STAGE_RUNNERS[stage](ctx)
            except Exception as e:
                log_tail = str(e)[-2000:]
                _update_stage(
                    job_id, stage,
                    status="failed", ended_at=_now(),
                    log_tail=log_tail,
                )
                _update_job(job_id, status="failed", notes=log_tail[:500])
                return

            _update_stage(job_id, stage, status="success", ended_at=_now())

            # ctx 字段可能被 stages 写入（session_id / claude_project_dir），同步回 DB
            _update_job(
                job_id,
                session_id=ctx.session_id,
                claude_project_dir=ctx.claude_project_dir,
            )

            if stage != "build":
                save_snapshot(job_id, stage)

        _update_job(job_id, status="success")
    finally:
        # 不主动从 _ACTIVE 删除：线程对象的 is_alive() 会变 False；
        # delete_job 与 _start_job_thread 都会清理或重建条目。
        pass

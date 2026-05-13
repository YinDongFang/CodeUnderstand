"""阶段状态机规则：依赖判定、可执行/可重跑判定、空位查找。"""
from __future__ import annotations

from cu.models import JOB_STAGES


def _prev_stage(stage: str) -> str | None:
    idx = JOB_STAGES.index(stage)
    if idx == 0:
        return None
    return JOB_STAGES[idx - 1]


def can_run_stage(stage: str, stage_status: dict[str, str]) -> bool:
    """是否允许执行该阶段（依赖：所有前序阶段须为 success）。"""
    if stage not in JOB_STAGES:
        return False
    prev = _prev_stage(stage)
    if prev is None:
        return True
    return stage_status.get(prev) == "success"


def can_rerun_stage(stage: str, stage_status: dict[str, str]) -> bool:
    """重跑判定：与 can_run 相同（依赖前序 success），不关心该阶段当前状态。"""
    return can_run_stage(stage, stage_status)


def next_pending_stage(stage_status: dict[str, str]) -> str | None:
    """返回下一个 *pending* 且可执行的阶段。

    语义：自动推进只看 literal 'pending'。'failed'/'cancelled' 视为需用户显式 rerun，
    本函数不会跨过它们自动启动新阶段。返回 None 表示无可自动推进项。
    """
    for s in JOB_STAGES:
        status = stage_status.get(s, "pending")
        if status == "success":
            continue
        if status == "pending" and can_run_stage(s, stage_status):
            return s
        return None
    return None


def stages_after(stage: str) -> tuple[str, ...]:
    """返回该阶段（含）之后的所有阶段，用于重跑时把该阶段及后续置 pending。"""
    if stage not in JOB_STAGES:
        return ()
    idx = JOB_STAGES.index(stage)
    return JOB_STAGES[idx:]

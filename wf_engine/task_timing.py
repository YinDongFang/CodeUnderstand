from __future__ import annotations

from datetime import datetime

from wf_engine import status as S
from wf_engine.lease_util import parse_utc_iso

_TERMINAL = frozenset({S.TASK_SUCCEEDED, S.TASK_FAILED, S.TASK_FAILED_STALLED})


def compute_active_duration_seconds(
    *,
    created_at: str,
    updated_at: str,
    status: str,
    interrupt_wall_seconds_accumulated: int,
    waiting_human_since: str | None,
    now: datetime,
) -> int:
    """Wall seconds from created_at to end, minus completed + open interrupt waits."""
    end = parse_utc_iso(updated_at) if status in _TERMINAL else now
    created = parse_utc_iso(created_at)
    wall = int((end - created).total_seconds())
    acc = max(0, int(interrupt_wall_seconds_accumulated))
    open_intr = 0
    if status == S.TASK_WAITING_HUMAN and waiting_human_since:
        open_intr = max(
            0,
            int((end - parse_utc_iso(waiting_human_since)).total_seconds()),
        )
    return max(0, wall - acc - open_intr)

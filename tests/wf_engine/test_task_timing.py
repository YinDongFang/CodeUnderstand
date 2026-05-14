from datetime import datetime, timedelta, timezone

from wf_engine import status as S
from wf_engine.task_timing import compute_active_duration_seconds


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_terminal_uses_updated_at_not_now():
    created = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    updated = created + timedelta(seconds=100)
    now = updated + timedelta(days=1)
    sec = compute_active_duration_seconds(
        created_at=_iso(created),
        updated_at=_iso(updated),
        status=S.TASK_SUCCEEDED,
        interrupt_wall_seconds_accumulated=0,
        waiting_human_since=None,
        now=now,
    )
    assert sec == 100


def test_deducts_open_interrupt_half_segment():
    created = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    intr = created + timedelta(seconds=30)
    end = created + timedelta(seconds=120)
    sec = compute_active_duration_seconds(
        created_at=_iso(created),
        updated_at=_iso(end),
        status=S.TASK_WAITING_HUMAN,
        interrupt_wall_seconds_accumulated=0,
        waiting_human_since=_iso(intr),
        now=end,
    )
    assert sec == 120 - (120 - 30)


def test_deducts_accumulated_and_open():
    created = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    intr = created + timedelta(seconds=40)
    end = created + timedelta(seconds=200)
    sec = compute_active_duration_seconds(
        created_at=_iso(created),
        updated_at=_iso(end),
        status=S.TASK_WAITING_HUMAN,
        interrupt_wall_seconds_accumulated=50,
        waiting_human_since=_iso(intr),
        now=end,
    )
    open_ = 200 - 40
    assert sec == max(0, 200 - 50 - open_)

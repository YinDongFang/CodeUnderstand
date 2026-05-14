# Task duration, execution count, and English detail UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist `execution_count` and interrupt wall-clock accounting; expose `active_duration_seconds` on list/detail APIs; show both on the Web list and detail; translate the **right-hand detail pane** to English per spec.

**Architecture:** Add three `tasks` columns + migration; update store methods (`open_interrupt`, `apply_resolve`, `prepare_task_for_rerun_execution`, `create_task` INSERT) and a pure **`compute_active_duration_seconds`** helper used by FastAPI serializers. Web: extend `api.ts` types and `App.tsx` strings/layout; keep left rail + create modal in Chinese.

**Tech Stack:** Python 3.12, SQLite, FastAPI, pytest, React + TypeScript (Vite).

**Spec:** `docs/superpowers/specs/2026-05-15-task-duration-execution-count-ui-design.md`

---

## File map

| File | Responsibility |
|------|------------------|
| `wf_engine/task_timing.py` (create) | Pure `compute_active_duration_seconds` using `parse_utc_iso` |
| `wf_engine/store/sqlite.py` | Schema + migration; `open_interrupt` / `apply_resolve` / `prepare_task_for_rerun_execution` / `create_task` INSERT |
| `wf_engine/server/routes_tasks.py` | After-create `execution_count=1`; `_serialize_task_detail` + `list_tasks` include new fields |
| `tests/wf_engine/test_task_timing.py` (create) | Unit tests for duration math (terminal, waiting, accumulated) |
| `tests/wf_engine/test_sqlite_store.py` | Migration + store behavior (interrupt flush, resolve, rerun bump) |
| `tests/wf_engine/test_api_tasks.py` | List/detail JSON shape |
| `web/src/api.ts` | `TaskSummary` / `TaskDetail` fields |
| `web/src/App.tsx` + `web/src/App.css` | List row meta, English detail labels |

---

### Task 1: `compute_active_duration_seconds` + unit tests

**Files:**

- Create: `wf_engine/task_timing.py`
- Create: `tests/wf_engine/test_task_timing.py`

- [ ] **Step 1: Add pure helper**

Create `wf_engine/task_timing.py`:

```python
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
```

- [ ] **Step 2: Unit tests**

Create `tests/wf_engine/test_task_timing.py`:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/wf_engine/test_task_timing.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add wf_engine/task_timing.py tests/wf_engine/test_task_timing.py
git commit -m "feat(wf_engine): add active duration helper and tests"
```

---

### Task 2: SQLite schema + interrupt / resolve / rerun / create_task

**Files:**

- Modify: `wf_engine/store/sqlite.py`

- [ ] **Step 1: Extend CREATE TABLE and migration**

In `init_schema` `CREATE TABLE tasks` block, append:  
`execution_count INTEGER NOT NULL DEFAULT 0`,  
`interrupt_wall_seconds_accumulated INTEGER NOT NULL DEFAULT 0`,  
`waiting_human_since TEXT`.

In `_migrate_tasks_table`, `ALTER TABLE` add missing columns; then backfill:

- `execution_count = 1` where `execution_count = 0 AND status != ?` (`TASK_PENDING`).
- `waiting_human_since = updated_at` where `status = TASK_WAITING_HUMAN` and `waiting_human_since IS NULL`.

Extend `create_task` INSERT with `0`, `0`, `NULL` for the three columns.

- [ ] **Step 2: `open_interrupt`**

In a transaction: if row already `TASK_WAITING_HUMAN` with non-null `waiting_human_since`, add elapsed seconds to `interrupt_wall_seconds_accumulated`. Then set `waiting_human_since = now` (and existing interrupt fields).

- [ ] **Step 3: `apply_resolve`**

Compute delta from `waiting_human_since`; `interrupt_wall_seconds_accumulated += delta`; `waiting_human_since = NULL`; existing resolve fields; **`execution_count += 1`**; `worker_generation + 1`.

- [ ] **Step 4: `prepare_task_for_rerun_execution`**

Add `execution_count = execution_count + 1`.

- [ ] **Step 5: Add `mark_first_run_scheduled(self, task_id: str)`**

`UPDATE tasks SET execution_count = 1 WHERE id=? AND execution_count = 0` (called from route after spawn).

- [ ] **Step 6: Extend `tests/wf_engine/test_sqlite_store.py`**

Cover migration, nested `open_interrupt` accumulation, `apply_resolve` bump, rerun bump.

- [ ] **Step 7: Run**

Run: `uv run pytest tests/wf_engine/test_sqlite_store.py tests/wf_engine/ -q --tb=short`

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add wf_engine/store/sqlite.py tests/wf_engine/test_sqlite_store.py
git commit -m "feat(wf_engine): persist execution_count and interrupt wall time"
```

---

### Task 3: API serialization + `mark_first_run_scheduled` from `create_task`

**Files:**

- Modify: `wf_engine/server/routes_tasks.py`
- Modify: `tests/wf_engine/test_api_tasks.py`

- [ ] **Step 1:** Add `_active_seconds(row)` using `compute_active_duration_seconds` + `datetime.now(timezone.utc)`.

- [ ] **Step 2:** Extend `_serialize_task_detail` with `execution_count`, `active_duration_seconds`.

- [ ] **Step 3:** Replace `list_tasks` comprehension with dict including `execution_count` and `active_duration_seconds`.

- [ ] **Step 4:** After `_spawn_for_task` in `create_task`, `cp.store.mark_first_run_scheduled(tid)`.

- [ ] **Step 5:** API tests for GET detail (`execution_count == 1`) and GET list keys present.

- [ ] **Step 6:** `uv run pytest tests/wf_engine/test_api_tasks.py -v`

- [ ] **Step 7: Commit**

```bash
git add wf_engine/server/routes_tasks.py tests/wf_engine/test_api_tasks.py
git commit -m "feat(wf_engine): expose execution_count and active_duration on API"
```

---

### Task 4: Web UI

**Files:**

- Modify: `web/src/api.ts`, `web/src/App.tsx`, optionally `web/src/App.css`

- [ ] **Step 1:** Extend `TaskSummary` / `TaskDetail` types.

- [ ] **Step 2:** `formatDurationSeconds`; list row shows run count + active duration.

- [ ] **Step 3:** Detail pane: English titles/meta/buttons/confirm per spec; left + modal stay Chinese.

- [ ] **Step 4:** `npm run build` in `web/`

- [ ] **Step 5: Commit**

```bash
git add web/src/api.ts web/src/App.tsx web/src/App.css
git commit -m "feat(web): run count, active duration, English detail pane"
```

---

### Task 5: Final verification + spec link (if not already)

- [ ] **Step 1:** `uv run pytest tests/wf_engine/ -q` and `web npm run build`

- [ ] **Step 2:** Ensure spec lists **实现计划** path (this file).

- [ ] **Step 3: Commit** if spec or plan-only edits remain.

---

## Self-review (plan vs spec)

| Spec § | Task |
|--------|------|
| §1–7 | Tasks 1–5 |

No TBD placeholders; Task 1 includes full test source in this file.

---

## Execution handoff

**Plan saved to `docs/superpowers/plans/2026-05-15-task-duration-execution-count-ui.md`.**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.  
**2. Inline Execution** — single session with `executing-plans` checkpoints.

**Which approach do you want?**

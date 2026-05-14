import tempfile
import os
from pathlib import Path

import pytest
import sqlite3

from wf_engine import status as S
from wf_engine.store.sqlite import SqliteStore, _utc_iso


def test_create_task_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        tid = store.create_task(
            workflow_key="wf1",
            workflow_revision="rev-a",
            input_obj={"k": 1},
            tasks_root=str(Path(td) / "runs"),
        )
        row = store.get_task(tid)
    assert row is not None
    assert row["workflow_key"] == "wf1"
    assert row["status"] == "pending"
    assert row.get("execution_count") == 0
    assert row.get("interrupt_wall_seconds_accumulated") == 0


def test_create_task_with_name_and_context_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        name="我的任务",
        workflow_key="w",
        workflow_revision="1",
        input_obj={"a": 1},
        context_obj={"env": "dev"},
        tasks_root=str(tmp_path / "runs"),
    )
    row = store.get_task(tid)
    assert row is not None
    assert row["name"] == "我的任务"
    assert row["input_json"] == {"a": 1}
    assert row["context_json"] == {"env": "dev"}

    store.save_task_context(tid, {"env": "dev", "k": 2})
    row2 = store.get_task(tid)
    assert row2 is not None
    assert row2["context_json"] == {"env": "dev", "k": 2}


def test_create_task_rejects_duplicate_name(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    store.create_task(
        name="only-once",
        workflow_key="w",
        workflow_revision="1",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.create_task(
            name="only-once",
            workflow_key="w",
            workflow_revision="1",
            input_obj={},
            tasks_root=str(tmp_path / "runs"),
        )


def test_migrate_adds_name_and_context_columns(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            workflow_key TEXT NOT NULL,
            workflow_revision TEXT NOT NULL,
            status TEXT NOT NULL,
            input_json TEXT NOT NULL,
            tasks_root TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            interrupt_seq INTEGER NOT NULL DEFAULT 0,
            interrupt_node_id TEXT,
            interrupt_expected_schema TEXT,
            interrupt_request_extras TEXT,
            interrupt_checkpoint TEXT,
            interrupt_response_payload TEXT,
            interrupt_response_consumed INTEGER NOT NULL DEFAULT 0,
            worker_pid INTEGER,
            lease_until TEXT,
            worker_generation INTEGER NOT NULL DEFAULT 0
        )"""
    )
    conn.close()

    store = SqliteStore(db)
    store.init_schema()
    with store.connect() as c:
        cols = [str(r[1]) for r in c.execute("PRAGMA table_info(tasks)")]
    assert "name" in cols and "context_json" in cols
    assert "execution_count" in cols
    assert "interrupt_wall_seconds_accumulated" in cols
    assert "waiting_human_since" in cols


def test_reconcile_stale_worker_marks_task_stalled(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        workflow_key="wf1",
        workflow_revision="rev-a",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    store.init_task_nodes(tid, ["only"])
    store.set_task_status(tid, S.TASK_RUNNING)
    store.update_node(tid, 0, status=S.NODE_RUNNING, started_at=_utc_iso())
    store.acquire_lease(tid, os.getpid(), "2000-01-01T00:00:00Z")

    store.reconcile_stale_worker_for_task(tid)
    row = store.get_task(tid)
    assert row is not None
    assert row["status"] == S.TASK_FAILED_STALLED
    node = store.list_nodes(tid)[0]
    assert node["status"] == S.NODE_FAILED
    assert node["error_json"]["category"] == "worker_lost"


def test_mark_first_run_scheduled_sets_execution_count(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        workflow_key="wf1",
        workflow_revision="r",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    assert store.get_task(tid)["execution_count"] == 0
    store.mark_first_run_scheduled(tid)
    assert store.get_task(tid)["execution_count"] == 1


def test_prepare_rerun_increments_execution_count(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        workflow_key="wf1",
        workflow_revision="r",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    store.mark_first_run_scheduled(tid)
    store.prepare_task_for_rerun_execution(tid)
    assert store.get_task(tid)["execution_count"] == 2


def test_apply_resolve_accumulates_interrupt_wall_and_bumps_exec(tmp_path: Path) -> None:
    import time

    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        workflow_key="wf1",
        workflow_revision="r",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    store.init_task_nodes(tid, ["a"])
    store.mark_first_run_scheduled(tid)
    store.set_task_status(tid, S.TASK_RUNNING)
    store.open_interrupt(
        tid,
        node_id="a",
        expected_schema=None,
        ui=None,
        checkpoint=None,
    )
    time.sleep(1.1)
    store.apply_resolve(tid, {"x": 1})
    row = store.get_task(tid)
    assert row is not None
    assert row["status"] == S.TASK_RUNNING
    assert row["waiting_human_since"] is None
    assert row["interrupt_wall_seconds_accumulated"] >= 1
    assert row["execution_count"] == 2


def test_second_open_interrupt_flushes_pending_segment(tmp_path: Path) -> None:
    import time

    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        workflow_key="wf1",
        workflow_revision="r",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    store.init_task_nodes(tid, ["a"])
    store.mark_first_run_scheduled(tid)
    store.set_task_status(tid, S.TASK_RUNNING)
    store.open_interrupt(
        tid, node_id="a", expected_schema=None, ui=None, checkpoint=None
    )
    time.sleep(1.1)
    store.open_interrupt(
        tid, node_id="a", expected_schema=None, ui=None, checkpoint=None
    )
    row = store.get_task(tid)
    assert row is not None
    assert row["interrupt_wall_seconds_accumulated"] >= 1

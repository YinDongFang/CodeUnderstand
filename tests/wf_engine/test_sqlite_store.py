import tempfile
import os
from pathlib import Path

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

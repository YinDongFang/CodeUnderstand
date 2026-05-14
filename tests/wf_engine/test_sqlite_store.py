import json
import tempfile
import os
from pathlib import Path

import pytest
import sqlite3

from wf_engine import status as S
from wf_engine.store.sqlite import SqliteStore
from wf_engine.utils.lease import utc_iso


def test_create_task_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        tid = store.create_task(
            workflow_key="wf1",
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
input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.create_task(
            name="only-once",
            workflow_key="w",
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
input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    store.init_task_nodes(tid, ["only"])
    store.set_task_status(tid, S.TASK_RUNNING)
    store.update_node(tid, 0, status=S.NODE_RUNNING, started_at=utc_iso())
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
    assert row["execution_count"] == 1


def test_console_settings_default_empty(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    assert store.get_console_settings() == {
        "root": ".wf_engine/tasks",
        "cookie": "",
        "authorization": "",
    }


def test_console_settings_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    store.set_console_settings(root="data/t", cookie="a=b", authorization="Bearer x")
    assert store.get_console_settings() == {
        "root": "data/t",
        "cookie": "a=b",
        "authorization": "Bearer x",
    }
    store.set_console_settings(root="", cookie="", authorization="")
    assert store.get_console_settings() == {
        "root": ".wf_engine/tasks",
        "cookie": "",
        "authorization": "",
    }


def test_console_settings_merges_legacy_split_rows(tmp_path: Path) -> None:
    from wf_engine.store.sqlite import LEGACY_OPS_KEY, LEGACY_SYSTEM_KEY

    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    with store.connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
            (LEGACY_OPS_KEY, json.dumps({"cookie": "c", "authorization": "a"})),
        )
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
            (LEGACY_SYSTEM_KEY, json.dumps({"tasks_root": "/legacy"})),
        )
    assert store.get_console_settings() == {
        "root": "/legacy",
        "cookie": "c",
        "authorization": "a",
    }
    store.set_console_settings(root="/n", cookie="x", authorization="y")
    with store.connect() as c:
        n_legacy = c.execute(
            "SELECT COUNT(*) FROM settings WHERE key IN (?, ?)",
            (LEGACY_OPS_KEY, LEGACY_SYSTEM_KEY),
        ).fetchone()[0]
    assert n_legacy == 0
    assert store.get_console_settings()["root"] == "/n"


def test_console_settings_corrupt_primary_falls_back_to_legacy(tmp_path: Path) -> None:
    from wf_engine.store.sqlite import CONSOLE_SETTINGS_KEY, LEGACY_OPS_KEY

    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    with store.connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
            (CONSOLE_SETTINGS_KEY, "not-json"),
        )
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
            (LEGACY_OPS_KEY, json.dumps({"cookie": "ok", "authorization": ""})),
    )
    assert store.get_console_settings()["cookie"] == "ok"
    assert store.get_console_settings()["root"] == ".wf_engine/tasks"


def test_second_open_interrupt_flushes_pending_segment(tmp_path: Path) -> None:
    import time

    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    tid = store.create_task(
        workflow_key="wf1",
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

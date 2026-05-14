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

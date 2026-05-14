import tempfile
from pathlib import Path

from wf_engine.store.sqlite import SqliteStore


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

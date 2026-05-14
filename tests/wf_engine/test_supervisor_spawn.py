"""Supervisor spawns worker subprocess; task completes successfully."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from wf_engine import status as S
from wf_engine.utils.task_layout import task_layout
from wf_engine.store.sqlite import SqliteStore
from wf_engine.supervisor import spawn_worker


def test_supervisor_spawns_worker_and_task_succeeds() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        db = td_path / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()

        tasks_root = td_path / "tasks"
        task_id = store.create_task(
            workflow_key="supervisor_spawn_wf",
            workflow_revision="1",
            input_obj={},
            tasks_root=str(tasks_root),
        )
        tr = tasks_root / task_id
        tr.mkdir(parents=True)
        layout = task_layout(tr)
        layout.workspace.mkdir(parents=True, exist_ok=True)
        layout.zips.mkdir(parents=True, exist_ok=True)

        store.init_task_nodes(task_id, ["n1"])
        store.set_task_status(task_id, S.TASK_RUNNING)

        extra = {"PYTHONPATH": str(repo_root)}
        proc = spawn_worker(
            db_path=db,
            task_id=task_id,
            task_root=tr,
            workflow_key="supervisor_spawn_wf",
            registry_module="tests.wf_engine._registry_fixtures",
            extra_env=extra,
            cwd=repo_root,
        )
        try:
            ret = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        assert ret == 0
        assert store.get_task(task_id)["status"] == S.TASK_SUCCEEDED
        assert (layout.workspace / "touched.txt").read_text(encoding="utf-8") == "ok"
        task_log = (layout.logs / "task.log").read_text(encoding="utf-8")
        assert "[0:n1]" in task_log

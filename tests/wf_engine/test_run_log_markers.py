"""``task.log`` run boundaries written by ``run_once``."""

from __future__ import annotations

from pathlib import Path

from wf_engine import status as S
from wf_engine.engine import Engine
from wf_engine.paths import task_layout
from wf_engine.runner import run_once
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow


def test_run_once_appends_run_begin_with_generation(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()

    eng = Engine()
    wf = Workflow(key="log_wf")

    def n1(ctx):
        (ctx.node_workdir / "out.txt").write_text("x", encoding="utf-8")

    wf.add_node("a", n1, whitelist=["out.txt"])
    eng.register_workflow(wf)

    tid = store.create_task(
        workflow_key="log_wf",
        workflow_revision="1",
        input_obj={},
        tasks_root=str(tmp_path / "runs"),
    )
    tr = tmp_path / "runs" / tid
    store.init_task_nodes(tid, ["a"])
    store.set_task_status(tid, S.TASK_RUNNING)

    run_once(store=store, workflow=wf, task_id=tid, task_root=tr, worker_pid=99999)

    log_path = task_layout(tr).logs / "task.log"
    text = log_path.read_text(encoding="utf-8")
    assert "WF_ENGINE_RUN_BEGIN generation=0 pid=99999" in text
    assert tid in text

    store.reset_nodes_from_ordinal(tid, 0)
    store.prepare_task_for_rerun_execution(tid)
    run_once(store=store, workflow=wf, task_id=tid, task_root=tr, worker_pid=99999)

    text2 = log_path.read_text(encoding="utf-8")
    assert text2.count("WF_ENGINE_RUN_BEGIN") == 2
    assert "WF_ENGINE_RUN_BEGIN generation=1 pid=99999" in text2

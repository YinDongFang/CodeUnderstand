# tests/wf_engine/test_runner_serial.py
from pathlib import Path
import tempfile

from wf_engine.engine import Engine
from wf_engine.utils.task_layout import task_layout
from wf_engine.runner import run_once
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow
from wf_engine import status as S


def test_three_nodes_linear_success():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        eng = Engine()
        wf = Workflow(key="w1")

        def n1(ctx):
            (ctx.node_workdir / "x1").write_text("1", encoding="utf-8")

        def n2(ctx):
            (ctx.node_workdir / "x2").write_text("2", encoding="utf-8")

        def n3(ctx):
            (ctx.node_workdir / "x3").write_text("3", encoding="utf-8")

        wf.add_node("a", n1, whitelist=["x1"])
        wf.add_node("b", n2, whitelist=["x2"])
        wf.add_node("c", n3, whitelist=["x3"])
        eng.register_workflow(wf)
        tr = Path(td) / "tasks" / "t1"
        tr.mkdir(parents=True)
        layout = task_layout(tr)
        layout.workspace.mkdir(parents=True)
        layout.zips.mkdir(parents=True)
        tid = store.create_task(
            workflow_key="w1",
            workflow_revision="1",
            input_obj={},
            tasks_root=str(Path(td) / "tasks"),
        )
        store.init_task_nodes(tid, ["a", "b", "c"])
        store.set_task_status(tid, S.TASK_RUNNING)
        run_once(store=store, workflow=wf, task_id=tid, task_root=tr)
        assert store.get_task(tid)["status"] == S.TASK_SUCCEEDED
        for o in range(3):
            assert store.list_nodes(tid)[o]["status"] == S.NODE_SUCCESS


def test_whitelist_miss_marks_validation_failure():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        eng = Engine()
        wf = Workflow(key="w2")

        def n1(ctx):
            (ctx.node_workdir / "wrong.txt").write_text("no", encoding="utf-8")

        wf.add_node("a", n1, whitelist=["expected.txt"])
        eng.register_workflow(wf)
        tr = Path(td) / "tasks" / "t2"
        tr.mkdir(parents=True)
        layout = task_layout(tr)
        layout.workspace.mkdir(parents=True)
        layout.zips.mkdir(parents=True)
        tid = store.create_task(
            workflow_key="w2",
            workflow_revision="1",
            input_obj={},
            tasks_root=str(Path(td) / "tasks"),
        )
        store.init_task_nodes(tid, ["a"])
        store.set_task_status(tid, S.TASK_RUNNING)
        run_once(store=store, workflow=wf, task_id=tid, task_root=tr)
        assert store.get_task(tid)["status"] == S.TASK_FAILED
        node = store.list_nodes(tid)[0]
        assert node["status"] == S.NODE_FAILED
        assert node["error_json"]["category"] == "validation"

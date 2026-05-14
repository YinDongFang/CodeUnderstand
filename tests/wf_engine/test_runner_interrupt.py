# tests/wf_engine/test_runner_interrupt.py
from pathlib import Path
import tempfile

from wf_engine import status as S
from wf_engine.context import NodeContext
from wf_engine.interrupt import interrupt
from wf_engine.utils.paths import task_layout
from wf_engine.runner import run_once
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow


def test_interrupt_then_resolve_completes_node():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        wf = Workflow(key="w1")

        def n1(ctx: NodeContext):
            (ctx.node_workdir / "done").write_text("ok", encoding="utf-8")

        def n2(ctx: NodeContext):
            if ctx.human_input is not None:
                (ctx.node_workdir / "h.txt").write_text(
                    ctx.human_input["text"], encoding="utf-8"
                )
                return
            interrupt(expected_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})

        wf.add_node("a", n1, whitelist=["done"])
        wf.add_node("b", n2, whitelist=["h.txt"])
        tr = Path(td) / "tasks" / "t1"
        layout = task_layout(tr)
        layout.workspace.mkdir(parents=True)
        layout.zips.mkdir(parents=True)
        tid = store.create_task(
            workflow_key="w1",
            workflow_revision="1",
            input_obj={},
            tasks_root=str(Path(td) / "tasks"),
        )
        store.init_task_nodes(tid, ["a", "b"])
        store.set_task_status(tid, S.TASK_RUNNING)
        run_once(store=store, workflow=wf, task_id=tid, task_root=tr)
        assert store.get_task(tid)["status"] == S.TASK_WAITING_HUMAN
        store.apply_resolve(tid, {"text": "hi"})
        store.set_task_status(tid, S.TASK_RUNNING)
        run_once(store=store, workflow=wf, task_id=tid, task_root=tr)
        assert store.get_task(tid)["status"] == S.TASK_SUCCEEDED

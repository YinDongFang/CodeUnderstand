"""Runner persists task context at each node boundary."""

from pathlib import Path

from wf_engine import status as S
from wf_engine.context import NodeContext
from wf_engine.interrupt import interrupt
from wf_engine.paths import task_layout
from wf_engine.runner import run_once
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow


def test_runner_persists_context_on_business_failure(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    wf = Workflow(key="wf_ctx_fail")

    def n1(ctx: NodeContext) -> None:
        ctx.context["from_n1"] = True
        (ctx.node_workdir / "x").write_text("ok", encoding="utf-8")

    def n2(ctx: NodeContext) -> None:
        ctx.context["from_n2"] = True
        raise RuntimeError("boom")

    wf.add_node("a", n1, whitelist=["x"])
    wf.add_node("b", n2)
    tr = tmp_path / "tasks" / "t1"
    layout = task_layout(tr)
    layout.workspace.mkdir(parents=True)
    layout.zips.mkdir(parents=True)
    tid = store.create_task(
        workflow_key="wf_ctx_fail",
        workflow_revision="1",
        input_obj={"seed": 1},
        context_obj={"initial": True},
        tasks_root=str(tmp_path / "tasks"),
    )
    store.init_task_nodes(tid, ["a", "b"])
    store.set_task_status(tid, S.TASK_RUNNING)

    run_once(store=store, workflow=wf, task_id=tid, task_root=tr)

    row = store.get_task(tid)
    assert row is not None
    assert row["status"] == S.TASK_FAILED
    ctx_out = row["context_json"]
    assert ctx_out["initial"] is True
    assert ctx_out["from_n1"] is True
    assert ctx_out["from_n2"] is True
    assert row["input_json"] == {"seed": 1}


def test_ops_globals_injected_from_store(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    store.set_ops_globals("ck=1", "tok")

    wf = Workflow(key="wf_ops")

    seen: dict[str, str] = {}

    def n1(ctx: NodeContext) -> None:
        seen["cookie"] = ctx.ops_globals.get("cookie", "")
        seen["authorization"] = ctx.ops_globals.get("authorization", "")

    wf.add_node("a", n1)
    tr = tmp_path / "tasks" / "t_ops"
    layout = task_layout(tr)
    layout.workspace.mkdir(parents=True)
    layout.zips.mkdir(parents=True)
    tid = store.create_task(
        workflow_key="wf_ops",
        workflow_revision="1",
        input_obj={},
        tasks_root=str(tmp_path / "tasks"),
    )
    store.init_task_nodes(tid, ["a"])
    store.set_task_status(tid, S.TASK_RUNNING)

    run_once(store=store, workflow=wf, task_id=tid, task_root=tr)

    assert seen == {"cookie": "ck=1", "authorization": "tok"}


def test_interrupt_saves_context_before_waiting_human(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    wf = Workflow(key="wf_ctx_int")

    def n1(ctx: NodeContext) -> None:
        ctx.context["saved_before_open_interrupt"] = {"n": 7}
        interrupt(
            expected_schema={"type": "object", "properties": {"x": {"type": "integer"}}}
        )

    wf.add_node("a", n1)
    tr = tmp_path / "tasks" / "t2"
    layout = task_layout(tr)
    layout.workspace.mkdir(parents=True)
    layout.zips.mkdir(parents=True)
    tid = store.create_task(
        workflow_key="wf_ctx_int",
        workflow_revision="1",
        input_obj={},
        context_obj={"base": 0},
        tasks_root=str(tmp_path / "tasks"),
    )
    store.init_task_nodes(tid, ["a"])
    store.set_task_status(tid, S.TASK_RUNNING)

    run_once(store=store, workflow=wf, task_id=tid, task_root=tr)

    row = store.get_task(tid)
    assert row is not None
    assert row["status"] == S.TASK_WAITING_HUMAN
    ctx_out = row["context_json"]
    assert ctx_out["base"] == 0
    assert ctx_out["saved_before_open_interrupt"] == {"n": 7}

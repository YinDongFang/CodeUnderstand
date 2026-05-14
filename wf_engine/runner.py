from __future__ import annotations

from pathlib import Path

from wf_engine import status as S
from wf_engine.context import NodeContext
from wf_engine.interrupt import ControlledInterrupt
from wf_engine.paths import task_layout
from wf_engine.store.sqlite import SqliteStore, _utc_iso
from wf_engine.workflow import Workflow
from wf_engine.zip_util import pack_whitelist_zip


def run_once(
    *,
    store: SqliteStore,
    workflow: Workflow,
    task_id: str,
    task_root: Path,
) -> None:
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if task["status"] == S.TASK_WAITING_HUMAN:
        return

    layout = task_layout(task_root)
    layout.workspace.mkdir(parents=True, exist_ok=True)
    layout.zips.mkdir(parents=True, exist_ok=True)

    node_rows = store.list_nodes(task_id)
    num = len(workflow.nodes)
    if len(node_rows) != num:
        msg = f"task node count {len(node_rows)} != workflow node count {num}"
        raise ValueError(msg)

    for row in node_rows:
        ordinal = int(row["ordinal"])
        if row["status"] == S.NODE_SUCCESS:
            continue

        spec = workflow.nodes[ordinal]
        if spec.id != row["node_id"]:
            msg = f"node id mismatch at ordinal {ordinal}: db={row['node_id']!r} wf={spec.id!r}"
            raise ValueError(msg)

        t = store.get_task(task_id)
        assert t is not None
        human_input: dict | None = None
        if (
            t["interrupt_response_payload"] is not None
            and not t["interrupt_response_consumed"]
            and t.get("interrupt_node_id") == spec.id
        ):
            human_input = t["interrupt_response_payload"]

        cur = store.get_task(task_id)
        assert cur is not None
        if cur["status"] != S.TASK_RUNNING:
            store.set_task_status(task_id, S.TASK_RUNNING)

        now = _utc_iso()
        store.update_node(task_id, ordinal, status=S.NODE_RUNNING, started_at=now)

        node_workdir = layout.workspace / spec.workdir_relative
        node_workdir.mkdir(parents=True, exist_ok=True)

        ctx = NodeContext(
            task_id=task_id,
            node_id=spec.id,
            workflow_key=workflow.key,
            task_root=task_root,
            workspace=layout.workspace,
            node_workdir=node_workdir,
            human_input=human_input,
        )
        injected_human = human_input is not None

        try:
            spec.fn(ctx)
        except ControlledInterrupt as c:
            store.open_interrupt(
                task_id,
                node_id=spec.id,
                expected_schema=c.expected_schema,
                ui=c.ui,
                checkpoint=c.checkpoint,
            )
            store.update_node(
                task_id,
                ordinal,
                status=S.NODE_WAITING_HUMAN,
                clear_finished_at=True,
            )
            store.release_lease(task_id)
            return
        except Exception as e:
            store.update_node(
                task_id,
                ordinal,
                status=S.NODE_FAILED,
                finished_at=_utc_iso(),
                error_json={"category": "business", "message": str(e)},
            )
            store.set_task_status(task_id, S.TASK_FAILED)
            store.release_lease(task_id)
            return

        globs = tuple(spec.whitelist_globs)
        dest_zip = layout.zips / f"{ordinal}_{spec.id}.zip"
        if globs:
            pack_whitelist_zip(parent=node_workdir, include_globs=globs, dest_zip=dest_zip)
            zip_path_str = str(dest_zip)
        else:
            zip_path_str = None

        fin = _utc_iso()
        if zip_path_str is not None:
            store.update_node(
                task_id,
                ordinal,
                status=S.NODE_SUCCESS,
                finished_at=fin,
                zip_path=zip_path_str,
            )
        else:
            store.update_node(
                task_id,
                ordinal,
                status=S.NODE_SUCCESS,
                finished_at=fin,
            )

        if injected_human:
            store.consume_interrupt_response(task_id)

        if ordinal == num - 1:
            store.set_task_status(task_id, S.TASK_SUCCEEDED)

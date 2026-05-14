"""示例工作流：多节点、人工中断（interrupt）、节点内写 ``task.log``。

三阶段目录 ``n1`` / ``n2`` / ``n3`` 均为 workspace 下相对路径，互不影响产物白名单 zip。

声明式模块约定：
- ``WORKFLOW_KEY`` 模块级常量（默认为文件名）
- ``get_input_schema()`` 返回 JSON Schema（可选）
- ``get_nodes()`` 返回节点定义列表
"""

from __future__ import annotations

import logging

from wf_engine import NodeContext, interrupt

log = logging.getLogger(__name__)

WORKFLOW_KEY = "demo_pipeline"


def get_input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "source_label": {"type": "string", "title": "来源标签"},
            "priority": {"type": "string", "title": "优先级说明"},
        },
        "required": ["source_label"],
    }


def step_fetch(ctx: NodeContext) -> None:
    ctx.context["demo_step"] = "fetch"
    ctx.context["source_label"] = str(ctx.input.get("source_label", ""))
    log.info("[%s] 拉取输入并写入 out.txt", ctx.node_id)
    (ctx.node_workdir / "out.txt").write_text(
        "demo-seed\nline2\n",
        encoding="utf-8",
    )
    log.info("[%s] 完成", ctx.node_id)


def step_review(ctx: NodeContext) -> None:
    if ctx.human_input is not None:
        note = str(ctx.human_input.get("note", "")).strip()
        log.info("[%s] 收到人工备注，长度=%d", ctx.node_id, len(note))
        (ctx.node_workdir / "approved.txt").write_text(note + "\n", encoding="utf-8")
        log.info("[%s] 已写入 approved.txt", ctx.node_id)
        return
    log.info("[%s] 等待人工输入 (interrupt)", ctx.node_id)
    interrupt(
        expected_schema={
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
        },
        ui={"title": "审核备注", "hint": "POST /interrupt/resolve 的 payload.note"},
    )


def step_summary(ctx: NodeContext) -> None:
    log.info("[%s] 汇总上下游文件", ctx.node_id)
    raw = (ctx.workspace / "n1" / "out.txt").read_text(encoding="utf-8")
    approval = (ctx.workspace / "n2" / "approved.txt").read_text(encoding="utf-8")
    text = f"--- upstream ---\n{raw}\n--- note ---\n{approval}\n"
    (ctx.node_workdir / "summary.txt").write_text(text, encoding="utf-8")
    log.info("[%s] 已生成 summary.txt (%d bytes)", ctx.node_id, len(text.encode("utf-8")))


def get_nodes() -> list[dict]:
    return [
        {"id": "fetch", "fn": step_fetch, "workdir": "n1", "whitelist": ["out.txt"]},
        {"id": "review", "fn": step_review, "workdir": "n2", "whitelist": ["approved.txt"]},
        {"id": "summary", "fn": step_summary, "workdir": "n3", "whitelist": ["summary.txt"]},
    ]

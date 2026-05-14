"""build 宏阶段：可选会话题目写回 → export_session → zip。"""
from __future__ import annotations

import argparse
import json
import os
import sys

from cu.env import stage_env
from cu.paths import artifact_root, sandbox_home
from cu.pipeline.export_session import export_session
from cu.pipeline.rewrite import rewrite_from_stdin_lines
from cu.runtime.archive import archive
from cu.session_questions import extract_question_lines

from cu.stages._common import repo_root, session_jsonl_path
from cu.stages.context import JobContext


def run_rewrite_stdin_mirror(ctx: JobContext, _env: dict[str, str]) -> None:
    """将当前会话中已抽取的题目经 stdin JSON 等价逻辑写回 ``cu.pipeline.rewrite``。"""
    path = session_jsonl_path(ctx.claude_project_dir, ctx.session_id)
    lines = extract_question_lines(path)
    if not lines:
        return
    try:
        rewrite_from_stdin_lines(ctx.repo, lines)
    except Exception as e:
        raise RuntimeError(f"[build/rewrite] {e}") from e


def run_build(ctx: JobContext) -> None:
    ctx.fire("build", "start")
    art = artifact_root(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
        claude_project_dir=ctx.claude_project_dir,
    )
    env["ARTIFACT_ROOT"] = art

    if ctx.build_with_rewrite:
        ctx.fire("build", "step:rewrite")
        run_rewrite_stdin_mirror(ctx, env)

    ctx.fire("build", "step:export-session")
    try:
        export_session(
            artifact_root=art,
            repo=ctx.repo,
            session_id=ctx.session_id,
            sandbox_home_path=sandbox_home(ctx.job_id),
            claude_project_dir=ctx.claude_project_dir or "",
        )
    except Exception as e:
        raise RuntimeError(f"[build/export-session] {e}") from e

    ctx.fire("build", "step:zip")
    try:
        archive(art)
    except Exception as e:
        raise RuntimeError(f"[build/zip] {e}") from e
    ctx.fire("build", "done")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="运行 build 宏阶段（单阶段调试入口）")
    ap.add_argument("spec", help="JSON：{\"ctx\": {...}}")
    args = ap.parse_args(argv)
    raw = json.loads(open(args.spec, encoding="utf-8").read())
    ctx = JobContext(**raw["ctx"])
    try:
        run_build(ctx)
    except Exception as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

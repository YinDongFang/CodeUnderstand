"""conversation 宏阶段：loop → pipeline clean → build-doc。"""
from __future__ import annotations

import argparse
import json
import sys

from cu.env import stage_env
from cu.paths import code_dir, sandbox_home
from cu.pipeline.clean import run_dedupe_session
from cu.runner import run_module

from cu.stages._common import check, find_claude_project_dir, repo_root
from cu.stages.context import JobContext


def run_conversation(ctx: JobContext) -> None:
    ctx.fire("conversation", "start")
    root = repo_root()
    target_path = code_dir(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
    )

    ctx.fire("conversation", "step:loop")
    result = run_module(
        "cu.pipeline.loop",
        [target_path, ctx.repo],
        env=env,
        cwd=root,
        pid_sink=ctx.pid_sink,
    )
    check(result, "conversation", "loop")

    if not ctx.session_id:
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError("[conversation/loop] 未从 stdout 解析到 session_id")
        ctx.session_id = lines[-1]

    sandbox = sandbox_home(ctx.job_id)
    found = find_claude_project_dir(sandbox, ctx.session_id)
    if not found:
        raise RuntimeError(
            f"[conversation/loop] 未在 {sandbox}/.claude/projects/ 下找到 "
            f"包含 {ctx.session_id}.jsonl 的目录"
        )
    ctx.claude_project_dir = found

    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
        claude_project_dir=ctx.claude_project_dir,
    )

    ctx.fire("conversation", "step:clean")
    try:
        run_dedupe_session(ctx.repo, ctx.session_id)
    except Exception as e:
        raise RuntimeError(f"[conversation/clean] {e}") from e

    ctx.fire("conversation", "step:build-doc")
    build_env = dict(env)
    build_env["BUILD_DOC_ONLY"] = "1"
    result = run_module(
        "cu.pipeline.build_docs",
        [ctx.repo, ctx.session_id],
        env=build_env,
        cwd=root,
        stream=True,
        pid_sink=ctx.pid_sink,
    )
    check(result, "conversation", "build-doc")
    ctx.fire("conversation", "done")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="运行 conversation 宏阶段（单阶段调试入口）")
    ap.add_argument("spec", help="JSON：{\"ctx\": {...}}")
    args = ap.parse_args(argv)
    raw = json.loads(open(args.spec, encoding="utf-8").read())
    ctx = JobContext(**raw["ctx"])
    try:
        run_conversation(ctx)
    except Exception as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

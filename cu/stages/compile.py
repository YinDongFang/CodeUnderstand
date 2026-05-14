"""compile 宏阶段：metadata → clean-artifacts → 删除 .git。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from cu.env import stage_env
from cu.paths import artifact_root, code_dir
from cu.pipeline.clean_artifacts import clean_artifact_tree
from cu.pipeline.metadata import run_metadata

from cu.stages._common import repo_root
from cu.stages.context import JobContext


def run_compile(ctx: JobContext) -> None:
    ctx.fire("compile", "start")
    layout_root = repo_root()
    art = artifact_root(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
        claude_project_dir=ctx.claude_project_dir,
    )
    env["ARTIFACT_ROOT"] = art

    ctx.fire("compile", "step:metadata")
    try:
        run_metadata(
            artifact_root=art,
            github_url=ctx.github_url,
            repo=ctx.repo,
            repo_layout_root=layout_root,
            env=env,
        )
    except Exception as e:
        raise RuntimeError(f"[compile/metadata] {e}") from e

    ctx.fire("compile", "step:clean-artifacts")
    try:
        clean_artifact_tree(art)
    except Exception as e:
        raise RuntimeError(f"[compile/clean-artifacts] {e}") from e

    ctx.fire("compile", "step:remove-git")
    git_dir = os.path.join(code_dir(ctx.job_id, ctx.repo), ".git")
    if os.path.isdir(git_dir):
        try:
            shutil.rmtree(git_dir)
        except OSError as e:
            raise RuntimeError(
                f"[compile/cleanup] 删除 .git 失败: {git_dir}: {e}"
            ) from e
    ctx.fire("compile", "done")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="运行 compile 宏阶段（单阶段调试入口）")
    ap.add_argument("spec", help="JSON：{\"ctx\": {...}}")
    args = ap.parse_args(argv)
    raw = json.loads(open(args.spec, encoding="utf-8").read())
    ctx = JobContext(**raw["ctx"])
    try:
        run_compile(ctx)
    except Exception as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

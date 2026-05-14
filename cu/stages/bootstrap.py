"""bootstrap 宏阶段：沙箱 + 下载 ZIP。"""
from __future__ import annotations

import argparse
import json
import os
import sys

from cu.env import stage_env
from cu.paths import artifact_root
from cu.runtime.download import download_github_zip
from cu.sandbox import bootstrap_sandbox

from cu.stages._common import repo_root
from cu.stages.context import JobContext


def run_bootstrap(ctx: JobContext) -> None:
    ctx.fire("bootstrap", "start")
    bootstrap_sandbox(ctx.job_id)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        github_url=ctx.github_url,
    )
    art = artifact_root(ctx.job_id, ctx.repo)
    projects_dir = env["PROJECTS_DIR"]
    os.makedirs(projects_dir, exist_ok=True)

    ctx.fire("bootstrap", "step:download")
    try:
        download_github_zip(ctx.zip_url, projects_dir)
    except Exception as e:
        raise RuntimeError(f"[bootstrap/download] {e}") from e
    ctx.fire("bootstrap", "done")


def main(argv: list[str] | None = None) -> int:
    """CLI：``python -m cu.stages.bootstrap <spec.json>`` —— 仅用于调试单阶段。"""
    ap = argparse.ArgumentParser(description="运行 bootstrap 宏阶段（单阶段调试入口）")
    ap.add_argument("spec", help="JSON：{\"ctx\": {... JobContext 字段 ...}}")
    args = ap.parse_args(argv)
    raw = json.loads(open(args.spec, encoding="utf-8").read())
    ctx = JobContext(**raw["ctx"])
    try:
        run_bootstrap(ctx)
    except Exception as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

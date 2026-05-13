"""CLI 入口：python -m cu run <zip_url>"""
from __future__ import annotations

import argparse
import re
import sys
import uuid

from cu.stages import JobContext, STAGES, STAGE_RUNNERS


_ZIP_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip$"
)


def _parse_github_url(zip_url: str) -> tuple[str, str, str]:
    """从 GitHub archive zip URL 解析 (user, repo, branch)。"""
    m = _ZIP_URL_RE.match(zip_url)
    if not m:
        raise ValueError(f"URL 不符合 GitHub archive ZIP 格式: {zip_url}")
    return m.group(1), m.group(2), m.group(3)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        gh_user, repo, _branch = _parse_github_url(args.zip_url)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    job_id = args.job_id or f"{repo}-{uuid.uuid4().hex[:8]}"
    github_url = f"https://github.com/{gh_user}/{repo}"

    ctx = JobContext(
        job_id=job_id,
        repo=repo,
        zip_url=args.zip_url,
        github_url=github_url,
    )

    start = STAGES.index(args.start) if args.start else 0
    end = STAGES.index(args.end) + 1 if args.end else len(STAGES)

    for stage_name in STAGES[start:end]:
        print(f"\n{'=' * 60}")
        print(f"  Stage: {stage_name}  job_id={job_id}")
        print(f"{'=' * 60}")
        try:
            STAGE_RUNNERS[stage_name](ctx)
            print(f"  [OK] {stage_name} 完成")
        except RuntimeError as e:
            print(f"  [FAIL] {stage_name} 失败:\n{e}", file=sys.stderr)
            return 1

    print(f"\n作业 {job_id} 全部完成。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="cu", description="代码质检工作流 CLI")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="执行完整或部分流水线")
    run_p.add_argument("zip_url", help="GitHub archive ZIP URL")
    run_p.add_argument("--job-id", help="自定义 job ID（默认自动生成）")
    run_p.add_argument("--start", choices=list(STAGES), help="从指定阶段开始")
    run_p.add_argument("--end", choices=list(STAGES), help="在指定阶段结束")

    args = parser.parse_args()
    if args.command == "run":
        return cmd_run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

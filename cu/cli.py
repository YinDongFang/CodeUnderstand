"""CLI 入口：python -m cu <command>

子命令：
  run     创建并执行作业（阻塞等待结果）
  list    列出所有作业
  show    作业详情（含 4 阶段状态）
  rerun   从指定阶段重跑（恢复快照）
  delete  删除作业（取消运行 + 清沙箱与快照 + 删 DB 记录）
  serve   启动 REST API 服务（uvicorn）
"""
from __future__ import annotations

import argparse
import sys

from cu import orchestrator as orch
from cu.models import JOB_STAGES


def _print_event(stage: str, message: str) -> None:
    print(f"  [{stage:<12}] {message}", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        rec = orch.create_job(args.zip_url, job_id=args.job_id)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    print(f"创建作业 {rec.job_id}  repo={rec.repo}")
    start = args.start or "bootstrap"
    end = args.end
    if end is not None and JOB_STAGES.index(end) < JOB_STAGES.index(start):
        print("错误: --end 不能早于 --start", file=sys.stderr)
        return 1
    try:
        t = orch.run_stage(
            rec.job_id, start, end_stage=end, on_event=_print_event,
        )
    except (KeyError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    t.join()

    job = orch.get_job(rec.job_id)
    print(f"\n作业 {rec.job_id} 终态：{job.status}")
    if job.status != "success" and job.notes:
        print(f"错误摘要：{job.notes[:500]}", file=sys.stderr)

    # 部分阶段跑完后 job.status 为 pending；以区间内各阶段是否 success 判定 CLI 退出码
    if end is not None and JOB_STAGES.index(end) < len(JOB_STAGES) - 1:
        stages = orch.get_stages(rec.job_id)
        si = JOB_STAGES.index(start)
        ei = JOB_STAGES.index(end)
        ok = all(
            stages[JOB_STAGES[i]].status == "success"
            for i in range(si, ei + 1)
        )
        return 0 if ok else 1
    return 0 if job.status == "success" else 1


def cmd_list(args: argparse.Namespace) -> int:
    rows = orch.list_jobs(status=args.status)
    if not rows:
        print("（无作业）")
        return 0
    print(f"{'job_id':<32}  {'repo':<24}  {'status':<10}  created_at")
    print("-" * 92)
    for r in rows:
        print(f"{r.job_id:<32}  {r.repo:<24}  {r.status:<10}  {r.created_at}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    job = orch.get_job(args.job_id)
    if job is None:
        print(f"错误: 作业不存在: {args.job_id}", file=sys.stderr)
        return 1
    print(f"job_id   : {job.job_id}")
    print(f"repo     : {job.repo}")
    print(f"status   : {job.status}")
    print(f"created  : {job.created_at}")
    print(f"updated  : {job.updated_at}")
    print(f"session  : {job.session_id or '(none)'}")
    if job.notes:
        print(f"notes    : {job.notes[:300]}")
    print()
    print(f"{'stage':<14}  {'status':<10}  {'attempt':<8}  duration")
    print("-" * 70)
    stages = orch.get_stages(args.job_id)
    for s in JOB_STAGES:
        sr = stages[s]
        duration = ""
        if sr.started_at and sr.ended_at:
            duration = f"{sr.started_at} -> {sr.ended_at}"
        elif sr.started_at:
            duration = f"{sr.started_at} -> ?"
        print(f"{s:<14}  {sr.status:<10}  {sr.attempt:<8}  {duration}")
        if sr.status == "failed" and sr.log_tail:
            print(f"  log_tail: {sr.log_tail[:300]}")
    return 0


def cmd_rerun(args: argparse.Namespace) -> int:
    try:
        t = orch.rerun_from(args.job_id, args.stage, on_event=_print_event)
    except KeyError as e:
        print(f"错误: 作业不存在: {e}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    t.join()
    job = orch.get_job(args.job_id)
    print(f"\n作业 {args.job_id} 终态：{job.status}")
    return 0 if job.status == "success" else 1


def cmd_cancel(args: argparse.Namespace) -> int:
    ok = orch.cancel(args.job_id)
    if not ok:
        print(f"无运行中作业: {args.job_id}", file=sys.stderr)
        return 1
    print(f"已请求取消 {args.job_id}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    ok = orch.delete_job(args.job_id)
    if ok:
        print(f"已删除作业 {args.job_id}")
        return 0
    print(f"错误: 作业不存在: {args.job_id}", file=sys.stderr)
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    from cu.serve import main as serve_main
    return serve_main(host=args.host, port=args.port)


def main() -> int:
    parser = argparse.ArgumentParser(prog="cu", description="代码质检工作流 CLI")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="创建并执行作业")
    p_run.add_argument("zip_url", help="GitHub archive ZIP URL")
    p_run.add_argument("--job-id", help="自定义 job ID（默认 <repo>-<uuid8>）")
    p_run.add_argument("--start", choices=list(JOB_STAGES), help="从指定阶段开始")
    p_run.add_argument(
        "--end",
        choices=list(JOB_STAGES),
        default=None,
        help="在指定阶段结束（含）；缺省一直跑到 build",
    )

    p_list = sub.add_parser("list", help="列出所有作业")
    p_list.add_argument("--status", help="按状态过滤")

    p_show = sub.add_parser("show", help="作业详情")
    p_show.add_argument("job_id")

    p_rerun = sub.add_parser("rerun", help="从某阶段重跑（恢复快照）")
    p_rerun.add_argument("job_id")
    p_rerun.add_argument("stage", choices=list(JOB_STAGES))

    p_cancel = sub.add_parser("cancel", help="取消运行中作业")
    p_cancel.add_argument("job_id")

    p_del = sub.add_parser("delete", help="删除作业（含沙箱与快照）")
    p_del.add_argument("job_id")

    p_serve = sub.add_parser("serve", help="启动 REST API 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()
    handlers = {
        "run": cmd_run,
        "list": cmd_list,
        "show": cmd_show,
        "rerun": cmd_rerun,
        "cancel": cmd_cancel,
        "delete": cmd_delete,
        "serve": cmd_serve,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

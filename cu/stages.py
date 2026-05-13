"""4 宏阶段定义与执行逻辑。

阶段序：bootstrap → conversation → compile → build
每个阶段函数接收 JobContext，调用 bash/Python 子进程并返回成功；失败抛 RuntimeError。

子进程环境由 ``cu.env.stage_env()`` 组装。设置 ``CU_TEST_MODE=1``（见 ``cu.test_mode``）时，将注入
``LOOP_USE_MOCK_CLAUDE`` / ``BUILD_USE_MOCK_CLAUDE`` / ``CLASSIFY_USE_MOCK_CLAUDE``，使 loop、doc 生成与
分类等 Claude CLI 调用统一走 ``testing/mock_claude.sh``。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Callable, Optional

import json

from cu.paths import artifact_root, code_dir, sandbox_home
from cu.sandbox import bootstrap_sandbox
from cu.env import stage_env
from cu.runner import run_script, run_python, RunResult


STAGES = ("bootstrap", "conversation", "compile", "build")


EventCallback = Optional[Callable[[str, str], None]]   # (stage, message)
PidSink = Optional[Callable[[int], None]]


@dataclass
class JobContext:
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str = ""
    claude_project_dir: str = ""
    on_event: EventCallback = None
    pid_sink: PidSink = None
    #: API 驱动的 ``build`` 为 True：该段内先跑一次 ``rewrite.py --stdin-lines``（与 ``cu run`` 默认跳过不同）。
    build_with_rewrite: bool = False

    def fire(self, stage: str, message: str) -> None:
        if self.on_event is not None:
            self.on_event(stage, message)


def _repo_root() -> str:
    """本仓库的根目录（cu/ 的父目录）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_claude_project_dir(sandbox_home_path: str, session_id: str) -> str:
    """在沙箱 ~/.claude/projects/ 下搜寻含有 {session_id}.jsonl 的目录。

    返回该目录绝对路径；找不到返回空串。仅看顶层 .jsonl，不进入 subagents 子目录。
    """
    root = os.path.join(sandbox_home_path, ".claude", "projects")
    if not os.path.isdir(root):
        return ""
    target = f"{session_id}.jsonl"
    for entry in os.listdir(root):
        proj = os.path.join(root, entry)
        if not os.path.isdir(proj):
            continue
        if os.path.isfile(os.path.join(proj, target)):
            return proj
    return ""


def _check(result: RunResult, stage: str, step: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"[{stage}/{step}] exit={result.returncode}\n"
            f"stdout(tail): {result.stdout[-2000:]}\n"
            f"stderr(tail): {result.stderr[-2000:]}"
        )


def run_bootstrap(ctx: JobContext) -> None:
    ctx.fire("bootstrap", "start")
    repo_root = _repo_root()
    bootstrap_sandbox(ctx.job_id)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        github_url=ctx.github_url,
    )
    art = artifact_root(ctx.job_id, ctx.repo)
    os.makedirs(os.path.join(art, "code"), exist_ok=True)

    ctx.fire("bootstrap", "step:download")
    result = run_script(
        os.path.join(repo_root, "download.sh"),
        args=[ctx.zip_url],
        env=env,
        cwd=repo_root,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "bootstrap", "download")
    ctx.fire("bootstrap", "done")


def run_conversation(ctx: JobContext) -> None:
    ctx.fire("conversation", "start")
    repo_root = _repo_root()
    target_path = code_dir(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
    )

    ctx.fire("conversation", "step:loop")
    result = run_script(
        os.path.join(repo_root, "loop.sh"),
        args=[target_path, ctx.repo],
        env=env,
        cwd=repo_root,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "conversation", "loop")

    if not ctx.session_id:
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError("[conversation/loop] 未从 stdout 解析到 session_id")
        ctx.session_id = lines[-1]

    sandbox = sandbox_home(ctx.job_id)
    found = _find_claude_project_dir(sandbox, ctx.session_id)
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
    result = run_python(
        os.path.join(repo_root, "clean.py"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
        stream=True,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "conversation", "clean")

    ctx.fire("conversation", "step:build-doc")
    build_env = dict(env)
    build_env["BUILD_DOC_ONLY"] = "1"
    result = run_script(
        os.path.join(repo_root, "build.sh"),
        args=[ctx.repo, ctx.session_id],
        env=build_env,
        cwd=repo_root,
        stream=True,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "conversation", "build-doc")
    ctx.fire("conversation", "done")


def run_compile(ctx: JobContext) -> None:
    ctx.fire("compile", "start")
    repo_root = _repo_root()
    art = artifact_root(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
        claude_project_dir=ctx.claude_project_dir,
    )
    env["ARTIFACT_ROOT"] = art

    ctx.fire("compile", "step:metadata")
    result = run_script(
        os.path.join(repo_root, "scripts", "metadata.sh"),
        args=[ctx.github_url, ctx.repo],
        env=env,
        cwd=repo_root,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "compile", "metadata")

    ctx.fire("compile", "step:clean-artifacts")
    result = run_script(
        os.path.join(repo_root, "scripts", "clean_artifacts.sh"),
        args=[art],
        env=env,
        cwd=repo_root,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "compile", "clean-artifacts")

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


def _session_jsonl_path(ctx: JobContext) -> str:
    if not ctx.claude_project_dir or not ctx.session_id:
        raise RuntimeError("[build] 缺少 claude_project_dir 或 session_id")
    sid = ctx.session_id
    if sid.lower().endswith(".jsonl"):
        sid = sid[:-6]
    p = os.path.join(ctx.claude_project_dir, f"{sid}.jsonl")
    if not os.path.isfile(p):
        raise RuntimeError(f"[build] 会话 JSONL 不存在: {p}")
    return p


def run_rewrite_stdin_mirror(ctx: JobContext, env: dict[str, str]) -> None:
    """将当前会话中已抽取的题目经 stdin JSON 投喂 ``rewrite.py``，通常为幂等等同写回。"""
    from cu.session_questions import extract_question_lines

    path = _session_jsonl_path(ctx)
    lines = extract_question_lines(path)
    if not lines:
        return
    stdin_payload = json.dumps({"lines": lines}, ensure_ascii=False)
    repo_root = _repo_root()
    result = run_python(
        os.path.join(repo_root, "rewrite.py"),
        args=[ctx.repo, "--single-source", "--non-interactive", "--stdin-lines"],
        env=env,
        cwd=repo_root,
        pid_sink=ctx.pid_sink,
        stdin_data=stdin_payload,
    )
    _check(result, "build", "rewrite")


def run_build(ctx: JobContext) -> None:
    """build 阶段：可选 ``rewrite.py``（仅 API Web build）→ ``export_session`` → zip。

    ``cu run`` 全流程中 ``JobContext.build_with_rewrite`` 为 False，跳过 rewrite，
    与 P2 CLI 语义一致。
    """
    ctx.fire("build", "start")
    repo_root = _repo_root()
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
    result = run_script(
        os.path.join(repo_root, "scripts", "export_session.sh"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "build", "export-session")

    ctx.fire("build", "step:zip")
    result = run_script(
        os.path.join(repo_root, "zip.sh"),
        args=[art],
        env=env,
        cwd=repo_root,
        pid_sink=ctx.pid_sink,
    )
    _check(result, "build", "zip")
    ctx.fire("build", "done")


STAGE_RUNNERS = {
    "bootstrap": run_bootstrap,
    "conversation": run_conversation,
    "compile": run_compile,
    "build": run_build,
}

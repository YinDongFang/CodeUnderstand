"""4 宏阶段定义与执行逻辑。

阶段序：bootstrap → conversation → compile → build
每个阶段函数接收 JobContext，调用 bash/python 子进程并返回成功；失败抛 RuntimeError。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from cu.paths import artifact_root, code_dir, sandbox_home
from cu.sandbox import bootstrap_sandbox
from cu.env import stage_env
from cu.runner import run_script, run_python, RunResult


STAGES = ("bootstrap", "conversation", "compile", "build")


@dataclass
class JobContext:
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str = ""
    claude_project_dir: str = ""


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
    repo_root = _repo_root()
    bootstrap_sandbox(ctx.job_id)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        github_url=ctx.github_url,
    )
    art = artifact_root(ctx.job_id, ctx.repo)
    os.makedirs(os.path.join(art, "code"), exist_ok=True)

    result = run_script(
        os.path.join(repo_root, "download.sh"),
        args=[ctx.zip_url],
        env=env,
        cwd=repo_root,
    )
    _check(result, "bootstrap", "download")


def run_conversation(ctx: JobContext) -> None:
    repo_root = _repo_root()
    target_path = code_dir(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
    )

    result = run_script(
        os.path.join(repo_root, "loop.sh"),
        args=[target_path, ctx.repo],
        env=env,
        cwd=repo_root,
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

    result = run_python(
        os.path.join(repo_root, "clean.py"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
    )
    _check(result, "conversation", "clean")

    build_env = dict(env)
    build_env["BUILD_DOC_ONLY"] = "1"
    result = run_script(
        os.path.join(repo_root, "build.sh"),
        args=[ctx.repo, ctx.session_id],
        env=build_env,
        cwd=repo_root,
    )
    _check(result, "conversation", "build-doc")


def run_compile(ctx: JobContext) -> None:
    repo_root = _repo_root()
    art = artifact_root(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
        claude_project_dir=ctx.claude_project_dir,
    )
    env["ARTIFACT_ROOT"] = art

    result = run_script(
        os.path.join(repo_root, "scripts", "metadata.sh"),
        args=[ctx.github_url, ctx.repo],
        env=env,
        cwd=repo_root,
    )
    _check(result, "compile", "metadata")

    result = run_script(
        os.path.join(repo_root, "scripts", "clean_artifacts.sh"),
        args=[art],
        env=env,
        cwd=repo_root,
    )
    _check(result, "compile", "clean-artifacts")

    git_dir = os.path.join(code_dir(ctx.job_id, ctx.repo), ".git")
    if os.path.isdir(git_dir):
        try:
            shutil.rmtree(git_dir)
        except OSError as e:
            raise RuntimeError(
                f"[compile/cleanup] 删除 .git 失败: {git_dir}: {e}"
            ) from e


def run_build(ctx: JobContext) -> None:
    repo_root = _repo_root()
    art = artifact_root(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
        claude_project_dir=ctx.claude_project_dir,
    )
    env["ARTIFACT_ROOT"] = art

    result = run_python(
        os.path.join(repo_root, "rewrite.py"),
        args=[ctx.repo, "--single-source", "--non-interactive"],
        env=env,
        cwd=repo_root,
    )
    _check(result, "build", "rewrite")

    result = run_script(
        os.path.join(repo_root, "scripts", "export_session.sh"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
    )
    _check(result, "build", "export-session")

    result = run_script(
        os.path.join(repo_root, "zip.sh"),
        args=[art],
        env=env,
        cwd=repo_root,
    )
    _check(result, "build", "zip")


STAGE_RUNNERS = {
    "bootstrap": run_bootstrap,
    "conversation": run_conversation,
    "compile": run_compile,
    "build": run_build,
}

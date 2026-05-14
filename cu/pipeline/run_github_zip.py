"""本地一条龙：ZIP URL → download → evaluate → loop → git 清理 → dedupe → build → pack。"""
from __future__ import annotations

import getpass
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from cu.logging_config import configure_logging, line_formatter
from cu.pipeline.clean import main_cli as dedupe_main_cli
from cu.pipeline.evaluate import evaluate_repo_difficulty
from cu.pipeline.pack import run_pack
from cu.pipeline_env import github_web_url, resolve_projects_dir, resolve_state_root
from cu.runner import run_module
from cu.runtime.download import download_github_zip, parse_github_archive_zip

_log = logging.getLogger(__name__)


def _chmod777(repo_root: Path) -> None:
    try:
        subprocess.run(
            ["chmod", "-R", "777", str(repo_root)],
            check=False,
            timeout=3600,
        )
    except FileNotFoundError:
        pass


def run_github_zip_pipeline(zip_url: str, *, repo_root: Path | None = None) -> None:
    """须在仓库根目录执行（便于 git pull / PYTHONPATH）。"""
    configure_logging()
    rr = (repo_root or Path.cwd()).resolve()
    zip_url = zip_url.strip()

    owner, repo, _branch = parse_github_archive_zip(zip_url)

    state_root = resolve_state_root()
    projects_dir = resolve_projects_dir()

    log_dir = state_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{repo}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.log"

    gh_web = github_web_url(owner, repo)

    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(line_formatter())
    _log.addHandler(fh)
    try:
        _log.info("日志文件: %s", log_file)

        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=rr, check=True)
        subprocess.run(["git", "pull"], cwd=rr, check=True)
        _chmod777(rr)

        target_path = projects_dir / repo

        _log.info("解析: %s/%s", owner, repo)
        _log.info("目标目录: %s", target_path)

        if not target_path.is_dir():
            _log.info("目标目录不存在，调用 download …")
            download_github_zip(zip_url, str(projects_dir))

        if not target_path.is_dir():
            raise FileNotFoundError(f"下载后仍不存在目录: {target_path}")

        difficulty = evaluate_repo_difficulty(str(target_path))
        _log.info("evaluate → difficulty=%s", difficulty)
        if difficulty == "easy":
            _log.info("easy 项目，跳过后续并退出。")
            return

        env = os.environ.copy()
        env.setdefault(
            "USER",
            os.environ.get("USER")
            or os.environ.get("LOGNAME")
            or os.environ.get("USERNAME")
            or getpass.getuser(),
        )

        r_loop = run_module(
            "cu.pipeline.loop",
            [str(target_path), repo],
            cwd=str(rr),
            env=env,
        )
        if r_loop.stdout:
            fh.stream.write(r_loop.stdout)
            if not r_loop.stdout.endswith("\n"):
                fh.stream.write("\n")
        if r_loop.stderr:
            fh.stream.write(r_loop.stderr)
            if not r_loop.stderr.endswith("\n"):
                fh.stream.write("\n")
        fh.flush()
        sys.stderr.write(r_loop.stdout + r_loop.stderr)
        if r_loop.returncode != 0:
            raise RuntimeError(f"loop 失败 rc={r_loop.returncode}")

        lines = [ln.strip() for ln in r_loop.stdout.splitlines() if ln.strip()]
        session_id = lines[-1] if lines else ""
        if not session_id:
            raise RuntimeError("未取得 session_id")
        _log.info("SESSION_ID=%s", session_id)

        _log.info("全部题目完成，开始清理 git 状态")
        git_dir = target_path / ".git"
        if git_dir.is_dir():
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=target_path, check=True)
            shutil.rmtree(git_dir)
            _log.info("已 reset 并移除 .git")
        else:
            _log.info("未找到 .git，跳过清理")

        _log.info("调用 clean 去重会话（target=%s session=%s）", repo, session_id)
        rc_d = dedupe_main_cli([repo, session_id])
        if rc_d != 0:
            raise RuntimeError("clean / dedupe 执行失败")

        r_build = run_module(
            "cu.pipeline.build_docs",
            [repo, session_id],
            cwd=str(rr),
            env=env,
        )
        if r_build.stdout:
            fh.stream.write(r_build.stdout)
        if r_build.stderr:
            fh.stream.write(r_build.stderr)
        fh.flush()
        sys.stderr.write(r_build.stdout + r_build.stderr)
        if r_build.returncode != 0:
            raise RuntimeError(f"build_docs 失败 rc={r_build.returncode}")

        _log.info("调用 pack …")
        run_pack(gh_web, repo, session_id)

        _log.info("任务完成")
    finally:
        _log.removeHandler(fh)
        fh.close()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 1:
        print(
            "用法: python -m cu.pipeline.run_github_zip <GitHub ZIP URL>",
            file=sys.stderr,
        )
        return 1
    rr = Path.cwd().resolve()
    try:
        run_github_zip_pipeline(argv[0], repo_root=rr)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

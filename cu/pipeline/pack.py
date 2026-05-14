"""复制代码 → metadata → rewrite → zip。"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

from cu.logging_config import configure_logging
from cu.pipeline.classify import classify_main_type
from cu.pipeline.evaluate import evaluate_repo_difficulty
from cu.pipeline.metadata import fetch_github_main_language
from cu.pipeline.rewrite import main_cli as rewrite_main_cli
from cu.pipeline_env import resolve_outputs_dir, resolve_projects_dir
from cu.runtime.archive import archive

_log = logging.getLogger(__name__)


def _out(msg: str) -> None:
    _log.info("%s", msg)


def run_pack(github_url: str, repo: str, session_id: str) -> None:
    github_url = github_url.strip()
    repo = repo.strip()
    session_id = session_id.strip()
    if not github_url or not repo or not session_id:
        raise ValueError("github / repo / session 不能为空")
    if "/" in repo or ".." in repo:
        raise ValueError("repo 名称非法")

    outputs_dir = resolve_outputs_dir()
    projects_dir = resolve_projects_dir()

    project_path = projects_dir / repo
    if not project_path.is_dir():
        raise FileNotFoundError(f"项目目录不存在: {project_path}")

    out = outputs_dir / f"code-understand-{repo}"
    meta_path = out / "metadata.json"
    code_dst_parent = out / "code"
    code_dir = code_dst_parent / repo

    _out("======================================================")
    _out("=                    Start Package                   =")
    _out("======================================================")

    _out("====================步骤 1：复制项目代码====================")
    if code_dst_parent.exists():
        shutil.rmtree(code_dst_parent)
    code_dst_parent.mkdir(parents=True)
    shutil.copytree(project_path, code_dir)
    _out("代码复制完成")

    _out("====================步骤 2：生成 metadata.json====================")
    main_language = fetch_github_main_language(github_url)
    _out(f"main_language: {main_language}")

    difficulty_level = evaluate_repo_difficulty(str(project_path))
    _out(f"difficulty_level: {difficulty_level}")

    rr = str(Path.cwd().resolve())
    main_type = classify_main_type(
        str(code_dir),
        github_url,
        env=os.environ.copy(),
        repo_root=rr,
    )
    _out(f"main_type: {main_type}")

    meta = {
        "basic_info": {
            "repo_name": github_url,
            "main_language": main_language,
            "github": {"url": github_url, "star": 0},
        },
        "category_info": {"main_type": main_type},
        "difficulty": {"level": difficulty_level},
        "extra_info": {"input_token": 0, "output_token": 0},
    }
    out.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")

    (out / "questions.json").write_text("[]\n", encoding="utf-8")

    _out("====================步骤 3：rewrite 会话 JSONL====================")
    prev_sid = os.environ.get("SESSION_ID")
    os.environ["SESSION_ID"] = session_id
    try:
        rc = rewrite_main_cli([repo])
    finally:
        if prev_sid is None:
            os.environ.pop("SESSION_ID", None)
        else:
            os.environ["SESSION_ID"] = prev_sid
    if rc != 0:
        raise RuntimeError("rewrite 退出非零")

    _out("====================步骤 3（续）：压缩输出目录====================")
    archive(str(out.resolve()))

    _out("======================================================")
    _out("=                    Package Done                    =")
    _out("======================================================")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 3:
        print(
            "用法: python -m cu.pipeline.pack <github> <repo> <session>",
            file=sys.stderr,
        )
        return 1
    try:
        run_pack(argv[0], argv[1], argv[2])
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

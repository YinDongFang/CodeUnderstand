"""按 repo 清理 Claude 目录与 outputs（``OUTPUTS_DIR`` 下产物）。"""
from __future__ import annotations

import os
import logging
import shutil
import sys
from pathlib import Path

from cu.logging_config import configure_logging
from cu.pipeline_env import resolve_outputs_dir


def run_cleanup(repo: str, selectors: list[str] | None = None) -> None:
    repo = repo.strip()
    if not repo or "/" in repo or ".." in repo:
        raise ValueError("repo 非法")

    outputs_dir = resolve_outputs_dir()
    user = (
        os.environ.get("USER", "").strip()
        or os.environ.get("LOGNAME", "").strip()
        or os.environ.get("USERNAME", "").strip()
        or "unknown"
    )
    repo_slug = repo.replace("_", "-")
    claude_project_dir = (
        Path.home() / ".claude" / "projects" / f"-home-{user}-projects-{repo_slug}"
    )
    out_dir = outputs_dir / f"code-understand-{repo}"
    zip_file = outputs_dir / f"code-understand-{repo}.zip"

    want = {"claude", "output", "zip"}
    if selectors:
        want.clear()
        for s in selectors:
            sl = s.strip().lower()
            if sl == "claude":
                want.add("claude")
            elif sl in ("output", "outputs"):
                want.add("output")
            elif sl == "zip":
                want.add("zip")
            else:
                raise ValueError(f"未知选择项 {s!r}（允许: claude output outputs zip）")

    log = logging.getLogger("cu.pipeline.cleanup_outputs")

    def msg(m: str) -> None:
        log.info("%s", m)

    msg("======================================================")
    msg(f"repo={repo} REPO_SLUG={repo_slug}")
    msg(f"OUTPUTS_DIR={outputs_dir}")
    if not selectors:
        msg("模式: 全部清理（claude + output + zip）")
    else:
        msg(f"模式: 仅清理 — claude={'claude' in want} output={'output' in want} zip={'zip' in want}")
    msg("======================================================")

    if "claude" in want:
        if claude_project_dir.is_dir():
            msg(f"删除 Claude 项目目录: {claude_project_dir}")
            shutil.rmtree(claude_project_dir)
        else:
            msg(f"跳过（不存在）: {claude_project_dir}")

    if "output" in want:
        if out_dir.is_dir():
            msg(f"删除 outputs 目录: {out_dir}")
            shutil.rmtree(out_dir)
        else:
            msg(f"跳过（不存在）: {out_dir}")

    if "zip" in want:
        if zip_file.is_file():
            msg(f"删除 zip: {zip_file}")
            zip_file.unlink()
        else:
            msg(f"跳过（不存在）: {zip_file}")

    msg("清理完成")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 1:
        print(
            "用法: python -m cu.pipeline.cleanup_outputs <repo> [claude|output|outputs|zip ...]",
            file=sys.stderr,
        )
        return 1
    repo = argv[0]
    sel = argv[1:] if len(argv) > 1 else None
    try:
        run_cleanup(repo, sel)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

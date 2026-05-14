"""宏阶段共享工具（路径解析、RunResult 校验、会话路径）。"""
from __future__ import annotations

import os

from cu.pipeline_env import normalize_session_id_filename
from cu.runner import RunResult


def repo_root() -> str:
    """本仓库根目录（cu/ 的父目录）。"""
    here = os.path.dirname(os.path.abspath(__file__))  # cu/stages
    return os.path.dirname(os.path.dirname(here))


def find_claude_project_dir(sandbox_home_path: str, session_id: str) -> str:
    """在沙箱 ~/.claude/projects/ 下搜寻含有 {session_id}.jsonl 的目录。"""
    root = os.path.join(sandbox_home_path, ".claude", "projects")
    if not os.path.isdir(root):
        return ""
    sid = normalize_session_id_filename(session_id.strip())
    target = f"{sid}.jsonl"
    for entry in os.listdir(root):
        proj = os.path.join(root, entry)
        if not os.path.isdir(proj):
            continue
        if os.path.isfile(os.path.join(proj, target)):
            return proj
    return ""


def check(result: RunResult, stage: str, step: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"[{stage}/{step}] exit={result.returncode}\n"
            f"stdout(tail): {result.stdout[-2000:]}\n"
            f"stderr(tail): {result.stderr[-2000:]}"
        )


def session_jsonl_path(claude_project_dir: str, session_id: str) -> str:
    if not claude_project_dir or not session_id:
        raise RuntimeError("[build] 缺少 claude_project_dir 或 session_id")
    sid = normalize_session_id_filename(session_id.strip())
    p = os.path.join(claude_project_dir, f"{sid}.jsonl")
    if not os.path.isfile(p):
        raise RuntimeError(f"[build] 会话 JSONL 不存在: {p}")
    return p

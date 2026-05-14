"""会话导出归一化：复制 + ``model`` 字段归一化 + subagents。"""
from __future__ import annotations

import json
import os
import shutil


def _normalize_jsonl_obj(obj: object) -> object:
    if not isinstance(obj, dict):
        return obj
    msg = obj.get("message")
    if isinstance(msg, dict) and "model" in msg and msg.get("model") != "model":
        msg["model"] = "model"

    data = obj.get("data")
    if isinstance(data, dict):
        dm = data.get("message")
        if isinstance(dm, dict):
            dmm = dm.get("message")
            if (
                isinstance(dmm, dict)
                and "model" in dmm
                and dmm.get("model") != "model"
            ):
                dmm["model"] = "model"
    return obj


def _resolve_session_paths(
    repo: str,
    session_id: str,
    sandbox_home_path: str,
    claude_project_dir: str,
) -> tuple[str, str]:
    sid = (session_id or "").strip()
    if sid.lower().endswith(".jsonl"):
        sid = sid[:-6]

    env_dir = (claude_project_dir or "").strip()
    if env_dir and os.path.isdir(env_dir):
        proj = env_dir
    else:
        user = (
            os.environ.get("USER", "").strip()
            or os.environ.get("LOGNAME", "").strip()
            or "user"
        )
        slug = repo.replace("_", "-")
        proj = os.path.join(
            sandbox_home_path,
            ".claude",
            "projects",
            f"-home-{user}-projects-{slug}",
        )

    session_file = os.path.join(proj, f"{sid}.jsonl")
    subagents_src = os.path.join(proj, sid, "subagents")
    return session_file, subagents_src


def export_session(
    *,
    artifact_root: str,
    repo: str,
    session_id: str,
    sandbox_home_path: str,
    claude_project_dir: str,
) -> None:
    """写入 ``{artifact_root}/sessions/session1/session.jsonl`` 及 ``subagents``。"""
    session_file, subagents_src = _resolve_session_paths(
        repo, session_id, sandbox_home_path, claude_project_dir
    )

    if not os.path.isfile(session_file):
        raise RuntimeError(
            f"[build/export-session] session 文件不存在: {session_file}"
        )

    session1 = os.path.join(artifact_root, "sessions", "session1")
    os.makedirs(session1, exist_ok=True)
    dst_jsonl = os.path.join(session1, "session.jsonl")

    shutil.copy2(session_file, dst_jsonl)

    out_lines: list[str] = []
    with open(dst_jsonl, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    for raw in raw_lines:
        line = raw.rstrip("\n\r")
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        obj = _normalize_jsonl_obj(obj)
        out_lines.append(json.dumps(obj, ensure_ascii=False))

    with open(dst_jsonl, "w", encoding="utf-8", newline="\n") as f:
        for ln in out_lines:
            f.write(ln)
            f.write("\n")

    if os.path.isdir(subagents_src):
        dst_sa = os.path.join(session1, "subagents")
        os.makedirs(dst_sa, exist_ok=True)
        for name in os.listdir(subagents_src):
            src_p = os.path.join(subagents_src, name)
            dst_p = os.path.join(dst_sa, name)
            if os.path.isdir(src_p):
                shutil.copytree(src_p, dst_p, dirs_exist_ok=True)
            else:
                shutil.copy2(src_p, dst_p)


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 2:
        print(
            "用法: python -m cu.pipeline.export_session <repo> <session_id>",
            file=sys.stderr,
        )
        print("环境变量: ARTIFACT_ROOT（必须）；HOME / CLAUDE_PROJECT_DIR", file=sys.stderr)
        return 1
    art = os.environ.get("ARTIFACT_ROOT", "").strip()
    if not art:
        print("错误: 需要设置 ARTIFACT_ROOT", file=sys.stderr)
        return 1
    try:
        export_session(
            artifact_root=os.path.abspath(art),
            repo=argv[0],
            session_id=argv[1],
            sandbox_home_path=os.path.abspath(os.path.expanduser(os.environ.get("HOME", "~"))),
            claude_project_dir=os.environ.get("CLAUDE_PROJECT_DIR", ""),
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

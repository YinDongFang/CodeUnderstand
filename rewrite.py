#!/usr/bin/env python3
"""
从 Claude Code 会话 JSONL 中提取「真实用户提问」（与 clean.py 规则一致），
可选地批量替换 message.content 后写回。

路径规则（与 build.sh / pack.sh 一致）：
  sourcePath  ~/.claude/projects/-home-{USER}-projects-{repo_slug}/ 下会话 .jsonl
              （优先 SESSION_ID 环境变量对应文件；否则取该目录最新修改的顶层 *.jsonl）
  copyPath    ${OUTPUTS_DIR}/code-understand-{repo}/sessions/session1/session.jsonl

用户消息仅从 sourcePath 解析并写入临时文件供编辑；编辑保存后，同一套「源正文→新正文」映射
对 sourcePath 与 copyPath 两份 JSONL 均做字面量替换写回。

真实提问：type=user 且 message.content 为单行字符串（无换行）、非 list 等。
临时文件格式：每行一个 JSON 字符串，对应一条问题（便于一行一号编辑）。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from typing import Any


def _infer_username() -> str:
    for key in ("USER", "LOGNAME", "USERNAME"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    try:
        u = getpass.getuser()
        if u:
            return u
    except Exception:
        pass
    return "user"


def resolve_source_session_jsonl(repo: str) -> str:
    """~/.claude/projects/-home-{USER}-projects-{repo_slug}/ 下的会话 .jsonl。

    若环境变量 SESSION_ID 已设置且 ``{SESSION_ID}.jsonl`` 存在则用之（与 pack.sh / build.sh 一致）；
    否则取该目录下最近修改的顶层 ``*.jsonl``。
    """
    user = _infer_username()
    repo_slug = repo.replace("_", "-")
    d = os.path.join(
        os.path.expanduser("~"), ".claude", "projects", f"-home-{user}-projects-{repo_slug}"
    )
    if not os.path.isdir(d):
        raise FileNotFoundError(f"Claude 项目目录不存在: {d}")

    sid = os.environ.get("SESSION_ID", "").strip()
    if sid:
        if sid.lower().endswith(".jsonl"):
            sid = sid[:-6]
        exact = os.path.join(d, f"{sid}.jsonl")
        if os.path.isfile(exact):
            return exact
        print(
            f"警告: SESSION_ID={sid!r} 时文件不存在 {exact}，改为选用目录下最新修改的 .jsonl",
            file=sys.stderr,
        )

    candidates: list[str] = []
    for name in os.listdir(d):
        if not name.endswith(".jsonl"):
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p):
            candidates.append(p)
    if not candidates:
        raise FileNotFoundError(f"目录下无顶层 .jsonl 会话文件: {d}")
    return max(candidates, key=lambda p: os.path.getmtime(p))


def resolve_copy_session_jsonl(repo: str) -> str:
    """${OUTPUTS_DIR}/code-understand-{repo}/sessions/session1/session.jsonl"""
    raw = os.environ.get("OUTPUTS_DIR", "").strip()
    if raw:
        out_root = os.path.abspath(os.path.expanduser(raw))
    else:
        out_root = os.path.join(os.path.expanduser("~"), "outputs")
    return os.path.join(
        out_root, f"code-understand-{repo}", "sessions", "session1", "session.jsonl"
    )


def _load_jsonl_objects(raw_lines: list[str]) -> list[Any | None]:
    """与 raw_lines 下标一一对应（含空行、坏 JSON），仅用于解析提问列表。"""
    out: list[Any | None] = []
    for line in raw_lines:
        s = line.strip()
        if not s:
            out.append(None)
            continue
        try:
            out.append(json.loads(s))
        except json.JSONDecodeError:
            out.append(None)
    return out


def _is_real_user_question(obj: Any) -> bool:
    if not isinstance(obj, dict) or obj.get("type") != "user":
        return False
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content", "")
    # 含 tool_result 的续行 user 消息 content 为 list，不是真实用户提问
    if not isinstance(content, str):
        return False
    if not content.strip():
        return False
    # 仅单行正文：不含换行符（与临时文件「一行一条」编辑方式一致）
    if "\n" in content or "\r" in content:
        return False
    if content.strip().startswith("<task-notification>"):
        return False
    return True


def collect_user_questions(objects: list[Any | None]) -> list[str]:
    """按 JSONL 顺序返回各条真实用户提问的 message.content 原文。"""
    found: list[str] = []
    for obj in objects:
        if obj is None:
            continue
        if _is_real_user_question(obj):
            msg = obj["message"]
            assert isinstance(msg, dict)
            found.append(str(msg["content"]))
    return found


def dump_questions_to_tmp(path: str, questions: list[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False))
            f.write("\n")


def load_questions_from_tmp(path: str) -> list[str]:
    lines: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                val = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"无法解析为 JSON 字符串的行: {line[:80]!r}... ({e})") from e
            if not isinstance(val, str):
                raise ValueError(f"每行必须是 JSON 字符串，得到类型: {type(val).__name__}")
            lines.append(val)
    return lines


def _json_string_literal(text: str) -> str:
    """JSON 字符串的源码形式（含首尾双引号与转义）。"""
    return json.dumps(text, ensure_ascii=False)


def write_jsonl_with_text_mapping(
    session_path: str,
    raw_lines: list[str],
    source_to_new: dict[str, str],
) -> None:
    """
    对每一行原始文本：按「源正文 -> 新正文」映射做子串替换。
    匹配与替换片段均为 json.dumps 后的字面量（两侧为双引号的 JSON 字符串形式）。
    """
    if not source_to_new:
        return
    pairs = sorted(
        source_to_new.items(),
        key=lambda kv: len(_json_string_literal(kv[0])),
        reverse=True,
    )
    bak = session_path + ".rewrite.bak"
    shutil.copy2(session_path, bak)
    try:
        with open(session_path, "w", encoding="utf-8", newline="\n") as f:
            for raw in raw_lines:
                line = raw
                for old, new in pairs:
                    if old == new:
                        continue
                    line = line.replace(
                        _json_string_literal(old),
                        _json_string_literal(new),
                    )
                f.write(line)
    except Exception:
        shutil.copy2(bak, session_path)
        raise
    else:
        os.remove(bak)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="按 repo 解析 ~/.claude 与 OUTPUTS 下两份 session.jsonl，用 gedit 编辑并写回用户单行提问"
    )
    ap.add_argument(
        "repo",
        help="项目名（与 pack.sh 第二参数相同，projects 下目录名），用于拼 Claude 目录与 code-understand 输出路径",
    )
    args = ap.parse_args()
    repo = (args.repo or "").strip()
    if not repo or "/" in repo or ".." in repo:
        print("错误: repo 非法（须非空且不能含 / 或 ..）", file=sys.stderr)
        return 1

    try:
        source_path = os.path.abspath(resolve_source_session_jsonl(repo))
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    copy_path = os.path.abspath(resolve_copy_session_jsonl(repo))

    print(f"sourcePath（提取用户消息）: {source_path}")
    print(f"copyPath（同步替换）:       {copy_path}")

    if not os.path.isfile(source_path):
        print(f"错误: source 不是文件: {source_path}", file=sys.stderr)
        return 1

    with open(source_path, "r", encoding="utf-8") as f:
        raw_source = f.readlines()

    objects = _load_jsonl_objects(raw_source)
    questions = collect_user_questions(objects)
    if not questions:
        print(
            "未找到可编辑的用户提问（type=user 且 message.content 为非空单行字符串、"
            "不得含换行符、不得为含 tool_result 的 list、且非 <task-notification>）。"
        )
        return 0

    tmp_dir = os.path.join(os.getcwd(), "tmp")
    tmp_path = os.path.join(tmp_dir, f"{repo}_questions.txt")
    dump_questions_to_tmp(tmp_path, questions)
    print(f"已写入: {tmp_path}（共 {len(questions)} 条）")
    print("说明: 每行一条 JSON 字符串，顺序与会话中一致；仅改字符串内文字，勿增删行数。")

    new_list: list[str] = []
    while True:
        subprocess.Popen(
            ["gedit", tmp_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        input("在编辑器中保存文件后，按 Enter 继续…")
        try:
            new_list = load_questions_from_tmp(tmp_path)
        except ValueError as e:
            print(f"读取编辑结果失败: {e}", file=sys.stderr)
            print("将重新打开 gedit，请修正后保存。", file=sys.stderr)
            continue
        if len(new_list) != len(questions):
            print(
                f"错误: 编辑后有效行数为 {len(new_list)}，与原来的 {len(questions)} 不一致，未写回。",
                file=sys.stderr,
            )
            print("将重新打开 gedit，请修正后保存。", file=sys.stderr)
            continue
        break

    source_to_new: dict[str, str] = {}
    for old_text, new_text in zip(questions, new_list):
        if new_text != old_text:
            source_to_new[old_text] = new_text

    if not source_to_new:
        print("内容无变化，未写回会话文件。")
        return 0

    if not os.path.isfile(copy_path):
        print(
            f"错误: copyPath 不存在，无法同步写回: {copy_path}\n"
            "请先执行 build.sh 生成 sessions/session1/session.jsonl，或检查 OUTPUTS_DIR。",
            file=sys.stderr,
        )
        return 1

    with open(copy_path, "r", encoding="utf-8") as f:
        raw_copy = f.readlines()

    write_jsonl_with_text_mapping(source_path, raw_source, source_to_new)
    write_jsonl_with_text_mapping(copy_path, raw_copy, source_to_new)
    print(
        f"已按 {len(source_to_new)} 条「源文本→新文本」映射写回 sourcePath 与 copyPath（逐行字面量替换）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

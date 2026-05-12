#!/usr/bin/env python3
"""
从 Claude Code 会话 JSONL 中提取「真实用户提问」（与 clean.py 规则一致），
可选地批量替换 message.content 后写回。

真实提问：type=user 且 message.content 为单行字符串（无换行）、非 list 等。
临时文件格式：每行一个 JSON 字符串，对应一条问题（便于一行一号编辑）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any


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


def print_numbered_questions(questions: list[str]) -> None:
    for n, text in enumerate(questions, start=1):
        print(f"----- [{n}] -----")
        print(text)
        print()


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
    # 长字面量先替换，降低「一段是另一段子串」时的误伤概率
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
        description="列出并可选地改写会话 JSONL 中的用户单行提问（message.content 为无换行的字符串的 user 行）"
    )
    ap.add_argument(
        "session_path",
        help="会话 .jsonl 路径（例如 ~/.claude/projects/.../xxx.jsonl）",
    )
    args = ap.parse_args()
    session_path = os.path.abspath(os.path.expanduser(args.session_path))
    if not os.path.isfile(session_path):
        print(f"错误: 不是有效文件: {session_path}", file=sys.stderr)
        return 1

    with open(session_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    objects = _load_jsonl_objects(raw_lines)
    questions = collect_user_questions(objects)
    if not questions:
        print(
            "未找到可编辑的用户提问（type=user 且 message.content 为非空单行字符串、"
            "不得含换行符、不得为含 tool_result 的 list、且非 <task-notification>）。"
        )
        return 0

    print_numbered_questions(questions)
    print(f"共 {len(questions)} 条问题。")

    ans = input("是否需要修改？(y/N): ").strip().lower()
    if ans not in ("y", "yes", "是"):
        print("已取消，未修改文件。")
        return 0

    stem = os.path.splitext(os.path.basename(session_path))[0]
    tmp_dir = os.path.join(os.getcwd(), "tmp")
    tmp_path = os.path.join(tmp_dir, f"{stem}_questions.txt")
    dump_questions_to_tmp(tmp_path, questions)
    print(f"已写入: {tmp_path}")
    print("说明: 每行一条 JSON 字符串，对应上面 [1]、[2]… 顺序；仅改字符串内文字，勿增删行数。")

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

    write_jsonl_with_text_mapping(session_path, raw_lines, source_to_new)
    print(f"已按 {len(source_to_new)} 条「源文本→新文本」映射写回（逐行字面量替换）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

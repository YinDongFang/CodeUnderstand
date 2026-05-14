#!/usr/bin/env python3
"""Mock Claude CLI（供 loop/build/classify 在 *USE_MOCK_CLAUDE 时使用）。

最后一个 CLI 参数视为 prompt（与旧 bash mock 脚本 ``prompt="${!#}"`` 语义一致）。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys


def _prompt_from_argv(argv: list[str]) -> str:
    if not argv:
        return ""
    return argv[-1]


def _json_mode(argv: list[str]) -> bool:
    for i in range(len(argv) - 1):
        if argv[i] == "--output-format" and argv[i + 1] == "json":
            return True
    return False


def _md5_32(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _next_seq() -> str:
    path = os.environ.get("LOOP_MOCK_SEQ_FILE")
    if not path:
        print("mock: 请设置环境变量 LOOP_MOCK_SEQ_FILE（由 cu.pipeline.loop 在 mock 模式下写入）", file=sys.stderr)
        sys.exit(2)
    try:
        with open(path, "r+", encoding="utf-8") as f:
            raw = f.read().strip() or "0"
            n = int(raw) + 1
            f.seek(0)
            f.truncate()
            f.write(f"{n}\n")
        return str(n)
    except OSError as e:
        print(f"mock: seq file {path}: {e}", file=sys.stderr)
        sys.exit(2)


def _is_prompt_entry(prompt: str) -> bool:
    return (
        "只返回最终版本的英文" in prompt
        or "整体架构" in prompt
        or "业务流程方面" in prompt
    )


def _is_second_layer_deeper(prompt: str) -> bool:
    return "提出2个新的问题" in prompt and "L1-Q-" in prompt


def _is_prompt_summary(prompt: str) -> bool:
    return "汇总以上" in prompt and "提出2个新的问题" in prompt


def _emit_json(session_id: str, body: str) -> None:
    print(json.dumps({"session_id": session_id, "result": body}, ensure_ascii=False))


def _mock_answer_suffix(prompt: str, tag: str) -> str:
    seq = _next_seq()
    return f"{prompt}__MOCK_{tag}__seq={seq}"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    prompt = _prompt_from_argv(argv)

    # classify CLI / metadata
    if (
        "Classify the project into exactly one category" in prompt
        and "Return ONLY a single JSON object" in prompt
    ):
        print(json.dumps({"main_type": "Learning/Tutorial"}, ensure_ascii=False))
        return 0

    # build_docs doc 生成 prompt
    if (
        "IMPORTANT: Write all doc/ output files to this absolute path:" in prompt
        or "Generate exactly 4 Markdown documents" in prompt
    ):
        return 0

    json_mode = _json_mode(argv)

    if json_mode:
        sid = f"mock-json-{os.getpid()}-{random.randint(0, 2**31)}"
        if _is_prompt_entry(prompt):
            h = _md5_32(prompt)
            lines = []
            for _ in range(5):
                lines.append(f"ENTRY-Q-{h}-{_next_seq()}")
            body = "\n".join(lines) + "\n"
        else:
            body = _mock_answer_suffix(prompt, "JSONFIRST_REPO")
        _emit_json(sid, body.rstrip("\n"))
        return 0

    if _is_prompt_entry(prompt):
        h = _md5_32(prompt)
        lines = []
        for _ in range(5):
            lines.append(f"ENTRY-Q-{h}-{_next_seq()}")
        sys.stdout.write("\n".join(lines) + "\n")
        return 0

    if "最后一个" in prompt and "综合性" in prompt:
        h = _md5_32(prompt)
        sys.stdout.write(f"FINAL-Q-{h}-{_next_seq()}\n")
        return 0

    if _is_prompt_summary(prompt):
        h = _md5_32(prompt)
        s1 = _next_seq()
        s2 = _next_seq()
        sys.stdout.write(f"SUM-Q-{h}-{s1}\nSUM-Q-{h}-{s2}\n")
        return 0

    if "提出2个新的问题" in prompt:
        h = _md5_32(prompt)
        if _is_second_layer_deeper(prompt):
            sys.stdout.write(
                f"L2-Q-{h}-{_next_seq()}\nL2-Q-{h}-{_next_seq()}\n"
            )
        else:
            sys.stdout.write(
                f"L1-Q-{h}-{_next_seq()}\nL1-Q-{h}-{_next_seq()}\n"
            )
        return 0

    if prompt.startswith("SUM-Q-"):
        tag = "REPO_SUM"
    elif prompt.startswith("L2-Q-"):
        tag = "REPO_L2"
    elif prompt.startswith("L1-Q-"):
        tag = "REPO_L1"
    elif prompt.startswith("ENTRY-Q-"):
        tag = "REPO_ENTRY"
    else:
        tag = "PLAIN"
    sys.stdout.write(_mock_answer_suffix(prompt, tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

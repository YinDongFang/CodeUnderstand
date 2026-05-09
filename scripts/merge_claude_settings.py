#!/usr/bin/env python3
"""
一次性脚本：读取 ~/.claude/settings.json，与当前目录下的 claude.settings.json 深度合并后写回。

合并规则：同为 dict 则递归；同为 list 则拼接（overlay 在后）；其余情况以 overlay 为准。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


def merge(base: object, overlay: object) -> object:
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = copy.deepcopy(base)
        for k, v in overlay.items():
            if k in out:
                out[k] = merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    if isinstance(base, list) and isinstance(overlay, list):
        return copy.deepcopy(base) + copy.deepcopy(overlay)
    return copy.deepcopy(overlay)


def main() -> int:
    overlay_path = Path.home() / "CodeUnderstand" / "scripts" / "claude.settings.json"
    user_path = Path.home() / ".claude" / "settings.json"

    if not overlay_path.is_file():
        print(f"[merge] 未找到 {overlay_path}", file=sys.stderr)
        return 1

    try:
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[merge] 解析 claude.settings.json 失败: {e}", file=sys.stderr)
        return 1

    if user_path.is_file():
        try:
            base = json.loads(user_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[merge] 解析 {user_path} 失败: {e}", file=sys.stderr)
            return 1
    else:
        base = {}

    merged = merge(base, overlay)

    user_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
    user_path.write_text(text, encoding="utf-8")
    print(f"[merge] 已写入 {user_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

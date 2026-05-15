"""运行状态的 JSON 持久化：load_state / save_state。"""
from __future__ import annotations

import json
import os
from typing import Any


def load_state(path: str) -> list[dict]:
    """读取持久化文件，返回已完成节点列表。文件不存在则返回 []。"""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["nodes"]


def save_state(path: str, data: dict[str, Any]) -> None:
    """原子写入：先写临时文件，再 os.replace 重命名。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)

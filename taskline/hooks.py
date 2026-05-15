"""Hook 上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HookContext:
    phase: str             # "before" | "after"
    node_id: str
    index: int             # submit 顺序
    resuming: bool         # True 仅当这是崩溃恢复点节点
    result: Any = None     # 仅 "after" 阶段有值

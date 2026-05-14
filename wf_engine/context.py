from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class NodeContext:
    task_id: str
    node_id: str
    workflow_key: str
    task_root: Path
    workspace: Path
    node_workdir: Path
    human_input: dict[str, Any] | None
    input: dict[str, Any]
    context: dict[str, Any]
    settings: dict[str, str]

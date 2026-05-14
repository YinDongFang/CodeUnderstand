from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskLayout:
    root: Path
    workspace: Path
    zips: Path
    logs: Path


def task_layout(task_root: Path) -> TaskLayout:
    return TaskLayout(
        root=task_root,
        workspace=task_root / "workspace",
        zips=task_root / "artifacts" / "zips",
        logs=task_root / "logs",
    )

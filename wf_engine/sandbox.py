from __future__ import annotations

from pathlib import Path


def resolve_node_workdir(workspace: Path, workdir_relative: str) -> Path:
    """Resolve ``workspace / workdir_relative`` and ensure it stays under ``workspace``."""
    ws = workspace.resolve()
    rel_path = Path(workdir_relative or ".").as_posix()
    if not rel_path or rel_path == ".":
        candidate = ws
    else:
        parts = Path(workdir_relative).parts
        if ".." in parts:
            msg = "workdir_relative must not contain '..'"
            raise ValueError(msg)
        candidate = (workspace / workdir_relative).resolve()
    try:
        candidate.relative_to(ws)
    except ValueError as e:
        msg = "node workdir escapes workspace"
        raise ValueError(msg) from e
    return candidate

from __future__ import annotations

from pathlib import Path

from wf_engine.server.state import ControlPlaneState


def effective_tasks_root(cp: ControlPlaneState) -> Path:
    """Directory under which new task folders are created (persisted override or server default)."""
    cfg = cp.store.get_system_config()
    raw = (cfg.get("tasks_root") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(cp.tasks_root).resolve()

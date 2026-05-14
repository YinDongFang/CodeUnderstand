from __future__ import annotations

from pathlib import Path

from wf_engine.server.state import ControlPlaneState


def effective_tasks_root(cp: ControlPlaneState) -> Path:
    """Directory under which new task folders are created (persisted override or server default)."""
    cfg = cp.store.get_console_settings()
    raw = (cfg.get("root") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path.home() / p
        return p.resolve()
    return Path(cp.tasks_root).resolve()

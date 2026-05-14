from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from wf_engine.engine import Engine
from wf_engine.store.sqlite import SqliteStore


@dataclass(frozen=True)
class ControlPlaneState:
    engine: Engine
    store: SqliteStore
    tasks_root: Path
    spawn_worker_fn: Callable[..., Any]

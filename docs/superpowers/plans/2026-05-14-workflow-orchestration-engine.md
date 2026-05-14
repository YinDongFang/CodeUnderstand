# Workflow orchestration engine implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Below, **`- [x]`** marks steps that are done in this repo; **`- [ ]`** would mean not started.

**Goal:** Deliver a single-machine Python library (`wf_engine`) that registers serial workflows in-process, runs each task in an isolated subprocess, persists state to SQLite, supports per-node whitelist zips and interrupt/resume plus node-level rerun with safe unzip, and exposes a minimal FastAPI control plane plus a Vite/React ops UI polling those APIs.

**Architecture:** Control plane (FastAPI + SQLite WAL) owns workflow registry and task rows; a supervisor spawns one worker subprocess per execution epoch per task with a DB-backed lease; workers run a pure `Runner` that executes node callables, packs zips on success, and raises a controlled interrupt path. The UI is a separate Vite app proxied to `/api` in dev.

**Tech stack:** Python 3.12+, FastAPI, Uvicorn, Pydantic v2, SQLite3 (`PRAGMA journal_mode=WAL`), `jsonschema` for interrupt payload validation, pytest; frontend: Vite 5+, React 18+, TypeScript.

**Spec source:** `docs/superpowers/specs/2026-05-14-workflow-orchestration-engine-design.md`

**Implementation status:** Task 1–11 are implemented on branch `flow` (`wf_engine/`, `web/`, `tests/wf_engine/`). Authoritative verification should be run on Ubuntu; see `docs/ubuntu-testing.md`. Utility modules have since been consolidated under `wf_engine/utils/` (`archives.py`, `lease.py`, `task_layout.py`, `sandbox.py`, `log_markers.py`), so older file-map references to top-level `zip_util.py`, `unzip_util.py`, `lease_util.py`, or `paths.py` are historical. Spec-gap items OE-001…OE-007 are closed per `docs/superpowers/issues/2026-05-14-wf-engine-spec-gap-closure.md` (landed with `288ba5e` among others). **Still outstanding** (plan “Gaps” §): log rotation §3.5.9, maximum zip size, multi-uvicorn / multi-writer guard beyond `workers=1`.

---

## File map (new repository layout)

Repository root assumes this plan is added alongside new code (adjust paths if your repo already has a layout).

| Path | Responsibility |
|------|----------------|
| `wf_engine/__init__.py` | Public exports: `Workflow`, `Engine`, `NodeContext`, `interrupt` |
| `wf_engine/status.py` | Task and node status string constants matching spec §8 |
| `wf_engine/workflow.py` | `Workflow`, `NodeSpec` (id, fn, workdir rel path, whitelist globs, flags) |
| `wf_engine/context.py` | `NodeContext` dataclass built by runner |
| `wf_engine/interrupt.py` | `interrupt()`, `ControlledInterrupt` exception type |
| `wf_engine/paths.py` | `task_root`, `workspace_dir`, `zips_dir`, `logs_dir` |
| `wf_engine/zip_util.py` | Pack whitelist zip; optional empty-glob handling (**skip zip** per §3.5.6) |
| `wf_engine/unzip_util.py` | Rerun extract with zip-slip checks §3.5.5 |
| `wf_engine/store/sqlite.py` | DDL, CRUD, transactions for tasks/nodes/interrupt/lease |
| `wf_engine/runner.py` | Core serial execution loop matching spec §3.4.4 |
| `wf_engine/supervisor.py` | Spawn worker module, lease acquire/release, stale detection |
| `wf_engine/worker_main.py` | CLI entry: `python -m wf_engine.worker_main --task-id ...` |
| `wf_engine/engine.py` | `Engine`: registry, `serve()`, db path, tasks_root |
| `wf_engine/server/app.py` | FastAPI app factory, mounts routes |
| `wf_engine/server/routes_tasks.py` | `/tasks` CRUD-ish endpoints per spec §5 + §3.4.5 |
| `web/` | Vite+React UI: list + detail + logs polling |
| `pyproject.toml` | Package + ruff/pytest deps |
| `tests/wf_engine/` | Unit + integration tests |

**Locked policy choices (from spec where 二选一):**

- Duplicate `workflow_key` on register: **raise `ValueError`**.
- Nodes with **empty whitelist**: **skip zip**, still mark `success`; `rerun` treats missing zip for that ordinal as **409** if that node was "success without zip" and rerun needs prior state — document in tests: **only nodes that produced zips participate in rerun chain**; if a prior node skipped zip, rerun reconstructs only from dirs left on disk — *simpler fix:* require **at least one glob or skip node from rerun chain** — for MVP tests use **all nodes have ≥1 glob or share workspace file**; spec §3.5.6: implement **skip zip** and document that **rerun from K requires each predecessor that must be replayed from zip to have a zip path in DB**; if skipped, operator must `rerun` from earlier node. *(Captured in Task 7 acceptance comment.)*
- `rerun` when zip missing: **HTTP 409** with `error.code = "missing_snapshot"`.
- `stalled` recovery: only **`rerun`** in MVP (no magic resume).
- Control plane: **single process** writing SQLite (one Uvicorn worker or `workers=1` documented).

---

### Task 1: `pyproject.toml` and package skeleton

**Files:**

- Create: `pyproject.toml`
- Create: `wf_engine/__init__.py`
- Create: `wf_engine/status.py`
- Create: `wf_engine/paths.py`
- Test: `tests/wf_engine/test_paths.py`

- [x] **Step 1: Write failing test for path layout**

```python
# tests/wf_engine/test_paths.py
from pathlib import Path
from wf_engine.paths import task_layout

def test_task_layout_under_tasks_root():
    root = Path("/data/tasks") / "tid-1"
    lo = task_layout(root)
    assert lo.workspace == root / "workspace"
    assert lo.zips == root / "artifacts" / "zips"
    assert lo.logs == root / "logs"
```

- [x] **Step 2: Run test — expect failure**

Run: `pytest tests/wf_engine/test_paths.py -v`

Expected: import error or missing `task_layout`.

- [x] **Step 3: Add `pyproject.toml`**

```toml
[project]
name = "wf-engine"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.32.0",
  "pydantic>=2.10.0",
  "jsonschema>=4.23.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.28.0", "ruff>=0.8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [x] **Step 4: Implement `status.py` and `paths.py`**

```python
# wf_engine/status.py
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_WAITING_HUMAN = "waiting_human"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_FAILED_STALLED = "stalled"

NODE_PENDING = "pending"
NODE_RUNNING = "running"
NODE_WAITING_HUMAN = "waiting_human"
NODE_SUCCESS = "success"
NODE_FAILED = "failed"
```

```python
# wf_engine/paths.py
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
```

```python
# wf_engine/__init__.py
"""Workflow orchestration engine (see project spec)."""
```

- [x] **Step 5: Run tests**

Run: `pytest tests/wf_engine/test_paths.py -v` — expect PASS.

- [x] **Step 6: Commit**

```bash
git add pyproject.toml wf_engine tests/wf_engine
git commit -m "feat(wf_engine): scaffold package, paths, status constants"
```

---

### Task 2: SQLite schema and store (tasks, nodes, interrupt, lease)

**Files:**

- Create: `wf_engine/store/__init__.py`
- Create: `wf_engine/store/sqlite.py`
- Test: `tests/wf_engine/test_sqlite_store.py`

- [x] **Step 1: Failing test — create task and load**

```python
# tests/wf_engine/test_sqlite_store.py
import tempfile
from pathlib import Path

from wf_engine.store.sqlite import SqliteStore


def test_create_task_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        tid = store.create_task(
            workflow_key="wf1",
            workflow_revision="rev-a",
            input_obj={"k": 1},
            tasks_root=str(Path(td) / "runs"),
        )
        row = store.get_task(tid)
        assert row is not None
        assert row["workflow_key"] == "wf1"
        assert row["status"] == "pending"
```

- [x] **Step 2: Run — expect fail**

Run: `pytest tests/wf_engine/test_sqlite_store.py -v`

- [x] **Step 3: Implement `SqliteStore`**

```python
# wf_engine/store/__init__.py
from wf_engine.store.sqlite import SqliteStore

__all__ = ["SqliteStore"]
```

```python
# wf_engine/store/sqlite.py
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wf_engine import status as S


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _loads(s: str | None) -> Any:
    if s is None:
        return None
    return json.loads(s)


class SqliteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_schema(self) -> None:
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL;")
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    workflow_key TEXT NOT NULL,
                    workflow_revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    tasks_root TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    interrupt_seq INTEGER NOT NULL DEFAULT 0,
                    interrupt_node_id TEXT,
                    interrupt_expected_schema TEXT,
                    interrupt_request_extras TEXT,
                    interrupt_checkpoint TEXT,
                    interrupt_response_payload TEXT,
                    interrupt_response_consumed INTEGER NOT NULL DEFAULT 0,
                    worker_pid INTEGER,
                    lease_until TEXT,
                    worker_generation INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS task_nodes (
                    task_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    zip_path TEXT,
                    error_json TEXT,
                    PRIMARY KEY (task_id, ordinal),
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_task_nodes_task ON task_nodes(task_id);
                """
            )

    def create_task(
        self,
        *,
        workflow_key: str,
        workflow_revision: str,
        input_obj: dict[str, Any],
        tasks_root: str,
    ) -> str:
        tid = str(uuid.uuid4())
        now = _utc_iso()
        with self.connect() as c:
            c.execute(
                """INSERT INTO tasks
                    (id, workflow_key, workflow_revision, status, input_json, tasks_root,
                     created_at, updated_at, interrupt_seq, interrupt_node_id,
                     interrupt_expected_schema, interrupt_request_extras, interrupt_checkpoint,
                     interrupt_response_payload, interrupt_response_consumed,
                     worker_pid, lease_until, worker_generation)
                    VALUES (?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,0)""",
                (
                    tid,
                    workflow_key,
                    workflow_revision,
                    S.TASK_PENDING,
                    _dumps(input_obj),
                    tasks_root,
                    now,
                    now,
                ),
            )
        return tid

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input_json"] = _loads(d["input_json"])
        d["interrupt_response_consumed"] = bool(d["interrupt_response_consumed"])
        for k in (
            "interrupt_expected_schema",
            "interrupt_request_extras",
            "interrupt_checkpoint",
            "interrupt_response_payload",
        ):
            d[k] = _loads(d[k]) if d[k] else None
        return d

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [self.get_task(r["id"]) for r in rows]  # type: ignore[list-item]

    def set_task_status(self, task_id: str, status: str) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, _utc_iso(), task_id),
            )

    def init_task_nodes(self, task_id: str, node_ids: list[str]) -> None:
        with self.connect() as c:
            for ord_, nid in enumerate(node_ids):
                c.execute(
                    """INSERT INTO task_nodes
                    (task_id, node_id, ordinal, status) VALUES (?,?,?,?)""",
                    (task_id, nid, ord_, S.NODE_PENDING),
                )

    def list_nodes(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM task_nodes WHERE task_id=? ORDER BY ordinal",
                (task_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["error_json"] = _loads(d["error_json"]) if d["error_json"] else None
            out.append(d)
        return out

    def update_node(
        self,
        task_id: str,
        ordinal: int,
        *,
        status: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        zip_path: str | None = None,
        error_json: dict | None = None,
    ) -> None:
        sets: list[str] = []
        vals: list[Any] = []
        if status is not None:
            sets.append("status=?")
            vals.append(status)
        if started_at is not None:
            sets.append("started_at=?")
            vals.append(started_at)
        if finished_at is not None:
            sets.append("finished_at=?")
            vals.append(finished_at)
        if zip_path is not None:
            sets.append("zip_path=?")
            vals.append(zip_path)
        if error_json is not None:
            sets.append("error_json=?")
            vals.append(_dumps(error_json))
        if not sets:
            return
        vals.extend([task_id, ordinal])
        with self.connect() as c:
            c.execute(
                f"UPDATE task_nodes SET {', '.join(sets)} WHERE task_id=? AND ordinal=?",
                vals,
            )

    def open_interrupt(
        self,
        task_id: str,
        *,
        node_id: str,
        expected_schema: dict | None,
        ui: dict | None,
        checkpoint: dict | None,
    ) -> int:
        now = _utc_iso()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                """UPDATE tasks SET interrupt_seq=interrupt_seq+1,
                interrupt_node_id=?,
                interrupt_expected_schema=?,
                interrupt_request_extras=?,
                interrupt_checkpoint=?,
                interrupt_response_payload=NULL,
                interrupt_response_consumed=0,
                status=?,
                updated_at=?
                WHERE id=?""",
                (
                    node_id,
                    _dumps(expected_schema) if expected_schema is not None else None,
                    _dumps(ui) if ui else None,
                    _dumps(checkpoint) if checkpoint else None,
                    S.TASK_WAITING_HUMAN,
                    now,
                    task_id,
                ),
            )
            row = c.execute(
                "SELECT interrupt_seq FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            c.execute("COMMIT")
        if row is None:
            raise KeyError(task_id)
        return int(row["interrupt_seq"])

    def apply_resolve(self, task_id: str, payload: dict[str, Any]) -> None:
        now = _utc_iso()
        with self.connect() as c:
            c.execute(
                """UPDATE tasks SET interrupt_response_payload=?,
                interrupt_response_consumed=0, status=?, updated_at=?,
                worker_generation=worker_generation+1 WHERE id=?""",
                (_dumps(payload), S.TASK_RUNNING, now, task_id),
            )

    def acquire_lease(self, task_id: str, pid: int, lease_until: str) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE tasks SET worker_pid=?, lease_until=?, updated_at=? WHERE id=?",
                (pid, lease_until, _utc_iso(), task_id),
            )

    def release_lease(self, task_id: str) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE tasks SET worker_pid=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                (_utc_iso(), task_id),
            )
```

- [x] **Step 4: Run tests** — PASS.

- [x] **Step 5: Commit** — `feat(wf_engine): add SQLite store and schema`

---

### Task 3: Workflow model and `Engine` registry

**Files:**

- Create: `wf_engine/workflow.py`
- Create: `wf_engine/engine.py`
- Test: `tests/wf_engine/test_engine_registry.py`

- [x] **Step 1: Failing test duplicate key**

```python
# tests/wf_engine/test_engine_registry.py
import pytest
from wf_engine.engine import Engine
from wf_engine.workflow import Workflow


def test_duplicate_workflow_key_rejected():
    eng = Engine()
    wf = Workflow(key="demo")

    def n1(ctx):
        pass

    wf.add_node("a", n1)
    eng.register_workflow(wf)
    with pytest.raises(ValueError, match="workflow_key"):
        eng.register_workflow(wf)
```

- [x] **Step 2: Run — fail**

- [x] **Step 3: Implement `Workflow` / `NodeSpec` / `Engine`**

```python
# wf_engine/workflow.py
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from wf_engine.context import NodeContext


@dataclass
class NodeSpec:
    id: str
    fn: Callable[[NodeContext], None]
    workdir_relative: str  # relative to workspace/
    whitelist_globs: Sequence[str] = field(default_factory=tuple)


@dataclass
class Workflow:
    key: str
    revision: str = "1"
    nodes: list[NodeSpec] = field(default_factory=list)

    def add_node(
        self,
        node_id: str,
        fn: Callable[[NodeContext], None],
        *,
        workdir: str = ".",
        whitelist: Sequence[str] = (),
    ) -> None:
        self.nodes.append(
            NodeSpec(
                id=node_id,
                fn=fn,
                workdir_relative=workdir,
                whitelist_globs=tuple(whitelist),
            )
        )
```

```python
# wf_engine/engine.py
from __future__ import annotations

from wf_engine.workflow import Workflow


class Engine:
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register_workflow(self, wf: Workflow) -> None:
        if wf.key in self._workflows:
            msg = f"workflow_key already registered: {wf.key!r}"
            raise ValueError(msg)
        self._workflows[wf.key] = wf

    def get_workflow(self, key: str) -> Workflow:
        return self._workflows[key]
```

```python
# wf_engine/context.py
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
```

- [x] **Step 4: Update `wf_engine/__init__.py`** to export `Engine`, `Workflow`, `NodeContext`.

- [x] **Step 5: Run tests** — PASS.

- [x] **Step 6: Commit**

---

### Task 4: Interrupt primitive

**Files:**

- Create: `wf_engine/interrupt.py`
- Modify: `wf_engine/__init__.py`
- Test: `tests/wf_engine/test_interrupt.py`

- [x] **Step 1: Test controlled interrupt carries schema**

```python
# tests/wf_engine/test_interrupt.py
import pytest
from wf_engine.interrupt import ControlledInterrupt, interrupt


def test_interrupt_raises_controlled():
    with pytest.raises(ControlledInterrupt) as ei:
        interrupt(expected_schema=None, ui={"title": "x"})
    assert ei.value.expected_schema is None
```

- [x] **Step 2: Implement**

```python
# wf_engine/interrupt.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Never


@dataclass
class ControlledInterrupt(Exception):
    expected_schema: dict[str, Any] | None
    ui: dict[str, Any] | None
    checkpoint: dict[str, Any] | None


def interrupt(
    *,
    expected_schema: dict[str, Any] | None,
    ui: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> Never:
    raise ControlledInterrupt(
        expected_schema=expected_schema,
        ui=ui,
        checkpoint=checkpoint,
    )
```

- [x] **Step 3: Run test** — PASS.

- [x] **Step 4: Commit**

---

### Task 5: Zip pack and unzip (whitelist + zip-slip)

**Files:**

- Create: `wf_engine/zip_util.py`
- Create: `wf_engine/unzip_util.py`
- Test: `tests/wf_engine/test_zip_util.py`
- Test: `tests/wf_engine/test_unzip_util.py`

- [x] **Step 1: Tests**

```python
# tests/wf_engine/test_zip_util.py
import zipfile
from pathlib import Path
import tempfile
from wf_engine.zip_util import pack_whitelist_zip


def test_pack_whitelist_includes_only_matches():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_text("A", encoding="utf-8")
        (root / "b.txt").write_text("B", encoding="utf-8")
        z = root / "out.zip"
        pack_whitelist_zip(parent=root, include_globs=("a.txt",), dest_zip=z)
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
        assert names == {"a.txt"}
```

```python
# tests/wf_engine/test_unzip_util.py
import io
import zipfile
from pathlib import Path
import tempfile
import pytest
from wf_engine.unzip_util import UnsafeArchiveError, extract_zip_safely


def test_extract_rejects_zip_slip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "x")
    buf.seek(0)
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td)
        with pytest.raises(UnsafeArchiveError):
            extract_zip_safely(buf.getvalue(), dest)
```

- [x] **Step 2: Run tests — fail until implemented**

Run: `pytest tests/wf_engine/test_zip_util.py tests/wf_engine/test_unzip_util.py -v`

- [x] **Step 3: Implement**

```python
# wf_engine/zip_util.py
from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def pack_whitelist_zip(*, parent: Path, include_globs: tuple[str, ...], dest_zip: Path) -> None:
    if not include_globs:
        return
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(dest_zip, "w", ZIP_DEFLATED) as zf:
        for pattern in include_globs:
            for p in parent.glob(pattern):
                if p.is_file():
                    zf.write(p, p.relative_to(parent).as_posix())
```

```python
# wf_engine/unzip_util.py
from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZipFile


class UnsafeArchiveError(ValueError):
    pass


def extract_zip_safely(data: bytes, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = dest_dir.resolve()
    with ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            target = (dest_dir / name).resolve()
            if base not in target.parents and target != base:
                raise UnsafeArchiveError(name)
            if ".." in Path(name).parts:
                raise UnsafeArchiveError(name)
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
```

- [x] **Step 4: Run tests** — PASS.

- [x] **Step 5: Commit**

---

### Task 6: `Runner` serial loop (in-process tests)

**Files:**

- Create: `wf_engine/runner.py`
- Test: `tests/wf_engine/test_runner_serial.py`
- Test: `tests/wf_engine/test_runner_interrupt.py`

Runner API：

```python
# wf_engine/runner.py  (signature only)
def run_once(
    *,
    store: SqliteStore,
    workflow: Workflow,
    task_id: str,
    task_root: Path,
) -> None:
    ...
```

- [x] **Step 1: `test_runner_serial.py` — 三节顺序成功**

```python
# tests/wf_engine/test_runner_serial.py
from pathlib import Path
import tempfile

from wf_engine.engine import Engine
from wf_engine.paths import task_layout
from wf_engine.runner import run_once
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow
from wf_engine import status as S


def test_three_nodes_linear_success():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        eng = Engine()
        wf = Workflow(key="w1")

        def n1(ctx):
            (ctx.node_workdir / "x1").write_text("1", encoding="utf-8")

        def n2(ctx):
            (ctx.node_workdir / "x2").write_text("2", encoding="utf-8")

        def n3(ctx):
            (ctx.node_workdir / "x3").write_text("3", encoding="utf-8")

        wf.add_node("a", n1, whitelist=["x1"])
        wf.add_node("b", n2, whitelist=["x2"])
        wf.add_node("c", n3, whitelist=["x3"])
        eng.register_workflow(wf)
        tr = Path(td) / "tasks" / "t1"
        tr.mkdir(parents=True)
        layout = task_layout(tr)
        layout.workspace.mkdir(parents=True)
        layout.zips.mkdir(parents=True)
        tid = store.create_task(
            workflow_key="w1",
            workflow_revision="1",
            input_obj={},
            tasks_root=str(Path(td) / "tasks"),
        )
        store.init_task_nodes(tid, ["a", "b", "c"])
        store.set_task_status(tid, S.TASK_RUNNING)
        run_once(store=store, workflow=wf, task_id=tid, task_root=tr)
        assert store.get_task(tid)["status"] == S.TASK_SUCCEEDED
        for o in range(3):
            assert store.list_nodes(tid)[o]["status"] == S.NODE_SUCCESS
```

Run: `pytest tests/wf_engine/test_runner_serial.py -v` — 在 `run_once` 未实现时期待 **FAIL**。

- [x] **Step 2: `test_runner_interrupt.py`**

```python
# tests/wf_engine/test_runner_interrupt.py
from pathlib import Path
import tempfile

from wf_engine import status as S
from wf_engine.context import NodeContext
from wf_engine.interrupt import interrupt
from wf_engine.paths import task_layout
from wf_engine.runner import run_once
from wf_engine.store.sqlite import SqliteStore
from wf_engine.workflow import Workflow


def test_interrupt_then_resolve_completes_node():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        store = SqliteStore(db)
        store.init_schema()
        wf = Workflow(key="w1")

        def n1(ctx: NodeContext):
            (ctx.node_workdir / "done").write_text("ok", encoding="utf-8")

        def n2(ctx: NodeContext):
            if ctx.human_input is not None:
                (ctx.node_workdir / "h.txt").write_text(
                    ctx.human_input["text"], encoding="utf-8"
                )
                return
            interrupt(expected_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})

        wf.add_node("a", n1, whitelist=["done"])
        wf.add_node("b", n2, whitelist=["h.txt"])
        tr = Path(td) / "tasks" / "t1"
        layout = task_layout(tr)
        layout.workspace.mkdir(parents=True)
        layout.zips.mkdir(parents=True)
        tid = store.create_task(
            workflow_key="w1",
            workflow_revision="1",
            input_obj={},
            tasks_root=str(Path(td) / "tasks"),
        )
        store.init_task_nodes(tid, ["a", "b"])
        store.set_task_status(tid, S.TASK_RUNNING)
        run_once(store=store, workflow=wf, task_id=tid, task_root=tr)
        assert store.get_task(tid)["status"] == S.TASK_WAITING_HUMAN
        store.apply_resolve(tid, {"text": "hi"})
        store.set_task_status(tid, S.TASK_RUNNING)
        run_once(store=store, workflow=wf, task_id=tid, task_root=tr)
        assert store.get_task(tid)["status"] == S.TASK_SUCCEEDED
```

Run: `pytest tests/wf_engine/test_runner_interrupt.py -v` — 实现前 **FAIL**。

- [x] **Step 3: 实现 `Runner.run_once`**

以 `wf_engine/runner.py` 实现，**必须**满足：

- 若 `get_task(task_id)["status"] == waiting_human`：立刻 `return`（不向 worker 进程开放）。
- 依 `task_nodes` 顺序找到首个 `status != success` 的 ordinal；若存在未消费的 `interrupt_response_payload` 且 DB 的 `interrupt_node_id` 对应该节点，则构造 `NodeContext(..., human_input=payload)`。
- 捕获 `ControlledInterrupt`：调用 `store.open_interrupt`（并 `store.update_node` 为 `waiting_human`），**不得**封装后再抛。
- 成功：`pack_whitelist_zip`（白名单为空则跳过 zip）、更新 `zip_path`、`success`；最后一节 `task`→`succeeded`。
- 其它异常：`node`+`task`→`failed`，`error_json` 含 `category: business`。

实现完成后使 Step 1–2 测试通过。

- [x] **Step 4: Run** — `pytest tests/wf_engine/test_runner_serial.py tests/wf_engine/test_runner_interrupt.py -v`

- [x] **Step 5: Commit**

---

### Task 7: Worker subprocess entry and supervisor

**Files:**

- Create: `wf_engine/worker_main.py`
- Create: `wf_engine/supervisor.py`
- Test: `tests/wf_engine/test_supervisor_spawn.py` (use `sys.executable -m wf_engine.worker_main` with env `WF_ENGINE_TASK_ID`, minimal argv)

- [x] **Step 1:** `worker_main` loads DB path + tasks root from env or argv, reconstructs `Engine` **without** user workflows — worker must receive **serialized workflow key only** and reload registry: **MVP constraint:** worker subprocess imports a **callback module** path also set by env `WF_ENGINE_REGISTRY_MODULE=yourapp.registry` that calls `register_all(engine)` so user code is loadable. Document in README fragment at end of plan.

Implement `supervisor.spawn_worker(task_id)` using `subprocess.Popen` and `store.acquire_lease`.

- [x] **Step 2: Integration test:** create task in DB, register wf in parent, spawn worker that runs one trivial node; assert task succeeded.

- [x] **Step 3: Commit**

---

### Task 8: FastAPI routes (control plane)

**Files:**

- Create: `wf_engine/server/app.py`
- Create: `wf_engine/server/routes_tasks.py`
- Modify: `wf_engine/engine.py` — add `serve(host, port, tasks_root: Path, db_path: Path)` mounting app
- Test: `tests/wf_engine/test_api_tasks.py` using `httpx.AsyncClient` + `ASGITransport`

Endpoints:

- `POST /tasks` body `{"workflow_key","input"}` → create dirs, insert task, spawn worker, return 201 `{"task_id":...}`.
- `GET /tasks`, `GET /tasks/{id}` with `nodes` array and `interrupt` when waiting.
- `GET /tasks/{id}/logs` tail file with cursor.
- `POST /tasks/{id}/interrupt/resolve` per §3.4.5.
- `POST /tasks/{id}/rerun` — validate not `waiting_human`; clear `workspace`, unzip preds, reset node rows from K, spawn worker.

Use Pydantic models; **409** on illegal transitions.

- [x] **Step 1: Test create + get roundtrip** (mock spawn or use real subprocess if fast enough).

- [x] **Step 2: Commit**

---

### Task 9: Rerun and missing snapshot

**Files:**

- Modify: `wf_engine/server/routes_tasks.py`
- Modify: `wf_engine/store/sqlite.py` — helpers to list successful predecessors' zip paths in order
- Test: `tests/wf_engine/test_rerun_api.py`

- [x] **Step 1: Test rerun clears workspace and restores files from zips.**

- [x] **Step 2: Test missing zip → 409** `missing_snapshot`.

- [x] **Step 3: Commit**

---

### Task 10: React UI (Vite)

**Files:**

- Create: `web/package.json`, `web/vite.config.ts`, `web/index.html`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/api.ts`

- [x] **Step 1: Scaffold** with `npm create vite@latest web -- --template react-ts`.

- [x] **Step 2:** Implement layout: left `TaskList`, right `TaskDetail` with `nodes.map` status chips, polling `GET /api/tasks` and `/api/tasks/:id` every 2s (simple `setInterval`).

- [x] **Step 3:** Log panel: `GET /api/tasks/:id/logs?cursor=`.

- [x] **Step 4:** When `interrupt` present, show raw JSON and a textarea to post `resolve` via `fetch`.

- [x] **Step 5:** Document dev: `uvicorn` on `:8000`, Vite proxy `/api` → `http://127.0.0.1:8000`.

- [x] **Step 6: Commit**

---

### Task 11: Docs and smoke checklist

**Files:**

- Create: `wf_engine/README.md` (short: registry module env, `Engine.serve`, example workflow)

- [x] **Step 1:** Paste minimal example:

```python
from wf_engine import Engine, Workflow, NodeContext, interrupt

def human_gate(ctx: NodeContext):
    if ctx.human_input is not None:
        (ctx.node_workdir / "out.txt").write_text(ctx.human_input["text"], encoding="utf-8")
        return
    interrupt(expected_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})

def build(engine: Engine):
    wf = Workflow(key="demo")
    wf.add_node("g", human_gate, workdir=".", whitelist=["out.txt"])
    engine.register_workflow(wf)
```

- [x] **Step 2: Commit**

---

## Spec coverage checklist (self-review)

| Spec section | Tasks |
|--------------|-------|
| §1–2 范围与对象 | Task 3, 8 |
| §3.1–3.3 进程与 SQLite | 2, 7, 8 |
| §3.4 interrupt | 4, 6, 8 |
| §3.5 路径/租约/rerun 安全等 | 1, 2, 5, 7, 9 |
| §4 白名单 zip | 5, 6 |
| §5 HTTP | 8, 9 |
| §6 UI | 10 |
| §7 模块 | map ↔ files |
| §8 状态机 | 2, 6, 8 (enforce illegal transitions) |
| §9 测试 | embedded per task |

**Gaps addressed in follow-ups if time:** log rotation §3.5.9; maximum zip size; multi-uvicorn guard.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-14-workflow-orchestration-engine.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration  
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints  

Reply with **1** or **2** if you want the plan executed under that mode; otherwise implement tasks manually in order.

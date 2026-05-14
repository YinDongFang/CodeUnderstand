from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, then checkpoint WAL and close (helps Windows file locks)."""
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except sqlite3.OperationalError:
                pass
            conn.close()

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
        clear_finished_at: bool = False,
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
        if clear_finished_at:
            sets.append("finished_at=NULL")
        elif finished_at is not None:
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

    def consume_interrupt_response(self, task_id: str) -> None:
        with self.connect() as c:
            c.execute(
                """UPDATE tasks SET interrupt_response_consumed=1,
                interrupt_response_payload=NULL, updated_at=? WHERE id=?""",
                (_utc_iso(), task_id),
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

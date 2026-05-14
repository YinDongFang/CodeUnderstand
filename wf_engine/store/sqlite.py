from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from wf_engine import status as S
from wf_engine.lease_util import parse_utc_iso, pid_alive


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
                    name TEXT,
                    context_json TEXT NOT NULL DEFAULT '{}',
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
            self._migrate_tasks_table(c)

    @staticmethod
    def _migrate_tasks_table(c: sqlite3.Connection) -> None:
        cols = [str(r[1]) for r in c.execute("PRAGMA table_info(tasks)").fetchall()]
        if "name" not in cols:
            c.execute("ALTER TABLE tasks ADD COLUMN name TEXT")
        if "context_json" not in cols:
            c.execute(
                "ALTER TABLE tasks ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'"
            )

    def create_task(
        self,
        *,
        workflow_key: str,
        workflow_revision: str,
        input_obj: dict[str, Any],
        tasks_root: str,
        name: str | None = None,
        context_obj: dict[str, Any] | None = None,
    ) -> str:
        tid = str(uuid.uuid4())
        now = _utc_iso()
        ctx = {} if context_obj is None else context_obj
        with self.connect() as c:
            c.execute(
                """INSERT INTO tasks
                    (id, workflow_key, workflow_revision, status, input_json, name,
                     context_json, tasks_root,
                     created_at, updated_at, interrupt_seq, interrupt_node_id,
                     interrupt_expected_schema, interrupt_request_extras, interrupt_checkpoint,
                     interrupt_response_payload, interrupt_response_consumed,
                     worker_pid, lease_until, worker_generation)
                    VALUES (?,?,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,0)""",
                (
                    tid,
                    workflow_key,
                    workflow_revision,
                    S.TASK_PENDING,
                    _dumps(input_obj),
                    name,
                    _dumps(ctx),
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
        raw_ctx = d.get("context_json")
        if raw_ctx is None or raw_ctx == "":
            d["context_json"] = {}
        else:
            d["context_json"] = _loads(raw_ctx)
        d["interrupt_response_consumed"] = bool(d["interrupt_response_consumed"])
        for k in (
            "interrupt_expected_schema",
            "interrupt_request_extras",
            "interrupt_checkpoint",
            "interrupt_response_payload",
        ):
            d[k] = _loads(d[k]) if d[k] else None
        return d

    def save_task_context(self, task_id: str, obj: dict[str, Any]) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE tasks SET context_json=?, updated_at=? WHERE id=?",
                (_dumps(obj), _utc_iso(), task_id),
            )

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

    def list_zip_snapshots_before_node(
        self, task_id: str, from_ordinal: int
    ) -> list[tuple[int, str]]:
        """Successful predecessors with on-disk zips, ordered by ordinal (< from_ordinal)."""
        with self.connect() as c:
            rows = c.execute(
                """
                SELECT ordinal, zip_path FROM task_nodes
                WHERE task_id=? AND ordinal<? AND status=? AND zip_path IS NOT NULL
                ORDER BY ordinal ASC
                """,
                (task_id, from_ordinal, S.NODE_SUCCESS),
            ).fetchall()
        return [(int(r["ordinal"]), str(r["zip_path"])) for r in rows]

    def reset_nodes_from_ordinal(self, task_id: str, from_ordinal: int) -> None:
        with self.connect() as c:
            c.execute(
                """UPDATE task_nodes SET status=?, started_at=NULL, finished_at=NULL,
                   zip_path=NULL, error_json=NULL
                   WHERE task_id=? AND ordinal>=?""",
                (S.NODE_PENDING, task_id, from_ordinal),
            )

    def prepare_task_for_rerun_execution(self, task_id: str) -> None:
        now = _utc_iso()
        with self.connect() as c:
            c.execute(
                """UPDATE tasks SET status=?, updated_at=?,
                   worker_generation=worker_generation+1,
                   interrupt_seq=0,
                   interrupt_node_id=NULL, interrupt_expected_schema=NULL,
                   interrupt_request_extras=NULL, interrupt_checkpoint=NULL,
                   interrupt_response_payload=NULL, interrupt_response_consumed=0
                   WHERE id=?""",
                (S.TASK_RUNNING, now, task_id),
            )

    def reconcile_all_running_tasks(self) -> None:
        with self.connect() as c:
            rows = c.execute(
                "SELECT id FROM tasks WHERE status=?",
                (S.TASK_RUNNING,),
            ).fetchall()
        for r in rows:
            self.reconcile_stale_worker_for_task(str(r["id"]))

    def reconcile_stale_worker_for_task(self, task_id: str) -> None:
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        if row is None or row["status"] != S.TASK_RUNNING:
            return
        now = datetime.now(timezone.utc)
        lease_dead = True
        lu = row["lease_until"]
        if lu:
            try:
                lease_dead = now > parse_utc_iso(str(lu))
            except ValueError:
                lease_dead = True
        pid = row["worker_pid"]
        pid_dead = pid is None or not pid_alive(int(pid))
        if not lease_dead and not pid_dead:
            return

        fin = _utc_iso()
        err = _dumps(
            {
                "category": "worker_lost",
                "message": "lease expired or worker process no longer running",
            }
        )
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            n = c.execute(
                "SELECT ordinal FROM task_nodes WHERE task_id=? AND status=?",
                (task_id, S.NODE_RUNNING),
            ).fetchone()
            if n is not None:
                c.execute(
                    """UPDATE task_nodes SET status=?, finished_at=?, error_json=?
                    WHERE task_id=? AND ordinal=?""",
                    (S.NODE_FAILED, fin, err, task_id, int(n["ordinal"])),
                )
            c.execute(
                """UPDATE tasks SET status=?, worker_pid=NULL, lease_until=NULL, updated_at=?
                WHERE id=? AND status=?""",
                (S.TASK_FAILED_STALLED, fin, task_id, S.TASK_RUNNING),
            )
            c.execute("COMMIT")

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

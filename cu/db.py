"""SQLite 持久化：schema 初始化与连接获取。

DB 文件路径：cu.paths.db_path()（默认 $CU_DATA_ROOT/db.sqlite）。
统一通过 get_connection() 上下文管理器获取连接，确保 foreign_keys=ON。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from cu.paths import data_root, db_path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id             TEXT PRIMARY KEY,
  repo               TEXT NOT NULL,
  zip_url            TEXT NOT NULL,
  github_url         TEXT NOT NULL,
  session_id         TEXT NOT NULL DEFAULT '',
  claude_project_dir TEXT NOT NULL DEFAULT '',
  status             TEXT NOT NULL DEFAULT 'pending',
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  notes              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stage_runs (
  job_id      TEXT NOT NULL,
  stage       TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  started_at  TEXT,
  ended_at    TEXT,
  exit_code   INTEGER,
  log_tail    TEXT NOT NULL DEFAULT '',
  attempt     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (job_id, stage),
  FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
"""


def init_db() -> None:
    """创建 DB 文件与 schema（幂等）。"""
    os.makedirs(data_root(), exist_ok=True)
    conn = sqlite3.connect(db_path())
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """返回带 foreign_keys=ON 的连接（上下文管理器，commit+close）。"""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()

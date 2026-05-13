import sqlite3

from cu.db import init_db, get_connection


def test_init_db_creates_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    init_db()
    db_file = tmp_path / "db.sqlite"
    assert db_file.is_file()
    conn = sqlite3.connect(str(db_file))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cur.fetchall()]
    conn.close()
    assert "jobs" in tables
    assert "stage_runs" in tables


def test_init_db_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    init_db()
    init_db()
    assert (tmp_path / "db.sqlite").is_file()


def test_get_connection_enables_fk(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    init_db()
    with get_connection() as conn:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

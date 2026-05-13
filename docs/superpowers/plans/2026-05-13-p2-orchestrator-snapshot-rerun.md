# P2 · 编排器 + 快照 + 重跑 + 删除 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 P1（CLI 端到端 4 阶段）基础上构建持久化编排服务：SQLite 状态机 + 全量 tar 快照 + 从某阶段重跑 + 删除作业 + REST API；为 P3（Web UI）准备好后端。

**Architecture:** 单进程：FastAPI 提供 REST API；每作业一个守护线程跑 P1 的 `STAGE_RUNNERS`，每阶段成功后写 SQLite + 在前 3 个阶段拍快照（tar 整棵假 `$HOME`，落到沙箱外的 `<job_dir>/snapshots/`）；重跑某阶段时先 `tarfile.extractall` 恢复至前阶段快照再执行。删除作业终止线程/子进程并删沙箱、快照、DB 记录。

**Tech Stack:** Python 3.10+ stdlib（`sqlite3`、`tarfile`、`threading`）、FastAPI、uvicorn、pytest、httpx（测试 FastAPI 用）。

---

## 与 P1 的关系

- 不修改 P1 已交付的 `cu/{paths,sandbox,env,runner,stages,cli,__init__,__main__}.py` 中的**对外语义**；可在 `cu/stages.py` 增加可选「进度回调」hook，但默认行为不变。
- `STAGE_RUNNERS` / `JobContext` / `STAGES` 是 P2 编排器的核心依赖。
- P1 的 `cli.py` 单条命令 `cu run <url>` 保留；P2 在其上 **追加** 子命令：`cu list / show / rerun / delete / serve`。
- P3 的 Web UI 复用 P2 的 REST API；本 P2 不写 UI。

---

## 文件结构

### 新建

| 文件 | 职责 |
|------|------|
| `cu/db.py` | SQLite 连接、schema 初始化、低层 CRUD 包装 |
| `cu/models.py` | `JobRecord`、`StageRunRecord` dataclass + 序列化 |
| `cu/state.py` | 状态机转换规则（阶段先后、可执行性判定） |
| `cu/snapshot.py` | tar 拍快照与恢复（含 exclude 自身路径） |
| `cu/orchestrator.py` | `Orchestrator` 单例：`create_job` / `run_stage` / `rerun_from` / `delete_job` / 线程管理 |
| `cu/api.py` | FastAPI 应用：路由、依赖注入、Pydantic schema |
| `cu/serve.py` | `cu serve` 入口：构造 app + uvicorn.run |
| `scripts/tar_sandbox.sh` | 可选独立 bash 包装（暂不用，仅占位；不在 P2 范围实现） |
| `tests/test_db.py` | DB schema 与基础 CRUD |
| `tests/test_models.py` | dataclass 序列化 |
| `tests/test_state.py` | 状态机判定逻辑 |
| `tests/test_snapshot.py` | tar 拍-恢复幂等 |
| `tests/test_orchestrator.py` | mock STAGE_RUNNERS 验证编排顺序、重跑、删除 |
| `tests/test_api.py` | FastAPI 路由（httpx + TestClient） |

### 修改

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | 增加 `[project.dependencies]`：`fastapi`、`uvicorn`、`httpx`（测试用） |
| `cu/cli.py` | 增加子命令 `list` / `show` / `rerun` / `delete` / `serve`；保留 `run`；`run` 内部改走 `Orchestrator`（保持 stdout 行为兼容） |
| `cu/stages.py` | 增加可选 `on_event: Callable[[str, str], None] | None` 钩子，编排器可订阅 stage 内部进度；默认 None 时不改变行为 |

---

## 数据模型（SQLite schema）

> **必读**：以下 schema 是 Task 2 的设计稿；实现按此 schema 创建表。

```sql
CREATE TABLE IF NOT EXISTS jobs (
  job_id        TEXT PRIMARY KEY,
  repo          TEXT NOT NULL,
  zip_url       TEXT NOT NULL,
  github_url    TEXT NOT NULL,
  session_id    TEXT NOT NULL DEFAULT '',
  claude_project_dir TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending|running|success|failed|cancelled
  created_at    TEXT NOT NULL,                    -- ISO8601 UTC
  updated_at    TEXT NOT NULL,
  notes         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stage_runs (
  job_id        TEXT NOT NULL,
  stage         TEXT NOT NULL,                    -- bootstrap|conversation|compile|build
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending|running|success|failed|cancelled
  started_at    TEXT,                              -- nullable
  ended_at      TEXT,                              -- nullable
  exit_code     INTEGER,
  log_tail      TEXT NOT NULL DEFAULT '',         -- 最近 2000 字符 stderr/stdout
  attempt       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (job_id, stage),
  FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
```

**字段说明**：
- `jobs.status`：作业整体状态。任一阶段 running 则作业 running；最末阶段（`build`）success 则作业 success；任一阶段 failed 则作业 failed；用户调用 cancel 则 cancelled。
- `stage_runs.attempt`：重跑该阶段时 +1，便于审计。
- `stage_runs.log_tail`：阶段失败时存 stderr 末段，方便 `cu show` 不读全文件就能展示错误概况。完整日志仍由 P1 的 bash/python 各自写到沙箱内 `logs/`、`loop_logs/`。

---

## 状态机规则

```
阶段顺序：bootstrap → conversation → compile → build

可执行性判定（state.py 的 can_run_stage）：
- bootstrap：始终可执行（不依赖前序）
- conversation：仅当 bootstrap 状态为 success
- compile：仅当 conversation 状态为 success
- build：仅当 compile 状态为 success

可重跑性（can_rerun_stage）：
- 任何阶段都可重跑，前提是 **该阶段所属前序阶段** 都是 success（即依赖满足）
- 重跑 bootstrap 时无前置快照，需要显式空壳重置（删 home + snapshots，再创建空骨架）

重跑副作用：
- 重跑阶段 N 时，将 N 及其后所有阶段状态置为 pending（attempt 不重置，仅在新一次运行成功/失败时 +1）
- 恢复：N != bootstrap 时，先 tarfile.extractall(snapshots/post-<prev(N)>.tar) 覆盖 home/
```

---

## 快照规则

- **何时拍**：`bootstrap`、`conversation`、`compile` 三个阶段 success 之后立即拍；`build` 后**不**拍。
- **路径**：`<job_dir>/snapshots/post-<stage>.tar`（无压缩，便于幂等 extractall）。
- **包含**：整棵 `<job_dir>/home/`（即假 `$HOME`）。
- **排除**：无（snapshots/ 已经在 home 之外，不存在自包含风险）。
- **格式**：`tarfile.open(..., "w")` 无压缩 tar；`add(home, arcname="home")`。

**恢复**：删 `home/` → `tarfile.open(...).extractall(<job_dir>)` 把 `home/` 还原。

---

## 并发模型

- 一个 `Orchestrator` 单例（模块级）。
- 每作业一个 `threading.Thread`（daemon=True），线程内顺序执行 `STAGE_RUNNERS[stage]` + 拍快照。
- 线程间通过 SQLite 串行化更新作业/阶段记录（每次更新前 `BEGIN IMMEDIATE`）。
- 内存 dict：`{job_id: (thread, current_subproc_pid: int | None, cancel_flag: threading.Event)}`。
- 取消：`cancel_flag.set()` + 若有子进程则 `os.killpg(pid, SIGTERM)`（Linux）；阶段间检查 cancel_flag。

> P1 的 `cu.runner.run_script`/`run_python` 返回 RunResult 即返回，无 PID 暴露。**Task 7** 中改 runner 增加可选 `pid_sink: Callable[[int], None] | None`，让编排器拿到 PID 用于 cancel。

---

## REST API

> 路径前缀 `/api/v1/`。Pydantic 模型见 `cu/api.py`。

| 方法 | 路径 | 行为 |
|------|------|------|
| `POST` | `/api/v1/jobs` | 创建作业；body：`{zip_url: str}`；返回 `JobRecord`，状态 `pending`，不自动启动 |
| `GET` | `/api/v1/jobs` | 列表，支持 `?status=running` 过滤 |
| `GET` | `/api/v1/jobs/{job_id}` | 详情（含 4 阶段记录） |
| `POST` | `/api/v1/jobs/{job_id}/stages/{stage}/run` | 触发执行该阶段；依赖未满足 → 409 |
| `POST` | `/api/v1/jobs/{job_id}/stages/{stage}/rerun` | 重跑该阶段（恢复快照 + 再跑）；依赖未满足 → 409 |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | 取消正在跑的作业 |
| `DELETE` | `/api/v1/jobs/{job_id}` | 删除作业（终止线程 + 清沙箱 + 清快照 + 删 DB 记录） |

P3 会增加 SSE `/api/v1/jobs/{job_id}/events`，本 P2 **不实现** SSE。

---

## Task 列表（17 个）

### Task 1: pyproject.toml — 增加 FastAPI/uvicorn/httpx 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 修改 pyproject.toml**

在 `[project]` 后追加：

```toml
dependencies = [
  "fastapi>=0.115",
  "uvicorn>=0.32",
]

[project.optional-dependencies]
test = ["pytest>=8.0", "httpx>=0.27"]
```

- [ ] **Step 2: 安装依赖**

Run: `python -m pip install -e ".[test]"`
Expected: 成功；`python -c "import fastapi, uvicorn, httpx; print(fastapi.__version__)"` 输出版本号。

- [ ] **Step 3: 跑现有测试**

Run: `python -m pytest tests/ -v -m "not slow"`
Expected: 16 passed, 1 deselected（与 P1 一致）。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add FastAPI/uvicorn/httpx dependencies"
```

---

### Task 2: cu/db.py — SQLite schema 与连接

**Files:**
- Create: `cu/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: 先写测试 tests/test_db.py**

```python
import os
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
    init_db()  # 再次调用不应抛错
    assert (tmp_path / "db.sqlite").is_file()


def test_get_connection_enables_fk(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    init_db()
    with get_connection() as conn:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cu.db'`

- [ ] **Step 3: 写 cu/db.py**

```python
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
    """返回带 foreign_keys=ON 的连接（上下文管理器）。"""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/test_db.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add cu/db.py tests/test_db.py
git commit -m "feat(db): add SQLite schema and connection helpers"
```

---

### Task 3: cu/models.py — JobRecord / StageRunRecord dataclass

**Files:**
- Create: `cu/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写 tests/test_models.py**

```python
from datetime import datetime, timezone

from cu.models import JobRecord, StageRunRecord, JOB_STAGES


def test_job_record_to_dict_roundtrip():
    now = "2026-05-13T10:00:00+00:00"
    j = JobRecord(
        job_id="abc", repo="r", zip_url="u", github_url="g",
        session_id="", claude_project_dir="",
        status="pending", created_at=now, updated_at=now, notes="",
    )
    d = j.to_dict()
    j2 = JobRecord.from_row(d)
    assert j2 == j


def test_stage_run_record_default():
    s = StageRunRecord(job_id="abc", stage="bootstrap")
    assert s.status == "pending"
    assert s.attempt == 0
    assert s.exit_code is None


def test_job_stages_constant():
    assert JOB_STAGES == ("bootstrap", "conversation", "compile", "build")
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 写 cu/models.py**

```python
"""作业与阶段记录 dataclass + 字典互转。"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping, Optional


JOB_STAGES: tuple[str, str, str, str] = ("bootstrap", "conversation", "compile", "build")

JOB_STATUSES = ("pending", "running", "success", "failed", "cancelled")
STAGE_STATUSES = ("pending", "running", "success", "failed", "cancelled")


@dataclass
class JobRecord:
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str
    claude_project_dir: str
    status: str
    created_at: str
    updated_at: str
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping) -> "JobRecord":
        return cls(
            job_id=row["job_id"],
            repo=row["repo"],
            zip_url=row["zip_url"],
            github_url=row["github_url"],
            session_id=row["session_id"],
            claude_project_dir=row["claude_project_dir"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            notes=row.get("notes", "") if hasattr(row, "get") else row["notes"],
        )


@dataclass
class StageRunRecord:
    job_id: str
    stage: str
    status: str = "pending"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    log_tail: str = ""
    attempt: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping) -> "StageRunRecord":
        return cls(
            job_id=row["job_id"],
            stage=row["stage"],
            status=row["status"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            exit_code=row["exit_code"],
            log_tail=row["log_tail"],
            attempt=row["attempt"],
        )
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/test_models.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add cu/models.py tests/test_models.py
git commit -m "feat(models): add JobRecord and StageRunRecord dataclasses"
```

---

### Task 4: cu/state.py — 状态机规则

**Files:**
- Create: `cu/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: 写 tests/test_state.py**

```python
import pytest

from cu.state import can_run_stage, can_rerun_stage, next_pending_stage


def test_can_run_bootstrap_with_no_history():
    assert can_run_stage("bootstrap", stage_status={}) is True


def test_can_run_conversation_only_after_bootstrap_success():
    assert can_run_stage("conversation", stage_status={"bootstrap": "success"}) is True
    assert can_run_stage("conversation", stage_status={"bootstrap": "pending"}) is False
    assert can_run_stage("conversation", stage_status={"bootstrap": "failed"}) is False


def test_can_rerun_compile_requires_conversation_success():
    assert can_rerun_stage(
        "compile",
        stage_status={"bootstrap": "success", "conversation": "success", "compile": "failed"},
    ) is True
    assert can_rerun_stage(
        "compile",
        stage_status={"bootstrap": "success", "conversation": "pending"},
    ) is False


def test_next_pending_returns_first_runnable():
    s = {"bootstrap": "success", "conversation": "success", "compile": "pending", "build": "pending"}
    assert next_pending_stage(s) == "compile"


def test_next_pending_none_when_all_done():
    s = {"bootstrap": "success", "conversation": "success", "compile": "success", "build": "success"}
    assert next_pending_stage(s) is None
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 写 cu/state.py**

```python
"""阶段状态机规则。"""
from __future__ import annotations

from cu.models import JOB_STAGES


def _prev_stage(stage: str) -> str | None:
    idx = JOB_STAGES.index(stage)
    if idx == 0:
        return None
    return JOB_STAGES[idx - 1]


def can_run_stage(stage: str, stage_status: dict[str, str]) -> bool:
    """是否允许执行该阶段（依赖：所有前序阶段须为 success）。"""
    if stage not in JOB_STAGES:
        return False
    prev = _prev_stage(stage)
    if prev is None:
        return True
    return stage_status.get(prev) == "success"


def can_rerun_stage(stage: str, stage_status: dict[str, str]) -> bool:
    """重跑判定：与 can_run 相同（依赖前序 success），不关心该阶段当前状态。"""
    return can_run_stage(stage, stage_status)


def next_pending_stage(stage_status: dict[str, str]) -> str | None:
    """返回下一个可执行的 pending 阶段；无则 None。"""
    for s in JOB_STAGES:
        if stage_status.get(s) != "success":
            if can_run_stage(s, stage_status):
                return s
            return None
    return None


def stages_after(stage: str) -> tuple[str, ...]:
    """返回该阶段（含）之后的所有阶段，用于重跑时把后续置 pending。"""
    if stage not in JOB_STAGES:
        return ()
    idx = JOB_STAGES.index(stage)
    return JOB_STAGES[idx:]
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/test_state.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add cu/state.py tests/test_state.py
git commit -m "feat(state): add stage dependency state machine"
```

---

### Task 5: cu/snapshot.py — tar 拍快照 / 恢复

**Files:**
- Create: `cu/snapshot.py`
- Create: `tests/test_snapshot.py`

- [ ] **Step 1: 写 tests/test_snapshot.py**

```python
import os

from cu.snapshot import save_snapshot, restore_snapshot


def test_save_and_restore_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    job_id = "snap-001"

    home = tmp_path / "jobs" / job_id / "home"
    home.mkdir(parents=True)
    (home / "file.txt").write_text("hello")
    (home / "sub").mkdir()
    (home / "sub" / "data.bin").write_bytes(b"\x00\x01\x02")

    save_snapshot(job_id, "bootstrap")

    snap = tmp_path / "jobs" / job_id / "snapshots" / "post-bootstrap.tar"
    assert snap.is_file() and snap.stat().st_size > 0

    (home / "file.txt").write_text("changed")
    (home / "new.txt").write_text("extra")

    restore_snapshot(job_id, "bootstrap")
    assert (home / "file.txt").read_text() == "hello"
    assert (home / "sub" / "data.bin").read_bytes() == b"\x00\x01\x02"
    assert not (home / "new.txt").exists()


def test_save_snapshot_missing_home_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    import pytest
    with pytest.raises(FileNotFoundError):
        save_snapshot("nope", "bootstrap")


def test_restore_missing_snapshot_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    import pytest
    (tmp_path / "jobs" / "j" / "home").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        restore_snapshot("j", "bootstrap")
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 写 cu/snapshot.py**

```python
"""全量 tar 快照与恢复（spec §6）。

快照路径：<job_dir>/snapshots/post-<stage>.tar（无压缩）
内容：整棵 <job_dir>/home/（arcname='home'）
恢复：先删 <job_dir>/home/，再 extractall 回 <job_dir>。
"""
from __future__ import annotations

import os
import shutil
import tarfile

from cu.paths import job_dir, sandbox_home, snapshots_dir


def _snapshot_path(job_id: str, stage: str) -> str:
    return os.path.join(snapshots_dir(job_id), f"post-{stage}.tar")


def save_snapshot(job_id: str, stage: str) -> str:
    """对 <job_dir>/home/ 整树打 tar。返回 tar 绝对路径。"""
    home = sandbox_home(job_id)
    if not os.path.isdir(home):
        raise FileNotFoundError(f"沙箱 home 不存在: {home}")
    snap_dir = snapshots_dir(job_id)
    os.makedirs(snap_dir, exist_ok=True)
    out = _snapshot_path(job_id, stage)
    with tarfile.open(out, "w") as tar:
        tar.add(home, arcname="home")
    return out


def restore_snapshot(job_id: str, stage: str) -> None:
    """用 post-<stage>.tar 覆盖恢复 <job_dir>/home/。"""
    out = _snapshot_path(job_id, stage)
    if not os.path.isfile(out):
        raise FileNotFoundError(f"快照不存在: {out}")
    home = sandbox_home(job_id)
    if os.path.isdir(home):
        shutil.rmtree(home)
    with tarfile.open(out, "r") as tar:
        tar.extractall(job_dir(job_id))


def snapshot_exists(job_id: str, stage: str) -> bool:
    return os.path.isfile(_snapshot_path(job_id, stage))


def delete_snapshots(job_id: str) -> None:
    """删除指定 job 的所有快照（用于 delete_job）。"""
    snap_dir = snapshots_dir(job_id)
    if os.path.isdir(snap_dir):
        shutil.rmtree(snap_dir)
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/test_snapshot.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add cu/snapshot.py tests/test_snapshot.py
git commit -m "feat(snapshot): add tar-based sandbox snapshot save/restore"
```

---

### Task 6: cu/runner.py — 暴露子进程 PID

**Files:**
- Modify: `cu/runner.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1: 修改 cu/runner.py**

把 `run_script` 与 `run_python` 的签名都增加 `pid_sink: Callable[[int], None] | None = None`。逻辑：从 `subprocess.run` 改为 `subprocess.Popen`，启动后立即调用 `pid_sink(proc.pid)`（若提供）；再等待 `proc.communicate(timeout=timeout)` 收尾。

为最小侵入，**保留** 非 stream / 非 pid_sink 情况下的旧行为；当 `pid_sink` 提供或 `stream=True` 时走新分支。代码：

```python
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def _run(
    cmd: list[str],
    *,
    env: dict[str, str] | None,
    cwd: str | None,
    timeout: int | None,
    stream: bool,
    pid_sink: Optional[Callable[[int], None]],
) -> RunResult:
    if stream:
        # 不收 stdout/stderr，实时透传
        try:
            proc = subprocess.Popen(cmd, env=env, cwd=cwd)
            if pid_sink:
                pid_sink(proc.pid)
            rc = proc.wait(timeout=timeout)
            return RunResult(rc, "", "")
        except subprocess.TimeoutExpired:
            proc.kill()
            return RunResult(-1, "", f"timeout after {timeout}s")
    # capture 模式
    try:
        proc = subprocess.Popen(
            cmd, env=env, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if pid_sink:
            pid_sink(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            return RunResult(-1, stdout or "", (stderr or "") + f"\ntimeout after {timeout}s")
        return RunResult(proc.returncode, stdout, stderr)
    except FileNotFoundError as e:
        return RunResult(127, "", str(e))


def run_script(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    stream: bool = False,
    pid_sink: Optional[Callable[[int], None]] = None,
) -> RunResult:
    return _run(
        ["bash", script] + (args or []),
        env=env, cwd=cwd, timeout=timeout,
        stream=stream, pid_sink=pid_sink,
    )


def run_python(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    stream: bool = False,
    pid_sink: Optional[Callable[[int], None]] = None,
) -> RunResult:
    return _run(
        ["python3", script] + (args or []),
        env=env, cwd=cwd, timeout=timeout,
        stream=stream, pid_sink=pid_sink,
    )
```

- [ ] **Step 2: 在 tests/test_runner.py 末尾追加一条 pid_sink 测试**

```python
def test_run_script_pid_sink_called(tmp_path):
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/bash\necho hello\n")
    script.chmod(0o755)
    captured = []
    result = run_script(
        str(script), env=None, cwd=str(tmp_path), timeout=10,
        pid_sink=lambda pid: captured.append(pid),
    )
    assert result.returncode == 0
    assert len(captured) == 1 and isinstance(captured[0], int) and captured[0] > 0
```

需要在文件顶部 import `run_script`（如已 import 则跳过）。

- [ ] **Step 3: 跑测试**

Run: `python -m pytest tests/test_runner.py -v`
Expected: 3 passed（原 2 + 新 1）。

- [ ] **Step 4: Commit**

```bash
git add cu/runner.py tests/test_runner.py
git commit -m "feat(runner): expose subprocess PID via pid_sink callback"
```

---

### Task 7: cu/stages.py — on_event 钩子

**Files:**
- Modify: `cu/stages.py`

- [ ] **Step 1: 在 stages.py 增加可选 on_event 与 pid_sink 透传**

`JobContext` 增加可选字段：

```python
@dataclass
class JobContext:
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str = ""
    claude_project_dir: str = ""
    on_event: Optional[Callable[[str, str], None]] = None  # (stage, message)
    pid_sink: Optional[Callable[[int], None]] = None
```

并在文件顶部 import：
```python
from typing import Callable, Optional
```

- [ ] **Step 2: 在每次 `run_script` / `run_python` 调用处透传 `pid_sink=ctx.pid_sink`**

注意保留原有 `stream=True` 参数。

- [ ] **Step 3: 在每个阶段函数起始处调 `on_event`**

例如 `run_bootstrap` 开头：
```python
    if ctx.on_event:
        ctx.on_event("bootstrap", "start")
```
末尾（成功结束）：
```python
    if ctx.on_event:
        ctx.on_event("bootstrap", "done")
```

每个阶段（4 个）都加。失败由调用方（编排器）通过 `_check` 抛 RuntimeError 后捕获并自行记 on_event 即可，**stages.py 内不 try/except**。

- [ ] **Step 4: 跑现有测试**

Run: `python -m pytest tests/ -v -m "not slow"`
Expected: 全部仍通过（on_event 默认 None，行为不变）。

- [ ] **Step 5: Commit**

```bash
git add cu/stages.py
git commit -m "feat(stages): add on_event hook and pid_sink passthrough"
```

---

### Task 8: cu/orchestrator.py — 编排核心

**Files:**
- Create: `cu/orchestrator.py`

> 本任务文件较长但全为机械实现；测试在 Task 9。

- [ ] **Step 1: 创建 cu/orchestrator.py**

```python
"""Orchestrator：作业生命周期与状态机持久化。

约束：
- 一个进程内单例（模块级 _ACTIVE 字典管理线程与 PID）。
- 每作业一个守护线程顺序跑 4 阶段。
- 每阶段：状态置 running → 调 STAGE_RUNNERS[stage](ctx) → 成功置 success + 拍快照（除 build）；失败置 failed。
- 失败/取消时停止后续阶段。
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from cu.db import get_connection, init_db
from cu.models import JobRecord, StageRunRecord, JOB_STAGES
from cu.paths import job_dir
from cu.snapshot import (
    save_snapshot,
    restore_snapshot,
    snapshot_exists,
    delete_snapshots,
)
from cu.stages import JobContext, STAGE_RUNNERS
from cu.state import can_run_stage, stages_after


_ZIP_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip$"
)


@dataclass
class _ActiveJob:
    thread: threading.Thread
    cancel_flag: threading.Event
    current_pid: list[int]  # mutable container so callbacks can update it


_ACTIVE: dict[str, _ActiveJob] = {}
_ACTIVE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_github_url(zip_url: str) -> tuple[str, str]:
    m = _ZIP_URL_RE.match(zip_url)
    if not m:
        raise ValueError(f"URL 不符合 GitHub archive ZIP 格式: {zip_url}")
    return m.group(1), m.group(2)


# ---------- DB helpers ----------

def _insert_job(rec: JobRecord) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO jobs(job_id, repo, zip_url, github_url, session_id,
                                claude_project_dir, status, created_at, updated_at, notes)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (rec.job_id, rec.repo, rec.zip_url, rec.github_url, rec.session_id,
             rec.claude_project_dir, rec.status, rec.created_at, rec.updated_at, rec.notes),
        )
        for stage in JOB_STAGES:
            conn.execute(
                "INSERT INTO stage_runs(job_id, stage) VALUES(?,?)",
                (rec.job_id, stage),
            )


def _get_job(job_id: str) -> JobRecord | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return JobRecord.from_row(row) if row else None


def _get_stages(job_id: str) -> dict[str, StageRunRecord]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM stage_runs WHERE job_id=?", (job_id,)
        ).fetchall()
        return {row["stage"]: StageRunRecord.from_row(row) for row in rows}


def _update_job(job_id: str, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", vals)


def _update_stage(job_id: str, stage: str, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id, stage]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE stage_runs SET {cols} WHERE job_id=? AND stage=?",
            vals,
        )


def _stage_status_map(job_id: str) -> dict[str, str]:
    return {s: r.status for s, r in _get_stages(job_id).items()}


def _reset_stage(job_id: str, stage: str) -> None:
    _update_stage(
        job_id, stage,
        status="pending",
        started_at=None, ended_at=None,
        exit_code=None, log_tail="",
    )


# ---------- Public API ----------

def ensure_db() -> None:
    init_db()


def create_job(zip_url: str, *, job_id: str | None = None, notes: str = "") -> JobRecord:
    ensure_db()
    _gh_user, repo = _parse_github_url(zip_url)
    job_id = job_id or f"{repo}-{uuid.uuid4().hex[:8]}"
    rec = JobRecord(
        job_id=job_id, repo=repo, zip_url=zip_url,
        github_url=f"https://github.com/{_gh_user}/{repo}",
        session_id="", claude_project_dir="",
        status="pending",
        created_at=_now(), updated_at=_now(),
        notes=notes,
    )
    _insert_job(rec)
    return rec


def list_jobs(status: str | None = None) -> list[JobRecord]:
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [JobRecord.from_row(r) for r in rows]


def get_job(job_id: str) -> JobRecord | None:
    return _get_job(job_id)


def get_stages(job_id: str) -> dict[str, StageRunRecord]:
    return _get_stages(job_id)


def run_stage(job_id: str, stage: str) -> threading.Thread:
    """启动指定阶段执行（后台线程）。依赖未满足抛 ValueError。"""
    rec = _get_job(job_id)
    if rec is None:
        raise KeyError(f"job not found: {job_id}")
    status_map = _stage_status_map(job_id)
    if not can_run_stage(stage, status_map):
        raise ValueError(f"dependencies not met for stage {stage}: {status_map}")
    return _start_job_thread(job_id, start_from=stage, restore=False)


def rerun_from(job_id: str, stage: str) -> threading.Thread:
    """从 stage 重跑：恢复 post-prev 快照（若非 bootstrap）→ 置 stage..build 为 pending → 启动。"""
    rec = _get_job(job_id)
    if rec is None:
        raise KeyError(f"job not found: {job_id}")
    status_map = _stage_status_map(job_id)
    if not can_run_stage(stage, status_map):
        raise ValueError(f"dependencies not met for rerun {stage}: {status_map}")

    if stage == "bootstrap":
        # 显式空壳重置
        d = job_dir(job_id)
        home = os.path.join(d, "home")
        snaps = os.path.join(d, "snapshots")
        for p in (home, snaps):
            if os.path.isdir(p):
                shutil.rmtree(p)
    else:
        prev = JOB_STAGES[JOB_STAGES.index(stage) - 1]
        if not snapshot_exists(job_id, prev):
            raise ValueError(f"missing snapshot post-{prev} for rerun {stage}")
        restore_snapshot(job_id, prev)

    for s in stages_after(stage):
        _reset_stage(job_id, s)
    return _start_job_thread(job_id, start_from=stage, restore=False)


def cancel(job_id: str) -> bool:
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(job_id)
    if not active:
        return False
    active.cancel_flag.set()
    for pid in list(active.current_pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _update_job(job_id, status="cancelled")
    return True


def delete_job(job_id: str) -> bool:
    cancel(job_id)
    with _ACTIVE_LOCK:
        active = _ACTIVE.pop(job_id, None)
    if active is not None:
        active.thread.join(timeout=5)
    d = job_dir(job_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
        return cur.rowcount > 0


# ---------- Thread runner ----------

def _start_job_thread(job_id: str, *, start_from: str, restore: bool) -> threading.Thread:
    with _ACTIVE_LOCK:
        existing = _ACTIVE.get(job_id)
        if existing and existing.thread.is_alive():
            raise RuntimeError(f"job {job_id} 已在运行")
        cancel_flag = threading.Event()
        pid_holder: list[int] = []
        t = threading.Thread(
            target=_run_thread,
            args=(job_id, start_from, cancel_flag, pid_holder),
            daemon=True,
        )
        _ACTIVE[job_id] = _ActiveJob(thread=t, cancel_flag=cancel_flag, current_pid=pid_holder)
        t.start()
        return t


def _pid_sink_factory(holder: list[int]):
    def sink(pid: int) -> None:
        holder.append(pid)
    return sink


def _run_thread(job_id: str, start_from: str, cancel_flag: threading.Event, pid_holder: list[int]) -> None:
    rec = _get_job(job_id)
    if rec is None:
        return
    _update_job(job_id, status="running")

    ctx = JobContext(
        job_id=rec.job_id,
        repo=rec.repo,
        zip_url=rec.zip_url,
        github_url=rec.github_url,
        session_id=rec.session_id,
        claude_project_dir=rec.claude_project_dir,
        pid_sink=_pid_sink_factory(pid_holder),
    )

    idx_start = JOB_STAGES.index(start_from)
    for stage in JOB_STAGES[idx_start:]:
        if cancel_flag.is_set():
            _update_stage(job_id, stage, status="cancelled", ended_at=_now())
            _update_job(job_id, status="cancelled")
            return

        attempt = _get_stages(job_id)[stage].attempt + 1
        _update_stage(
            job_id, stage,
            status="running", started_at=_now(),
            ended_at=None, exit_code=None, log_tail="",
            attempt=attempt,
        )
        pid_holder.clear()
        try:
            STAGE_RUNNERS[stage](ctx)
        except Exception as e:
            log_tail = str(e)[-2000:]
            _update_stage(
                job_id, stage,
                status="failed", ended_at=_now(),
                log_tail=log_tail,
            )
            _update_job(job_id, status="failed", notes=log_tail[:500])
            return

        _update_stage(job_id, stage, status="success", ended_at=_now())

        # ctx 字段可能被 stages 写入（session_id / claude_project_dir），同步回 DB
        _update_job(
            job_id,
            session_id=ctx.session_id,
            claude_project_dir=ctx.claude_project_dir,
        )

        # 拍快照（除 build）
        if stage != "build":
            save_snapshot(job_id, stage)

    _update_job(job_id, status="success")


def is_running(job_id: str) -> bool:
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(job_id)
        return bool(active and active.thread.is_alive())
```

- [ ] **Step 2: 语法检查**

Run: `python -m py_compile cu/orchestrator.py`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add cu/orchestrator.py
git commit -m "feat(orchestrator): add job lifecycle with state machine and snapshots"
```

---

### Task 9: tests/test_orchestrator.py — 编排器集成测试

**Files:**
- Create: `tests/test_orchestrator.py`

- [ ] **Step 1: 写测试（mock STAGE_RUNNERS）**

```python
"""Orchestrator 集成测试：mock STAGE_RUNNERS，验证状态流转与快照行为。"""
import os
import time
from unittest.mock import patch

import pytest

from cu import orchestrator as orch
from cu.models import JOB_STAGES


URL = "https://github.com/u/demo-repo/archive/refs/heads/main.zip"


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "cu"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".claude").mkdir()
    orch.ensure_db()
    with orch._ACTIVE_LOCK:
        orch._ACTIVE.clear()
    return tmp_path


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _fake_stage_runner(ctx):
    from cu.paths import sandbox_home
    home = sandbox_home(ctx.job_id)
    os.makedirs(home, exist_ok=True)


def test_create_job_inserts_records(db_env):
    rec = orch.create_job(URL, job_id="j1")
    assert rec.job_id == "j1"
    assert rec.status == "pending"
    stages = orch.get_stages("j1")
    assert set(stages.keys()) == set(JOB_STAGES)
    for s in JOB_STAGES:
        assert stages[s].status == "pending"


def test_run_all_stages_success(db_env):
    orch.create_job(URL, job_id="j2")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        t = orch.run_stage("j2", "bootstrap")
        t.join(timeout=5)
    job = orch.get_job("j2")
    assert job.status == "success"
    stages = orch.get_stages("j2")
    for s in JOB_STAGES:
        assert stages[s].status == "success", f"{s} not success: {stages[s]}"
    # 快照：前 3 个有，build 没
    from cu.snapshot import snapshot_exists
    assert snapshot_exists("j2", "bootstrap")
    assert snapshot_exists("j2", "conversation")
    assert snapshot_exists("j2", "compile")
    assert not snapshot_exists("j2", "build")


def test_stage_failure_stops_chain(db_env):
    orch.create_job(URL, job_id="j3")

    def boom(ctx):
        raise RuntimeError("kaboom")

    runners = {s: _fake_stage_runner for s in JOB_STAGES}
    runners["conversation"] = boom
    with patch.dict(orch.STAGE_RUNNERS, runners, clear=False):
        t = orch.run_stage("j3", "bootstrap")
        t.join(timeout=5)
    job = orch.get_job("j3")
    assert job.status == "failed"
    stages = orch.get_stages("j3")
    assert stages["bootstrap"].status == "success"
    assert stages["conversation"].status == "failed"
    assert stages["compile"].status == "pending"
    assert "kaboom" in stages["conversation"].log_tail


def test_rerun_restores_snapshot(db_env):
    orch.create_job(URL, job_id="j4")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.run_stage("j4", "bootstrap").join(timeout=5)

    # 污染 home，再 rerun compile：应从 post-conversation 恢复
    from cu.paths import sandbox_home
    home = sandbox_home("j4")
    dirty = os.path.join(home, "dirty.txt")
    with open(dirty, "w") as f:
        f.write("noise")

    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.rerun_from("j4", "compile").join(timeout=5)

    assert not os.path.exists(dirty), "rerun_from compile 应恢复至 post-conversation 快照"
    stages = orch.get_stages("j4")
    assert stages["compile"].attempt == 2
    assert stages["build"].attempt == 2


def test_delete_job(db_env):
    orch.create_job(URL, job_id="j5")
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_stage_runner for s in JOB_STAGES},
        clear=False,
    ):
        orch.run_stage("j5", "bootstrap").join(timeout=5)
    from cu.paths import job_dir
    d = job_dir("j5")
    assert os.path.isdir(d)
    assert orch.delete_job("j5") is True
    assert not os.path.isdir(d)
    assert orch.get_job("j5") is None
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: 5 passed

> **Windows 注意**：测试在 Windows 上跑可能因 `signal.SIGTERM`/`os.kill` 语义差异在 `cancel` 路径出问题，但本测试套不调 cancel，应能通过。若 `tarfile` 在 Windows 上对路径 mode 有警告也不影响断言。

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator.py
git commit -m "test: add orchestrator integration tests for run/rerun/delete"
```

---

### Task 10: cu/cli.py — 增加 list/show/rerun/delete/serve 子命令

**Files:**
- Modify: `cu/cli.py`

- [ ] **Step 1: 重构 cu/cli.py**

保留 `run` 子命令并改为：内部调 `orch.create_job` + `orch.run_stage("bootstrap")` 并 `thread.join()` 等待，期间打印进度（按现有 `run_stage` 已经在线程内打日志，CLI 仅等待并最后打 status）。

新增子命令：
- `cu list [--status STATUS]`：表格输出 jobs（job_id、repo、status、created_at）。
- `cu show <job_id>`：详情（含 4 阶段状态、时间、log_tail）。
- `cu rerun <job_id> <stage>`：调 `orch.rerun_from`，join 等待。
- `cu delete <job_id>`：调 `orch.delete_job`。
- `cu serve [--host 127.0.0.1] [--port 8765]`：导入 `cu.serve.main(host, port)` 并调用（实际启动在 Task 11）。

完整代码：

```python
"""CLI 入口：python -m cu <command>"""
from __future__ import annotations

import argparse
import sys

from cu import orchestrator as orch
from cu.models import JOB_STAGES


def cmd_run(args: argparse.Namespace) -> int:
    try:
        rec = orch.create_job(args.zip_url, job_id=args.job_id)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    print(f"创建作业 {rec.job_id}（repo={rec.repo}）")
    start = args.start or "bootstrap"
    t = orch.run_stage(rec.job_id, start)
    t.join()
    job = orch.get_job(rec.job_id)
    print(f"作业 {rec.job_id} 终态：{job.status}")
    return 0 if job.status == "success" else 1


def cmd_list(args: argparse.Namespace) -> int:
    rows = orch.list_jobs(status=args.status)
    if not rows:
        print("（无作业）")
        return 0
    print(f"{'job_id':<24}  {'repo':<24}  {'status':<10}  created_at")
    print("-" * 80)
    for r in rows:
        print(f"{r.job_id:<24}  {r.repo:<24}  {r.status:<10}  {r.created_at}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    job = orch.get_job(args.job_id)
    if job is None:
        print(f"错误: 作业不存在: {args.job_id}", file=sys.stderr)
        return 1
    print(f"job_id   : {job.job_id}")
    print(f"repo     : {job.repo}")
    print(f"status   : {job.status}")
    print(f"created  : {job.created_at}")
    print(f"updated  : {job.updated_at}")
    print(f"session  : {job.session_id or '(none)'}")
    if job.notes:
        print(f"notes    : {job.notes[:200]}")
    print()
    print(f"{'stage':<14}  {'status':<10}  {'attempt':<8}  duration")
    print("-" * 60)
    stages = orch.get_stages(args.job_id)
    for s in JOB_STAGES:
        sr = stages[s]
        duration = ""
        if sr.started_at and sr.ended_at:
            duration = f"{sr.started_at} → {sr.ended_at}"
        print(f"{s:<14}  {sr.status:<10}  {sr.attempt:<8}  {duration}")
        if sr.status == "failed" and sr.log_tail:
            print(f"  log_tail: {sr.log_tail[:300]}")
    return 0


def cmd_rerun(args: argparse.Namespace) -> int:
    try:
        t = orch.rerun_from(args.job_id, args.stage)
    except (KeyError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    t.join()
    job = orch.get_job(args.job_id)
    print(f"作业 {args.job_id} 终态：{job.status}")
    return 0 if job.status == "success" else 1


def cmd_delete(args: argparse.Namespace) -> int:
    ok = orch.delete_job(args.job_id)
    if ok:
        print(f"已删除作业 {args.job_id}")
        return 0
    print(f"错误: 作业不存在: {args.job_id}", file=sys.stderr)
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    from cu.serve import main as serve_main
    return serve_main(host=args.host, port=args.port)


def main() -> int:
    parser = argparse.ArgumentParser(prog="cu", description="代码质检工作流 CLI")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="创建并执行作业")
    p_run.add_argument("zip_url", help="GitHub archive ZIP URL")
    p_run.add_argument("--job-id", help="自定义 job ID")
    p_run.add_argument("--start", choices=list(JOB_STAGES), help="从指定阶段开始")

    p_list = sub.add_parser("list", help="列出所有作业")
    p_list.add_argument("--status", help="按状态过滤")

    p_show = sub.add_parser("show", help="作业详情")
    p_show.add_argument("job_id")

    p_rerun = sub.add_parser("rerun", help="从某阶段重跑")
    p_rerun.add_argument("job_id")
    p_rerun.add_argument("stage", choices=list(JOB_STAGES))

    p_del = sub.add_parser("delete", help="删除作业（含沙箱与快照）")
    p_del.add_argument("job_id")

    p_serve = sub.add_parser("serve", help="启动 REST API 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()
    handlers = {
        "run": cmd_run, "list": cmd_list, "show": cmd_show,
        "rerun": cmd_rerun, "delete": cmd_delete, "serve": cmd_serve,
    }
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 验证 help**

Run: `python -m cu --help`
Expected: 6 个子命令出现。

Run: `python -m cu list`
Expected: 输出「（无作业）」或现有作业列表（在干净 `CU_DATA_ROOT` 下应为空；但当前用户 `~/.code-understand` 可能已存在数据）。

- [ ] **Step 3: Commit**

```bash
git add cu/cli.py
git commit -m "feat(cli): add list/show/rerun/delete/serve subcommands"
```

---

### Task 11: cu/api.py + cu/serve.py — FastAPI

**Files:**
- Create: `cu/api.py`
- Create: `cu/serve.py`

- [ ] **Step 1: 创建 cu/api.py**

```python
"""FastAPI 应用：REST API 暴露 Orchestrator 能力。"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from cu import orchestrator as orch
from cu.models import JOB_STAGES


class CreateJobRequest(BaseModel):
    zip_url: str
    job_id: Optional[str] = None
    notes: Optional[str] = ""


class StageDTO(BaseModel):
    stage: str
    status: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    log_tail: str = ""
    attempt: int = 0


class JobDTO(BaseModel):
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str
    claude_project_dir: str
    status: str
    created_at: str
    updated_at: str
    notes: str = ""
    stages: list[StageDTO] = []


def _job_to_dto(job_id: str) -> JobDTO | None:
    rec = orch.get_job(job_id)
    if rec is None:
        return None
    stages = orch.get_stages(job_id)
    stage_dtos = [
        StageDTO(**stages[s].to_dict()) for s in JOB_STAGES
    ]
    return JobDTO(**rec.to_dict(), stages=stage_dtos)


def create_app() -> FastAPI:
    app = FastAPI(title="CodeUnderstand Orchestrator", version="0.2.0")
    orch.ensure_db()

    @app.post("/api/v1/jobs", response_model=JobDTO, status_code=201)
    def create_job(req: CreateJobRequest):
        try:
            rec = orch.create_job(req.zip_url, job_id=req.job_id, notes=req.notes or "")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _job_to_dto(rec.job_id)

    @app.get("/api/v1/jobs", response_model=list[JobDTO])
    def list_jobs(status: Optional[str] = None):
        return [_job_to_dto(r.job_id) for r in orch.list_jobs(status=status)]

    @app.get("/api/v1/jobs/{job_id}", response_model=JobDTO)
    def get_job(job_id: str):
        dto = _job_to_dto(job_id)
        if dto is None:
            raise HTTPException(status_code=404, detail="job not found")
        return dto

    @app.post("/api/v1/jobs/{job_id}/stages/{stage}/run")
    def run_stage(job_id: str, stage: str):
        if stage not in JOB_STAGES:
            raise HTTPException(status_code=400, detail="invalid stage")
        try:
            orch.run_stage(job_id, stage)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"started": True}

    @app.post("/api/v1/jobs/{job_id}/stages/{stage}/rerun")
    def rerun_stage(job_id: str, stage: str):
        if stage not in JOB_STAGES:
            raise HTTPException(status_code=400, detail="invalid stage")
        try:
            orch.rerun_from(job_id, stage)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"started": True}

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        ok = orch.cancel(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="no active run")
        return {"cancelled": True}

    @app.delete("/api/v1/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        ok = orch.delete_job(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="job not found")
        return None

    return app


app = create_app()
```

- [ ] **Step 2: 创建 cu/serve.py**

```python
"""uvicorn 启动入口。"""
from __future__ import annotations

import uvicorn


def main(host: str = "127.0.0.1", port: int = 8765) -> int:
    uvicorn.run("cu.api:app", host=host, port=port, log_level="info")
    return 0
```

- [ ] **Step 3: 语法检查**

Run: `python -m py_compile cu/api.py cu/serve.py`
Expected: OK

- [ ] **Step 4: 启动冒烟（手动）**

Run（后台启动，测试时停掉）:
```
python -m cu serve --port 8765 &
sleep 2
curl -s http://127.0.0.1:8765/api/v1/jobs
kill %1
```
Expected: `curl` 返回 `[]` 或现有作业 JSON。

> Windows 上 `&` 与 `kill %1` 行为可能受 Git Bash 影响，建议改用 PowerShell/`uvicorn` 直接前台启动 + Ctrl+C 验证。

- [ ] **Step 5: Commit**

```bash
git add cu/api.py cu/serve.py
git commit -m "feat(api): add FastAPI REST endpoints and uvicorn entry"
```

---

### Task 12: tests/test_api.py — FastAPI 路由测试

**Files:**
- Create: `tests/test_api.py`

- [ ] **Step 1: 写测试（用 fastapi.testclient）**

```python
"""FastAPI 路由测试 — TestClient。"""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cu import orchestrator as orch
from cu.api import create_app
from cu.models import JOB_STAGES


URL = "https://github.com/u/demo-repo/archive/refs/heads/main.zip"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "cu"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".claude").mkdir()
    with orch._ACTIVE_LOCK:
        orch._ACTIVE.clear()
    return TestClient(create_app())


def test_create_and_get_job(client):
    r = client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j1"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job_id"] == "api-j1"
    assert body["repo"] == "demo-repo"
    assert body["status"] == "pending"
    assert len(body["stages"]) == 4

    r2 = client.get("/api/v1/jobs/api-j1")
    assert r2.status_code == 200
    assert r2.json()["job_id"] == "api-j1"


def test_create_invalid_url(client):
    r = client.post("/api/v1/jobs", json={"zip_url": "not-a-url"})
    assert r.status_code == 400


def test_list_jobs(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j2"})
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200
    items = r.json()
    assert any(j["job_id"] == "api-j2" for j in items)


def test_run_stage_dependency_409(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j3"})
    r = client.post("/api/v1/jobs/api-j3/stages/conversation/run")
    assert r.status_code == 409


def test_delete_job(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j4"})
    r = client.delete("/api/v1/jobs/api-j4")
    assert r.status_code == 204
    r2 = client.get("/api/v1/jobs/api-j4")
    assert r2.status_code == 404


def _fake_runner(ctx):
    from cu.paths import sandbox_home
    os.makedirs(sandbox_home(ctx.job_id), exist_ok=True)


def test_run_stage_and_show(client):
    client.post("/api/v1/jobs", json={"zip_url": URL, "job_id": "api-j5"})
    with patch.dict(
        orch.STAGE_RUNNERS,
        {s: _fake_runner for s in JOB_STAGES},
        clear=False,
    ):
        r = client.post("/api/v1/jobs/api-j5/stages/bootstrap/run")
        assert r.status_code == 200
        with orch._ACTIVE_LOCK:
            t = orch._ACTIVE["api-j5"].thread
        t.join(timeout=5)
    detail = client.get("/api/v1/jobs/api-j5").json()
    assert detail["status"] == "success"
    statuses = {s["stage"]: s["status"] for s in detail["stages"]}
    assert statuses["bootstrap"] == "success"
    assert statuses["build"] == "success"
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_api.py -v`
Expected: 6 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_api.py
git commit -m "test: add FastAPI route tests with TestClient"
```

---

### Task 13: cu/stages.py — 让 stages 内部不强写 stdout（API 模式）

> 背景：P1 的 `cu/cli.py` 在 `cmd_run` 内部用 `print` 输出阶段头，但 P2 orchestrator 在线程里跑时这些 print 仍会输出到服务进程 stdout。需要把进度统一通过 `on_event` 钩子传出，CLI/API 各自决定如何展示。

**Files:**
- Modify: `cu/stages.py`

- [ ] **Step 1: 检查 cu/stages.py 是否有不通过 on_event 的 print**

读取 `cu/stages.py`。若 P1 实现没有 print（应当没有），跳过本任务并 commit 一条空 commit message 注释。  
若有 print，全部改为：`if ctx.on_event: ctx.on_event(stage, "...")`。

- [ ] **Step 2: 跑现有测试**

Run: `python -m pytest tests/ -v -m "not slow"`
Expected: 全部通过。

- [ ] **Step 3: Commit（仅当真有改动）**

```bash
git add cu/stages.py
git commit -m "refactor(stages): route progress messages through on_event hook"
```

---

### Task 14: docs — P2 状态机与 API 速查表

**Files:**
- Create: `docs/superpowers/specs/p2-api-reference.md`

- [ ] **Step 1: 创建文档**

```markdown
# P2 · 编排器 API 速查表

## CLI 子命令
- `cu run <zip_url> [--job-id ID] [--start STAGE]`
- `cu list [--status STATUS]`
- `cu show <job_id>`
- `cu rerun <job_id> <stage>`
- `cu delete <job_id>`
- `cu serve [--host HOST] [--port PORT]`

## REST API
- POST   /api/v1/jobs                                       创建作业
- GET    /api/v1/jobs?status=running                        列表
- GET    /api/v1/jobs/{job_id}                              详情（含 4 阶段）
- POST   /api/v1/jobs/{job_id}/stages/{stage}/run           触发阶段
- POST   /api/v1/jobs/{job_id}/stages/{stage}/rerun         重跑阶段
- POST   /api/v1/jobs/{job_id}/cancel                       取消运行
- DELETE /api/v1/jobs/{job_id}                              删除作业

## 状态字段语义
- 作业 status：pending | running | success | failed | cancelled
- 阶段 status：pending | running | success | failed | cancelled
- 阶段顺序：bootstrap → conversation → compile → build
- 快照：post-bootstrap / post-conversation / post-compile（build 后不拍）

## 重跑约束
- bootstrap：始终可执行（重跑时显式空壳重置）
- 其它阶段：要求所有前序阶段为 success；重跑前先恢复 post-prev 快照
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/p2-api-reference.md
git commit -m "docs: add P2 API quick reference"
```

---

### Task 15: 全套测试回归 + Ubuntu 验证清单

**Files:**
- Create: `docs/superpowers/plans/p2-ubuntu-verification-checklist.md`

- [ ] **Step 1: 本机跑全套**

Run: `python -m pytest tests/ -v -m "not slow"`
Expected: 应有 ~31+ tests passed（P1: 16 + P2 新增 ~15+）。

- [ ] **Step 2: 创建 Ubuntu 验证清单**

```markdown
# P2 · Ubuntu 真机验证清单

> 在 Ubuntu 上从 git 拉取最新 mindflow 后执行。

## 0. 准备

```bash
cd ~ && git clone <repo> CodeUnderstand && cd CodeUnderstand
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[test]"
which claude jq wget unzip zip curl
```

## 1. 单元测试

```bash
python -m pytest tests/ -v -m "not slow"
```
预期：~31 passed。

## 2. P1 e2e 冒烟（bootstrap 阶段）

```bash
python -m pytest tests/test_e2e_smoke.py -v -m slow
```
预期：1 passed；磁盘上出现 `$HOME/.code-understand/jobs/smoke-test/home/code-understand-nocode/code/nocode/README.md`。

## 3. P2 CLI 端到端

```bash
cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id u-001
cu show u-001
ls ~/.code-understand/jobs/u-001/
```
预期：4 阶段 success；快照 post-bootstrap / post-conversation / post-compile 在 snapshots/ 下；`code-understand-nocode.zip` 在 home/ 下。

## 4. P2 重跑

```bash
cu rerun u-001 conversation
cu show u-001
```
预期：home/ 恢复到 post-bootstrap 状态、conversation/compile/build 重新跑；attempt 计数 +1。

## 5. P2 API

```bash
cu serve --port 8765 &
SERVE_PID=$!
sleep 2
curl -s http://127.0.0.1:8765/api/v1/jobs | jq '.[].job_id'
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"zip_url":"https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip","job_id":"api-001"}' | jq
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs/api-001/stages/bootstrap/run
sleep 60
curl -s http://127.0.0.1:8765/api/v1/jobs/api-001 | jq '.stages'
kill $SERVE_PID
```

## 6. P2 删除

```bash
cu delete api-001
ls ~/.code-understand/jobs/  # 应不再含 api-001
```

## 7. 并行

```bash
cu run https://github.com/a/repo1/archive/refs/heads/main.zip --job-id par-1 &
cu run https://github.com/b/repo2/archive/refs/heads/main.zip --job-id par-2 &
wait
cu list
```
预期：两个作业互不影响、各自 success。
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/p2-ubuntu-verification-checklist.md
git commit -m "docs: add P2 Ubuntu verification checklist"
```

---

## 自检（在所有 Task 完成后再做）

1. **Spec 覆盖**：
   - §6.1 快照策略：Task 5 实现 + Task 8 集成 ✓
   - §6.2 重跑语义：Task 8 `rerun_from` ✓
   - §6.3 清理策略：明确无定期清理；删除靠 Task 8 `delete_job` ✓
   - §7.1 UI 占位（时间展示）：API 已暴露 created_at/started_at/ended_at ✓
   - §7.2 API 形状：Task 11 完整覆盖 ✓
   - §8 删除作业：Task 8 + Task 11 + Task 10（CLI）✓

2. **占位符扫描**：无 TBD/TODO/`pass` 占位。

3. **类型一致**：
   - `JobRecord` / `StageRunRecord` 在 models.py 定义，db.py / orchestrator.py / api.py 引用一致。
   - `JobContext` 在 stages.py 增加 `on_event` / `pid_sink` 字段，orchestrator.py 注入。
   - `STAGE_RUNNERS` / `JOB_STAGES` 拼写未变。

4. **路径一致**：
   - `data_root()` / `job_dir()` / `sandbox_home()` / `snapshots_dir()` 全部沿用 P1 已定义函数。
   - 快照路径：`<job_dir>/snapshots/post-<stage>.tar`（与文档一致）。

5. **依赖一致**：
   - 新增依赖 fastapi / uvicorn 在 Task 1 `pyproject.toml`；httpx 在 `[test]` extra。
   - 不引入未声明的依赖。

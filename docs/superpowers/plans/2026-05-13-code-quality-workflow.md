# 代码质检工作流编排系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Ubuntu 上交付单机 FastAPI 服务：多作业并行、每作业沙箱假 `$HOME`、四宏阶段（`bootstrap` / `conversation` / `compile` / `build`）、全量 tar 快照与重跑、Web UI（列表/详情/时间/SSE 日志）、手动删除作业；bash 负责原子脚本，Python 编排；dispatch 仅占位字段。

**Architecture:** 作业数据根目录 `CU_JOBS_DATA_DIR`（默认仓库下 `var/cu-jobs/`，加入 `.gitignore`）下每 `job_id` 含 `orchestration.sqlite`、`home/`（假 `$HOME`）、`snapshots/`（tar 在树外）。编排器子进程继承服务环境并覆盖 `HOME`、`CODE_UNDERSTAND_STATE_ROOT=$HOME`（仅指向沙箱内，满足现有 `loop.sh`/`pack.sh` 对变量名的读取，而非真机 `~/Documents` 方案）、以及作业级路径。宏阶段通过 `scripts/workflow/` 下薄封装顺序调用既有 `download.sh`/`loop.sh`/拆出的 `pack` 片段/`rewrite.py`/`zip.sh`，必要时对仓库根脚本做**最小**行级修改以支持「代码已在 `code-understand-{repo}/code/{repo}`」与「会话导出在 rewrite 之后」。

**Tech Stack:** Python 3.11+、`uvicorn`、`fastapi`、`jinja2`、`httpx`（测 API）、`pytest`、`sqlite3`（stdlib）；前端首版 **Jinja2 + 少量原生 JS（`EventSource` SSE）**，不引入单独 Node 构建链。

**Spec:** `docs/superpowers/specs/2026-05-13-code-quality-workflow-design.md`

---

## 文件结构（新建 / 修改一览）

| 路径 | 职责 |
|------|------|
| `pyproject.toml` | 包元数据、依赖、`pytest` 入口、`workflow` 包 |
| `workflow/__init__.py` | 包标记 |
| `workflow/config.py` | `CU_JOBS_DATA_DIR`、快照目录名、`CU_BIND_HOST`/`CU_BIND_PORT` |
| `workflow/domain.py` | 宏阶段枚举、`STAGE_ORDER`、合法迁移 |
| `workflow/storage.py` | SQLite：jobs、stage_runs、时间戳字段 |
| `workflow/paths.py` | `job_root(job_id)`、`fake_home(job_id)`、`snapshots_dir(job_id)`、`code_understand_root(repo)` |
| `workflow/snapshot.py` | `create_snapshot(fake_home, tarball_path)`、`restore_snapshot(...)`，内部 `tar` 排除 `snapshots` 若误放在树内时的防护 |
| `workflow/process_env.py` | `build_subprocess_env(job, repo, session_id=None) -> dict[str,str]` |
| `workflow/runner.py` | `run_macro(job_id, stage, log_path)`：锁、子进程、`Popen` 流式写日志、返回码 |
| `workflow/app.py` | FastAPI 挂载路由、静态模板、lifespan |
| `workflow/routes/jobs.py` | REST + SSE |
| `workflow/templates/*.html` | 列表、详情、新建表单 |
| `scripts/workflow/bootstrap_home.sh` | 建 `home/` 子目录、从真实 `$REAL_HOME/.claude` rsync 排除 `projects`、建空 `home/.claude/projects` |
| `scripts/workflow/download_into_job.sh` | 下载 zip 到 `$HOME/tmp`，解压到 `$HOME/code-understand-{repo}/code/{repo}`（可调 `download.sh` 或内联 wget/unzip 逻辑，**避免**再写 `$HOME/projects`） |
| `scripts/workflow/run_conversation.sh` | `export` 后调用 `loop.sh` + 拆出的 **仅 doc** 脚本（见 Task 9） |
| `scripts/workflow/run_compile.sh` | metadata + clean |
| `scripts/workflow/run_build.sh` | rewrite + export + zip |
| `download.sh` / `loop.sh` / `build.sh` / `pack.sh` / `rewrite.py` / `clean.py` | **最小修改**或拆文件：仅当封装脚本无法干净注入时改（任务内给 diff） |
| `tests/workflow/test_paths.py` | 纯函数路径 |
| `tests/workflow/test_snapshot.py` | tar 往返 |
| `tests/workflow/test_storage.py` | SQLite CRUD |
| `tests/workflow/test_api_jobs.py` | `TestClient` + 临时目录 fixture |

---

### Task 1: Python 工程脚手架与 pytest

**Files:**
- Create: `pyproject.toml`
- Create: `workflow/__init__.py`
- Create: `workflow/config.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`（Modify：追加 `var/`、`__pycache__`、`.pytest_cache`）

- [ ] **Step 1: 写失败测试（包可导入）**

Create: `tests/workflow/test_import.py`

```python
def test_workflow_package_imports():
    import workflow
    import workflow.config

    assert workflow.config.default_jobs_data_dir().endswith("var/cu-jobs")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/workflow/test_import.py -v`  
Expected: `ModuleNotFoundError: No module named 'workflow'` 或 import 路径错误。

- [ ] **Step 3: 最小实现**

Create `pyproject.toml`:

```toml
[project]
name = "code-understand-workflow"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.32.0",
  "jinja2>=3.1.4",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.27.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Create `workflow/__init__.py`（空文件即可）。

Create `workflow/config.py`:

```python
from __future__ import annotations

import os
from pathlib import Path


def default_jobs_data_dir() -> str:
    raw = os.environ.get("CU_JOBS_DATA_DIR", "").strip()
    if raw:
        return str(Path(raw).expanduser().resolve())
    return str((Path(__file__).resolve().parents[1] / "var" / "cu-jobs").resolve())
```

Create `tests/conftest.py`（空文件或 `pytest_plugins = []`）。

- [ ] **Step 4: 安装并跑通**

Run:

```bash
cd /path/to/CodeUnderstand && pip install -e ".[dev]" && pytest tests/workflow/test_import.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml workflow tests .gitignore
git commit -m "chore: add workflow Python package scaffold"
```

---

### Task 2: 路径助手与假 HOME 布局

**Files:**
- Create: `workflow/paths.py`
- Create: `tests/workflow/test_paths.py`

- [ ] **Step 1: 失败测试**

Create `tests/workflow/test_paths.py`:

```python
from pathlib import Path

from workflow.paths import fake_home, job_root, orchestration_db, snapshots_dir


def test_job_layout_under_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_JOBS_DATA_DIR", str(tmp_path))
    jid = "550e8400-e29b-41d4-a716-446655440000"
    root = job_root(jid)
    assert root == tmp_path / jid
    assert fake_home(jid) == root / "home"
    assert snapshots_dir(jid) == root / "snapshots"
    assert orchestration_db(jid) == root / "orchestration.sqlite"
```

- [ ] **Step 2: pytest 失败** — 缺 `workflow.paths`。

- [ ] **Step 3: 实现 `workflow/paths.py`**

```python
from __future__ import annotations

import os
from pathlib import Path

from workflow.config import default_jobs_data_dir


def jobs_data_dir() -> Path:
    return Path(default_jobs_data_dir())


def job_root(job_id: str) -> Path:
    return jobs_data_dir() / job_id


def fake_home(job_id: str) -> Path:
    return job_root(job_id) / "home"


def snapshots_dir(job_id: str) -> Path:
    return job_root(job_id) / "snapshots"


def orchestration_db(job_id: str) -> Path:
    return job_root(job_id) / "orchestration.sqlite"


def code_understand_root(job_id: str, repo: str) -> Path:
    return fake_home(job_id) / f"code-understand-{repo}"
```

- [ ] **Step 4: pytest PASS**

- [ ] **Step 5: Commit** — `feat(workflow): add job path helpers`

---

### Task 3: SQLite 存储与「作业–repo 1:1」

**Files:**
- Create: `workflow/storage.py`
- Create: `tests/workflow/test_storage.py`

- [ ] **Step 1: 失败测试**

```python
import pytest

from workflow.storage import RepoConflictError, connect, create_job, get_job


def test_create_job_unique_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_JOBS_DATA_DIR", str(tmp_path))
    db = tmp_path / "global.sqlite"
    connect(db)
    jid = create_job(db, zip_url="https://github.com/o/r/archive/refs/heads/main.zip", repo="r")
    row = get_job(db, jid)
    assert row["repo"] == "r"
    with pytest.raises(RepoConflictError):
        create_job(db, zip_url="https://github.com/o/r2/archive/refs/heads/main.zip", repo="r")
```

（`create_job` 在已有未删除作业占用同一 `repo` 时抛 `RepoConflictError`。）

- [ ] **Step 2: 实现 `workflow/storage.py`**

- 表 `jobs(id TEXT PK, zip_url TEXT, repo TEXT UNIQUE, dispatch_stub TEXT, created_at TEXT, updated_at TEXT)`  
- 表 `stage_runs(job_id TEXT, stage TEXT, status TEXT, started_at TEXT, ended_at TEXT, error TEXT, PRIMARY KEY(job_id, stage))`  
- `create_job`：`INSERT` 前 `SELECT 1 FROM jobs WHERE repo=? AND deleted=0`（加 `deleted INTEGER DEFAULT 0` 列，删除作业软删或硬删后释放 repo 由产品决定——spec 为 1:1 活跃作业：建议 **`deleted_at` 可空**，唯一约束为 `UNIQUE(repo) WHERE deleted_at IS NULL` SQLite 3.31+ 部分索引；若版本顾虑则用应用层检查 + 普通 `repo` 列 + 查询未删除列表）。

- [ ] **Step 3: pytest PASS**

- [ ] **Step 4: Commit**

---

### Task 4: 快照 tar 创建与恢复

**Files:**
- Create: `workflow/snapshot.py`
- Create: `tests/workflow/test_snapshot.py`

- [ ] **Step 1: 失败测试**

```python
from pathlib import Path

from workflow.snapshot import create_home_snapshot, restore_home_snapshot


def test_roundtrip_restores_file(tmp_path):
    home = tmp_path / "home"
    (home / "a").mkdir(parents=True)
    (home / "a" / "f.txt").write_text("hi")
    snap = tmp_path / "snapshots" / "post-bootstrap.tar"
    snap.parent.mkdir(parents=True)
    create_home_snapshot(home, snap)
    (home / "a" / "f.txt").write_text("gone")
    restore_home_snapshot(snap, home)
    assert (home / "a" / "f.txt").read_text() == "hi"
```

- [ ] **Step 2: 实现** — `create_home_snapshot` 使用 `tar -cf` 在 `cwd=home.parent` 下打包目录 `home` 的 basename；`restore` 使用 `tar -xf` 到 `parent`；文档注明 **快照文件不得位于 `home` 内**（测试里放在外）。

- [ ] **Step 3: pytest PASS**

- [ ] **Step 4: Commit**

---

### Task 5: `scripts/workflow/bootstrap_home.sh`

**Files:**
- Create: `scripts/workflow/bootstrap_home.sh`
- Create: `tests/workflow/test_bootstrap_script.py`（用 `subprocess` 调真实 bash，需 CI 有 bash；Windows 开发可 skip）

- [ ] **Step 1: 脚本行为**  
  - 参数：`REAL_HOME`（真机，用于复制源）、`FAKE_HOME`（绝对路径）  
  - `mkdir -p "$FAKE_HOME/logs" "$FAKE_HOME/tmp" "$FAKE_HOME/loop_logs"`  
  - `rsync -a --exclude='projects/' "$REAL_HOME/.claude/" "$FAKE_HOME/.claude/"` 若源不存在则 `mkdir -p "$FAKE_HOME/.claude/projects"`  
  - 确保 `"$FAKE_HOME/.claude/projects"` 存在且为空或仅占位

- [ ] **Step 2: 测试**（Linux）：`tmp_path` 下复制最小假 `.claude` 结构后跑脚本，断言目录存在。

- [ ] **Step 3: Commit**

---

### Task 6: `scripts/workflow/download_into_job.sh`

**Files:**
- Create: `scripts/workflow/download_into_job.sh`

- [ ] **Step 1: 接口**  
  - 参数：`ZIP_URL`、`REPO`、`BRANCH`（或从 URL 解析）、`FAKE_HOME`  
  - 目标：`$FAKE_HOME/code-understand-$REPO/code/$REPO`  
  - 下载到 `$FAKE_HOME/tmp/$REPO.zip`，`unzip`，将 `${REPO}-${BRANCH}` **移动**为最终路径（与现有 `download.sh` 逻辑对齐但 **不写** `$HOME/projects`）  
  - `git init` 等与 `download.sh` 尾部一致（若 spec 不需要 git 可删——当前 `run.sh` 会删 `.git`，保留 init 与现网一致更安全）

- [ ] **Step 2: 集成测试**（可选网络：用 `pytest -m "not network"` 默认跳过；或本地 fixture zip）

- [ ] **Step 3: Commit**

---

### Task 7: 编排 `bootstrap` 宏（Python 调脚本）

**Files:**
- Create: `workflow/process_env.py`
- Create: `workflow/runner.py`（初版仅 `bootstrap`）
- Modify: `workflow/storage.py`（更新阶段状态与时间戳）

- [ ] **Step 1: `process_env.py`**  
  返回 dict：`HOME`、`CODE_UNDERSTAND_STATE_ROOT`（=`HOME`）、`USER`/`LOGNAME` 继承、`PATH` 继承、`SESSION_ID` 若已有则带上。

- [ ] **Step 2: `runner.run_bootstrap`**  
  - `mkdir -p job_root/snapshots`  
  - `subprocess.run([bash, bootstrap_home.sh, os.environ["HOME"], fake_home], env=..., check=True)`  
  - `subprocess.run([bash, download_into_job.sh, ...], cwd=REPO_ROOT, check=True)`  
  - 成功：`create_snapshot` → `post-bootstrap.tar`  
  - 写 `stage_runs`：`bootstrap` `success`，`started_at`/`ended_at` ISO8601

- [ ] **Step 3: pytest** 使用 `monkeypatch` 将 `wget`/`unzip` 换为 echo 的 fake script（若不想网络）

- [ ] **Step 4: Commit**

---

### Task 8: `conversation` 宏 — 拆分 `build.sh` 的 doc 部分

**Spec:** `loop.sh` 后需在 **最终代码目录** 生成 `doc/`，且 **不** 提前复制 `session.jsonl` 到 `sessions/`。

**Files:**
- Create: `scripts/workflow/run_document.sh`（从 `build.sh` **复制**「`cp session` + `jq` 归一化」之后的 **续跑 doc** 段落为独立脚本，入参：`REPO`、`SESSION_ID`、`FAKE_HOME`；其中 `PROJECT_DIR="$FAKE_HOME/code-understand-$REPO/code/$REPO"`，`SESSION_FILE` 仍指向 `$FAKE_HOME/.claude/projects/.../$SESSION.jsonl`）  
- Create: `scripts/workflow/run_conversation.sh`：先 `loop.sh "$PROJECT_DIR" "$REPO"` 读 stdout `SESSION_ID` 写入 `job_root/session_id.txt`，再 `run_document.sh`  
- Modify: `workflow/runner.py` 增加 `run_conversation`

- [ ] **Step 1: 从 `build.sh` 逐行剪切**到 `run_document.sh` 并保持 `jq`、`claude` 依赖说明在注释头

- [ ] **Step 2: 手工或 pytest** 在 fixture 沙箱跑（可 mock `claude`）

- [ ] **Step 3: 成功后 `post-conversation.tar`**

- [ ] **Step 4: Commit**

---

### Task 9: `compile` 宏 — metadata + clean

**Files:**
- Create: `scripts/workflow/run_metadata.sh`：从 `pack.sh` **步骤 1–2**（复制代码到 `OUT/code`）改为 **已是最终布局则跳过复制**，只执行 `metadata.json`、`questions.json`、`curl`+`evaluate`+`classify`；`OUT="$FAKE_HOME/code-understand-$REPO"`，`code_dir="$OUT/code/$REPO"`  
- Create: `scripts/workflow/run_clean.sh`：`find` 删 `.DS_Store`、`__MACOSX`（与 `zip.sh` 规则对齐）  
- Create: `scripts/workflow/run_compile.sh`：串联上述两脚本  
- Modify: `runner.py`

- [ ] **Step 1: 实现并本地跑 dry-run**

- [ ] **Step 2: 成功后 `post-compile.tar`**

- [ ] **Step 3: Commit**

---

### Task 10: `build` 宏 — `rewrite`、导出 session、`zip`

**Files:**
- Modify: `rewrite.py` — 增加模式：**仅写 `.claude` 真源** 或 **仅导出到 `sessions/session1/session.jsonl`**（由 CLI 子命令或 `--export-only` 区分）；删除「双文件同步」路径在沙箱模式下只保留一份（spec）；保持旧行为可用环境变量 `REWRITE_DUAL_WRITE=1` 便于过渡测试  
- Create: `scripts/workflow/export_session.sh`：`cp` + 可选 `jq` 与 `build.sh` 对齐  
- Create: `scripts/workflow/run_build.sh`：`rewrite` → `export_session` → `zip.sh "$OUT"`  
- Modify: `runner.py`：**不**打快照

- [ ] **Step 1: 为 `rewrite.py` 写 pytest**（临时 jsonl fixture）

- [ ] **Step 2: Commit**

---

### Task 11: 重跑与恢复快照

**Files:**
- Modify: `workflow/runner.py`
- Modify: `workflow/routes/jobs.py`（下一任务可先 stub）

- [ ] **Step 1: `rerun_from(job_id, stage)`**  
  - 映射：`conversation` → 恢复 `post-bootstrap.tar`；`compile` → `post-conversation`；`build` → `post-compile`；`bootstrap` → 删除 `home` 下 `code-understand-*`、`.claude/projects` 内容等（实现清单列路径）后 **不** restore  
  - 将 `stage` 及之后 `stage_runs` 重置 `pending`

- [ ] **Step 2: pytest** 用 Task 4 的快照在迷你 `home` 上验证

- [ ] **Step 3: Commit**

---

### Task 12: FastAPI REST（jobs CRUD、run、rerun、cancel）

**Files:**
- Create: `workflow/app.py`
- Create: `workflow/routes/jobs.py`
- Create: `tests/workflow/test_api_jobs.py`

- [ ] **Step 1: 失败测试** — `POST /api/jobs` 创建、`GET /api/jobs/{id}`、`POST .../stages/bootstrap/run` 返回 200 且阶段状态变化（runner mock：`monkeypatch.setattr`）

- [ ] **Step 2: 实现路由**  
  - `POST /api/jobs` body：`{"zip_url": "...", "dispatch_stub": null}`，解析 `repo` 自 URL 与 `download.sh` 同正则  
  - `POST /api/jobs/{id}/stages/{stage}/run`：`stage in bootstrap|conversation|compile|build`  
  - `POST .../rerun`：调用 Task 11  
  - `POST .../cancel`：`runner.cancel(job_id)` 杀进程组

- [ ] **Step 3: `uvicorn workflow.app:app` 手工 smoke**

- [ ] **Step 4: Commit**

---

### Task 13: SSE `/api/jobs/{id}/events`

**Files:**
- Modify: `workflow/routes/jobs.py`
- Create: `workflow/logbus.py`（内存 `asyncio.Queue` 每 job 一条，或 tail 文件）

- [ ] **Step 1: 实现** — `text/event-stream`，事件 `{"type":"log","chunk":"..."}` / `{"type":"state",...}`

- [ ] **Step 2: httpx 异步测试** 读首条事件（可选）

- [ ] **Step 3: Commit**

---

### Task 14: Jinja Web UI（时间展示）

**Files:**
- Create: `workflow/templates/base.html`
- Create: `workflow/templates/jobs_list.html`
- Create: `workflow/templates/job_detail.html`
- Modify: `workflow/app.py` — `Jinja2Templates(directory="workflow/templates")`，`GET /` 列表、`GET /jobs/{id}` 详情

- [ ] **Step 1: 列表展示** `created_at`、各 `stage` 的 `started_at`/`ended_at` 及耗时秒数（模板内算或 view-model）

- [ ] **Step 2: 详情页** 四阶段按钮：运行 / 从此重跑；`EventSource` 订阅 `/api/jobs/{id}/events`

- [ ] **Step 3: Commit**

---

### Task 15: `DELETE /api/jobs/{id}` 与磁盘清理

**Files:**
- Modify: `workflow/routes/jobs.py`
- Create: `workflow/cleanup.py` — `shutil.rmtree(job_root)`、`cancel` 子进程

- [ ] **Step 1: pytest** 创建空 job 目录后 DELETE，断言路径不存在

- [ ] **Step 2: Commit**

---

### Task 16: 文档与启动说明

**Files:**
- Create: `docs/superpowers/plans/README-workflow-run.md` — 否，skill 说用户未要求不写多余 md；改为在根 `README.md` **增加一节**（若已有 README 则追加小节；若无则仅增加「Workflow 服务」段落，避免整文件重写）

- [ ] **Step 1: 在现有 `README.md` 末尾追加**「Ubuntu：`pip install -e ".[dev]"`；`export CU_JOBS_DATA_DIR=...`；`uvicorn workflow.app:app --host 127.0.0.1 --port 8765`」

- [ ] **Step 2: Commit**

---

## Self-review（对照 spec）

| Spec 章节 | 覆盖任务 |
|-----------|----------|
| 沙箱假 `$HOME`、.claude 复制排除 projects | Task 5,7 |
| 运行期在假 `$HOME`，不用真机 Documents 方案 | Task 7 `CODE_UNDERSTAND_STATE_ROOT=$HOME` |
| 四宏阶段 ID | Task 7–10,12 |
| 快照 post-bootstrap/conversation/compile；build 无快照 | Task 4,7–10,11 |
| 重跑恢复 / bootstrap 空壳 | Task 11 |
| 无定期自动清理；UI 时间 | Task 14；无自动清理（未实现定时器） |
| 作业–repo 1:1 | Task 3 |
| dispatch stub 字段 | Task 12 `dispatch_stub` |
| 删除作业 | Task 15 |
| 验收：并行不同 repo | Task 3 唯一性 + Task 7 沙箱；集成大测可另开 Task 17 为可选 |

**Placeholder 扫描：** 计划中任务均为可执行文件名与函数名；未留 TBD 代码块。

**类型/命名一致：** 宏阶段字符串全文统一 `bootstrap` / `conversation` / `compile` / `build`。

**缺口（可选后续）：** 完整端到端需真 `claude` 与网络下载；计划允许 `Task 17`（未展开）作为 staging 手工清单。

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-13-code-quality-workflow.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 每任务派生子代理、任务间人工复审，迭代快  

**2. Inline Execution** — 本会话按任务执行 `executing-plans`，批量检查点  

**Which approach?**

# P1 · 沙箱与脚本接入 — 实现计划

> **归档说明（2026-05）**：下文任务表基于 **bash 脚本 + `run_script`** 时代书写，**已与当前代码基线不符**。流水线实现以 **`cu.pipeline.*` / `cu.runtime.*`** 与 **`docs/superpowers/specs/2026-05-13-shell-to-python-design.md`** 为准。本文件仅作历史任务分解参考，实施时勿再照抄其中的 `*.sh` 路径。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Ubuntu 上通过 Python CLI 端到端跑通 1 个 repo 的 4 宏阶段（bootstrap → conversation → compile → build），输出干净 zip；全程使用沙箱 `$HOME` 隔离。

**正文保留策略**：自「文件结构」起的表格与 Task 章节仍为 **bash 时代原文**，便于对照当初拆解粒度；所列 **`*.sh` 路径多数已从仓库移除**，勿当作现行清单执行。

**Architecture（现行）**：Python 包 **`cu/`** 提供路径常量、沙箱创建、**`cu.runner.run_module`** 驱动的阶段编排与 **`cu.pipeline.*` / `cu.runtime.*`** 流水线；编排器同时有 **CLI** 与 **P3 Web/API**。

**Tech Stack（现行）**：Python 3.10+、pytest、**`cu run` / uvicorn**；子进程依赖 **claude**（或 **`CU_TEST_MODE` + `cu.testing.mock_claude`**）、zip/git 等与 **`shell-to-python-design`** 清册一致。**上文若为历史措辞**：仍以归档说明为准。

---

## 文件结构

### 新建

| 文件 | 职责 |
|------|------|
| `cu/__init__.py` | 包入口，暴露版本号 |
| `cu/paths.py` | 路径常量与目录解析（`CU_DATA_ROOT`、作业布局） |
| `cu/sandbox.py` | 沙箱 HOME 创建、`.claude` 配置复制、环境自检 |
| `cu/env.py` | 为每个宏阶段组装子进程环境变量 dict |
| `cu/runner.py` | 子进程执行（日志捕获、超时、退出码处理） |
| `cu/stages.py` | 4 宏阶段定义与执行逻辑 |
| `cu/cli.py` | CLI 入口（`python -m cu.cli`） |
| `cu/__main__.py` | `python -m cu` 入口转发 |
| `scripts/metadata.sh` | 从 `pack.sh` 提取的 metadata 生成（classify + evaluate + metadata.json + questions.json） |
| `scripts/clean_artifacts.sh` | 清理 `.DS_Store`、`__MACOSX`、隐藏文件等 |
| `scripts/export_session.sh` | 从沙箱 `.claude/projects/...` 复制+归一化 session.jsonl 到产物 `sessions/` |
| `tests/test_paths.py` | paths 模块单元测试 |
| `tests/test_sandbox.py` | sandbox 模块单元测试 |
| `tests/test_env.py` | env 模块单元测试 |
| `tests/test_stages.py` | 阶段执行集成测试（mock claude） |
| `tests/conftest.py` | pytest fixtures（tmp 目录、mock HOME 等） |
| `pyproject.toml` | 项目元数据与 pytest 配置 |

### 修改

| 文件 | 改动 |
|------|------|
| `download.sh` | 去掉 `git config --global`（改为 `git -c`），确保纯 env 驱动 |
| `build.sh` | 增加 `BUILD_DOC_ONLY=1` 模式：跳过 session 复制/归一化，仅生成 `doc/` |
| `rewrite.py` | 增加 `--single-source` 模式：仅编辑源会话，不写 copyPath；增加 `--non-interactive` 模式：直接读 tmp 文件写回，不启动 gedit |
| `loop.sh` | 无代码改动；通过 env `HOME` + `CODE_UNDERSTAND_STATE_ROOT` 注入即可适配 |

---

### Task 1: pyproject.toml + 包骨架

**Files:**
- Create: `pyproject.toml`
- Create: `cu/__init__.py`

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[project]
name = "code-understand"
version = "0.1.0"
requires-python = ">=3.10"

[project.scripts]
cu = "cu.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 2: 创建 cu/__init__.py**

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: 验证包可导入**

Run: `python -c "import cu; print(cu.__version__)"`
Expected: `0.1.0`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml cu/__init__.py
git commit -m "feat(cu): add Python package skeleton"
```

---

### Task 2: cu/paths.py — 路径常量

**Files:**
- Create: `cu/paths.py`
- Create: `tests/conftest.py`
- Create: `tests/test_paths.py`

- [ ] **Step 1: 写 test_paths.py 失败测试**

```python
import os
from cu.paths import data_root, job_dir, sandbox_home, snapshots_dir, artifact_root


def test_data_root_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CU_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = data_root()
    assert result == str(tmp_path / ".code-understand")


def test_data_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "custom"))
    result = data_root()
    assert result == str(tmp_path / "custom")


def test_job_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = job_dir("abc123")
    assert result == str(tmp_path / "jobs" / "abc123")


def test_sandbox_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = sandbox_home("abc123")
    assert result == str(tmp_path / "jobs" / "abc123" / "home")


def test_snapshots_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = snapshots_dir("abc123")
    assert result == str(tmp_path / "jobs" / "abc123" / "snapshots")


def test_artifact_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    result = artifact_root("abc123", "react")
    assert result == str(tmp_path / "jobs" / "abc123" / "home" / "code-understand-react")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cu.paths'`

- [ ] **Step 3: 实现 cu/paths.py**

```python
"""路径常量与作业目录解析。

布局（§3.4）：
    $CU_DATA_ROOT/                          （默认 $HOME/.code-understand）
    ├── db.sqlite
    ├── app.log
    └── jobs/<job_id>/
        ├── home/                           （假 $HOME — 沙箱根）
        │   ├── .claude/                    （bootstrap 时从真机复制）
        │   ├── code-understand-{repo}/     （干净产物根）
        │   │   ├── code/{repo}/
        │   │   ├── doc/
        │   │   ├── sessions/
        │   │   ├── metadata.json
        │   │   └── questions.json
        │   ├── logs/
        │   ├── loop_logs/
        │   ├── tmp/
        │   └── agent-{repo}/
        └── snapshots/
            ├── post-bootstrap.tar
            ├── post-conversation.tar
            └── post-compile.tar
"""
from __future__ import annotations

import os


def _real_home() -> str:
    return os.environ.get("HOME", os.path.expanduser("~"))


def data_root() -> str:
    override = os.environ.get("CU_DATA_ROOT", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(_real_home(), ".code-understand")


def job_dir(job_id: str) -> str:
    return os.path.join(data_root(), "jobs", job_id)


def sandbox_home(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "home")


def snapshots_dir(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "snapshots")


def artifact_root(job_id: str, repo: str) -> str:
    return os.path.join(sandbox_home(job_id), f"code-understand-{repo}")


def code_dir(job_id: str, repo: str) -> str:
    return os.path.join(artifact_root(job_id, repo), "code", repo)


def db_path() -> str:
    return os.path.join(data_root(), "db.sqlite")
```

- [ ] **Step 4: 创建 tests/conftest.py**

```python
"""Shared pytest fixtures."""
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_paths.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add cu/paths.py tests/conftest.py tests/test_paths.py
git commit -m "feat(cu): add paths module with job directory layout"
```

---

### Task 3: cu/sandbox.py — 沙箱创建

**Files:**
- Create: `cu/sandbox.py`
- Create: `tests/test_sandbox.py`

- [ ] **Step 1: 写 test_sandbox.py 失败测试**

```python
import os
from cu.sandbox import bootstrap_sandbox


def test_bootstrap_creates_dirs(tmp_path, monkeypatch):
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / ".claude").mkdir()
    (real_home / ".claude" / "settings.json").write_text('{"key":"val"}')
    (real_home / ".claude" / "projects").mkdir()
    (real_home / ".claude" / "projects" / "some-old-session").mkdir()

    monkeypatch.setenv("HOME", str(real_home))
    cu_root = tmp_path / "cu_data"
    monkeypatch.setenv("CU_DATA_ROOT", str(cu_root))

    job_id = "test-job-001"
    bootstrap_sandbox(job_id)

    home = cu_root / "jobs" / job_id / "home"
    assert home.is_dir()
    assert (home / ".claude").is_dir()
    assert (home / ".claude" / "settings.json").read_text() == '{"key":"val"}'
    assert not (home / ".claude" / "projects" / "some-old-session").exists()
    assert (home / ".claude" / "projects").is_dir()
    assert (cu_root / "jobs" / job_id / "snapshots").is_dir()


def test_bootstrap_idempotent(tmp_path, monkeypatch):
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / ".claude").mkdir()

    monkeypatch.setenv("HOME", str(real_home))
    cu_root = tmp_path / "cu_data"
    monkeypatch.setenv("CU_DATA_ROOT", str(cu_root))

    bootstrap_sandbox("job-x")
    marker = cu_root / "jobs" / "job-x" / "home" / "marker.txt"
    marker.write_text("keep")
    bootstrap_sandbox("job-x")
    assert not marker.exists(), "re-bootstrap should reset the sandbox"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_sandbox.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 cu/sandbox.py**

```python
"""沙箱 HOME 创建与 .claude 配置复制。

§2: 从真实 ~/.claude 复制配置/凭据，但不复制 projects/ 内容（避免共享会话）。
重入时先清空再重建（等价于重跑 bootstrap 宏阶段）。
"""
from __future__ import annotations

import os
import shutil

from cu.paths import sandbox_home, snapshots_dir, job_dir


_CLAUDE_COPY_EXCLUDES = {"projects"}


def _real_home() -> str:
    return os.environ.get("HOME", os.path.expanduser("~"))


def _copy_claude_config(real_claude: str, sandbox_claude: str) -> None:
    if not os.path.isdir(real_claude):
        os.makedirs(sandbox_claude, exist_ok=True)
        return
    for entry in os.listdir(real_claude):
        if entry in _CLAUDE_COPY_EXCLUDES:
            continue
        src = os.path.join(real_claude, entry)
        dst = os.path.join(sandbox_claude, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    projects = os.path.join(sandbox_claude, "projects")
    os.makedirs(projects, exist_ok=True)


def bootstrap_sandbox(job_id: str) -> str:
    """创建或重置沙箱 HOME，返回沙箱 home 路径。"""
    home = sandbox_home(job_id)
    snaps = snapshots_dir(job_id)

    if os.path.exists(home):
        shutil.rmtree(home)

    os.makedirs(home, exist_ok=True)
    os.makedirs(snaps, exist_ok=True)

    real_claude = os.path.join(_real_home(), ".claude")
    sandbox_claude = os.path.join(home, ".claude")
    os.makedirs(sandbox_claude, exist_ok=True)
    _copy_claude_config(real_claude, sandbox_claude)

    for sub in ("logs", "loop_logs", "tmp"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)

    return home
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_sandbox.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add cu/sandbox.py tests/test_sandbox.py
git commit -m "feat(cu): add sandbox bootstrap with .claude config copy"
```

---

### Task 4: cu/env.py — 阶段环境变量构建

**Files:**
- Create: `cu/env.py`
- Create: `tests/test_env.py`

- [ ] **Step 1: 写 test_env.py 失败测试**

```python
import os
from cu.env import stage_env


def test_stage_env_sets_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    env = stage_env(
        job_id="j1",
        repo="react",
        session_id="sess-uuid",
        github_url="https://github.com/facebook/react",
    )
    expected_home = str(tmp_path / "jobs" / "j1" / "home")
    assert env["HOME"] == expected_home
    assert env["PROJECTS_DIR"] == os.path.join(
        expected_home, "code-understand-react", "code"
    )
    assert env["OUTPUTS_DIR"] == expected_home
    assert env["CODE_UNDERSTAND_STATE_ROOT"] == expected_home
    assert env["SESSION_ID"] == "sess-uuid"
    assert "PATH" in env
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_env.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 cu/env.py**

```python
"""为各宏阶段子进程组装环境变量字典。

核心策略（§2）：继承当前进程环境 → 覆盖作业级变量。
"""
from __future__ import annotations

import os

from cu.paths import sandbox_home, artifact_root


def stage_env(
    *,
    job_id: str,
    repo: str,
    session_id: str = "",
    github_url: str = "",
) -> dict[str, str]:
    """返回完整的子进程环境变量（基于当前进程 + 作业覆盖）。"""
    home = sandbox_home(job_id)
    art = artifact_root(job_id, repo)
    env = os.environ.copy()

    env["HOME"] = home
    env["PROJECTS_DIR"] = os.path.join(art, "code")
    env["OUTPUTS_DIR"] = home
    env["CODE_UNDERSTAND_STATE_ROOT"] = home

    if session_id:
        env["SESSION_ID"] = session_id
    if github_url:
        env["GITHUB_URL"] = github_url

    return env
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_env.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add cu/env.py tests/test_env.py
git commit -m "feat(cu): add stage environment builder"
```

---

### Task 5: cu/runner.py — 子进程执行器

**Files:**
- Create: `cu/runner.py`
- Create: `tests/test_runner.py`

- [ ] **Step 1: 写 test_runner.py 失败测试**

```python
from cu.runner import run_script


def test_run_script_success(tmp_path):
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/bash\necho hello\n")
    script.chmod(0o755)
    result = run_script(str(script), env=None, cwd=str(tmp_path), timeout=10)
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_script_failure(tmp_path):
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/bash\nexit 42\n")
    script.chmod(0o755)
    result = run_script(str(script), env=None, cwd=str(tmp_path), timeout=10)
    assert result.returncode == 42
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 cu/runner.py**

```python
"""子进程执行：运行 bash 脚本并捕获输出。"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def run_script(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
) -> RunResult:
    cmd = ["bash", script] + (args or [])
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return RunResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(
            returncode=-1,
            stdout=e.stdout or "",
            stderr=e.stderr or f"timeout after {timeout}s",
        )


def run_python(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
) -> RunResult:
    cmd = ["python3", script] + (args or [])
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return RunResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(
            returncode=-1,
            stdout=e.stdout or "",
            stderr=e.stderr or f"timeout after {timeout}s",
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_runner.py -v`
Expected: 2 passed (仅在 Linux/macOS 通过；Windows 无 bash)

- [ ] **Step 5: Commit**

```bash
git add cu/runner.py tests/test_runner.py
git commit -m "feat(cu): add subprocess runner with timeout support"
```

---

### Task 6: 修改 download.sh — 去掉 git config --global

**Files:**
- Modify: `download.sh:71-72`

- [ ] **Step 1: 修改 download.sh**

将第 71-72 行:
```bash
git config --global user.email "temp@example.com"
git config --global user.name "temp"
```
替换为:
```bash
git -c user.email="temp@example.com" -c user.name="temp" init >/dev/null 2>&1
git -c user.email="temp@example.com" -c user.name="temp" add . >/dev/null 2>&1
git -c user.email="temp@example.com" -c user.name="temp" commit -m "Initial commit from $ZIP_URL" >/dev/null 2>&1
```
同时删掉原来紧跟的 `git init`、`git add`、`git commit` 三行（73-75），因为已合并到 `-c` 调用中。

替换后的 67-77 区段完整如下：
```bash
cd "$TARGET_DIR"

download_out "Initializing git repository ..."
git -c user.email="temp@example.com" -c user.name="temp" init >/dev/null 2>&1
git -c user.email="temp@example.com" -c user.name="temp" add . >/dev/null 2>&1
git -c user.email="temp@example.com" -c user.name="temp" commit -m "Initial commit from $ZIP_URL" >/dev/null 2>&1

download_out "All done! Repository initialized in $(pwd)"
```

- [ ] **Step 2: Commit**

```bash
git add download.sh
git commit -m "fix(download): use git -c instead of --global config"
```

---

### Task 7: 修改 build.sh — 增加 BUILD_DOC_ONLY 模式

**Files:**
- Modify: `build.sh:41-95`

- [ ] **Step 1: 在 build.sh 第 40 行后（`SESSION_FILE` 赋值之后、session 复制之前）插入模式判断**

在 `[[ -f "${SESSION_FILE}" ]] ...` 行之后，将当前的 session 复制+归一化+subagents 复制块（行 41-95）用条件包裹：

```bash
if [[ -z "${BUILD_DOC_ONLY:-}" ]]; then
  # --- 原有 session 复制、归一化、subagents 复制逻辑（行 41-95 原封不动） ---
  SESSIONS_ROOT="${DIR}/sessions"
  SESSION1="${SESSIONS_ROOT}/session1"
  DST_JSONL="${SESSION1}/session.jsonl"
  # ... 保持原有全部代码直到 subagents 复制完成 ...
fi
```

doc 生成部分（行 97 至文件末尾 — 从 `_OPENINGS` 数组开始）保持不变，始终执行。

- [ ] **Step 2: 验证 BUILD_DOC_ONLY 模式下跳过复制**

Run: `BUILD_DOC_ONLY=1 bash build.sh 2>&1 | head -5`（会因缺 repo/session 而报 usage，仅确认语法正确）
Expected: 正常打印 usage 信息，无语法错误

- [ ] **Step 3: Commit**

```bash
git add build.sh
git commit -m "feat(build): add BUILD_DOC_ONLY mode to skip session copy"
```

---

### Task 8: 新建 scripts/metadata.sh — 从 pack.sh 提取 metadata 生成

**Files:**
- Create: `scripts/metadata.sh`

- [ ] **Step 1: 创建 scripts/metadata.sh**

从 `pack.sh` 的步骤 2（行 64-102）提取，改为独立脚本：

```bash
#!/usr/bin/env bash
# 生成 metadata.json 与空 questions.json。
# 用法: metadata.sh <github_url> <repo>
# 环境变量:
#   ARTIFACT_ROOT   code-understand-{repo} 产物根（必须已存在 code/{repo}/）
# 依赖: bash, jq, curl, 同目录 evaluate.sh, classify.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_SCRIPT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
EVALUATE_SH="${REPO_SCRIPT_DIR}/evaluate.sh"
CLASSIFY_SH="${REPO_SCRIPT_DIR}/classify.sh"

meta_out() { printf '[metadata.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
meta_err() { printf '[metadata.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

[[ "${#}" -eq 2 ]] || { meta_err "用法: $0 <github_url> <repo>"; exit 1; }
github="${1}"
repo="${2}"

: "${ARTIFACT_ROOT:?需要设置 ARTIFACT_ROOT 环境变量}"
code_dir="${ARTIFACT_ROOT}/code/${repo}"
[[ -d "${code_dir}" ]] || { meta_err "错误: code 目录不存在: ${code_dir}"; exit 1; }

META="${ARTIFACT_ROOT}/metadata.json"

api_url="${github//github.com/api.github.com/repos}"
curl_url="https://gh-proxy.org/${api_url}"
main_language="unknown"
if api_json="$(curl -fsSL -- "${curl_url}" 2>/dev/null)"; then
  main_language="$(printf '%s' "${api_json}" | jq -r '(.language // "") | ascii_downcase' 2>/dev/null || printf '')"
  [[ -n "${main_language}" ]] || main_language="unknown"
  meta_out "main_language: ${main_language}"
else
  meta_err "警告: 无法拉取 GitHub API, main_language=unknown"
fi

difficulty_level="$(bash "${EVALUATE_SH}" "${code_dir}" | tr -d '\r' | head -n1)"
[[ -n "${difficulty_level}" ]] || difficulty_level="medium"
meta_out "difficulty_level: ${difficulty_level}"

main_type="$(bash "${CLASSIFY_SH}" "${code_dir}" "${github}" | tr -d '\r' | head -n1)"
[[ -n "${main_type}" ]] || main_type="Unknown"
meta_out "main_type: ${main_type}"

jq -n \
  --arg github "${github}" \
  --arg ml "${main_language}" \
  --arg mt "${main_type}" \
  --arg dl "${difficulty_level}" \
  '{
    basic_info: {
      repo_name: $github,
      main_language: $ml,
      github: { url: $github, star: 0 }
    },
    category_info: { main_type: $mt },
    difficulty: { level: $dl },
    extra_info: { input_token: 0, output_token: 0 }
  }' >"${META}"

printf '%s\n' '[]' >"${ARTIFACT_ROOT}/questions.json"

meta_out "metadata.json 与 questions.json 已生成"
```

- [ ] **Step 2: 加执行权限**

Run: `chmod +x scripts/metadata.sh`

- [ ] **Step 3: Commit**

```bash
git add scripts/metadata.sh
git commit -m "feat(scripts): extract metadata generation from pack.sh"
```

---

### Task 9: 新建 scripts/clean_artifacts.sh — 产物树清理

**Files:**
- Create: `scripts/clean_artifacts.sh`

- [ ] **Step 1: 创建 scripts/clean_artifacts.sh**

```bash
#!/usr/bin/env bash
# 清理产物树中不应提交的文件。
# 用法: clean_artifacts.sh <artifact_root>
#   artifact_root 即 code-understand-{repo}/ 根
set -euo pipefail

clean_out() { printf '[clean_artifacts.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

[[ "${#}" -eq 1 ]] || { echo "用法: $0 <artifact_root>" >&2; exit 1; }
artifact_root="${1}"
[[ -d "${artifact_root}" ]] || { echo "错误: 目录不存在: ${artifact_root}" >&2; exit 1; }

deleted=0

while IFS= read -r -d '' f; do
  rm -f -- "${f}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type f -name '.DS_Store' -print0 2>/dev/null)

while IFS= read -r -d '' d; do
  rm -rf -- "${d}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type d -name '__MACOSX' -print0 2>/dev/null)

while IFS= read -r -d '' f; do
  rm -f -- "${f}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type f -name '._*' -print0 2>/dev/null)

while IFS= read -r -d '' f; do
  rm -f -- "${f}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type f -name '.gitkeep' -print0 2>/dev/null)

clean_out "已清理 ${deleted} 项"
```

- [ ] **Step 2: 加执行权限**

Run: `chmod +x scripts/clean_artifacts.sh`

- [ ] **Step 3: Commit**

```bash
git add scripts/clean_artifacts.sh
git commit -m "feat(scripts): add artifact tree cleanup script"
```

---

### Task 10: 新建 scripts/export_session.sh — 会话导出到产物树

**Files:**
- Create: `scripts/export_session.sh`

- [ ] **Step 1: 创建 scripts/export_session.sh**

从 `build.sh` 提取 session 复制 + model 归一化逻辑，作为独立脚本：

```bash
#!/usr/bin/env bash
# 从沙箱 ~/.claude/projects/... 复制 session.jsonl + subagents 到产物树 sessions/session1/，
# 并归一化 JSONL 内 model 字段。
# 用法: export_session.sh <repo> <session_id>
# 环境变量:
#   ARTIFACT_ROOT   code-understand-{repo} 产物根
#   HOME            沙箱 HOME（.claude/projects 在此之下）
set -euo pipefail

exp_out() { printf '[export_session.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
exp_err() { printf '[export_session.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

[[ "${#}" -eq 2 ]] || { exp_err "用法: $0 <repo> <session_id>"; exit 1; }
repo="${1}"
session="${2}"
: "${ARTIFACT_ROOT:?需要设置 ARTIFACT_ROOT}"
command -v jq >/dev/null 2>&1 || { exp_err "错误: 需要 jq"; exit 1; }

repo_slug="${repo//_/-}"
USER="${USER:-$(id -un 2>/dev/null || printf unknown)}"
CLAUDE_PROJECTS="${HOME}/.claude/projects/-home-${USER}-projects-${repo_slug}"
SESSION_FILE="${CLAUDE_PROJECTS}/${session}.jsonl"
SA_SRC="${CLAUDE_PROJECTS}/${session}/subagents"

[[ -f "${SESSION_FILE}" ]] || { exp_err "错误: session 文件不存在: ${SESSION_FILE}"; exit 1; }

SESSION1="${ARTIFACT_ROOT}/sessions/session1"
DST_JSONL="${SESSION1}/session.jsonl"
mkdir -p "${SESSION1}"

exp_out "复制 session → ${DST_JSONL}"
cp -a -- "${SESSION_FILE}" "${DST_JSONL}"

exp_out "归一化 JSONL 内 model 字段"
jq_normalize_line='(
  if (.message | type) == "object" and (.message | has("model")) and .message.model != "model" then
    .message.model = "model"
  else . end
)
| (if (.data | type) == "object" and (.data.message | type) == "object" and (.data.message.message | type) == "object"
      and (.data.message.message | has("model")) and .data.message.message.model != "model" then
    .data.message.message.model = "model"
  else . end)'

tmp_jsonl="$(mktemp)"
trap 'rm -f -- "${tmp_jsonl}"' EXIT
changed=0
while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line//[:space:]}" ]] && continue
  if ! jq -e . >/dev/null 2>&1 <<<"${line}"; then
    exp_err "警告: 跳过无法解析的 JSONL 行"
    continue
  fi
  before_s="$(jq -cS . <<<"${line}")"
  out="$(jq -c "${jq_normalize_line}" <<<"${line}" 2>/dev/null)" || { exp_err "错误: jq 归一化失败"; exit 1; }
  after_s="$(jq -cS . <<<"${out}")"
  [[ "${before_s}" == "${after_s}" ]] || changed=$((changed + 1))
  printf '%s\n' "${out}" >>"${tmp_jsonl}"
done <"${DST_JSONL}"
mv -f -- "${tmp_jsonl}" "${DST_JSONL}"
trap - EXIT
exp_out "model 字段归一化完成（变更: ${changed}）"

if [[ -d "${SA_SRC}" ]]; then
  mkdir -p "${SESSION1}/subagents"
  cp -a -- "${SA_SRC}/." "${SESSION1}/subagents/"
  exp_out "subagents 复制完成"
else
  exp_out "未找到 subagents 目录（跳过）"
fi

exp_out "会话导出完成"
```

- [ ] **Step 2: 加执行权限**

Run: `chmod +x scripts/export_session.sh`

- [ ] **Step 3: Commit**

```bash
git add scripts/export_session.sh
git commit -m "feat(scripts): add session export with model normalization"
```

---

### Task 11: 修改 rewrite.py — 单会话 + 非交互模式

**Files:**
- Modify: `rewrite.py:219-316`

- [ ] **Step 1: 在 argparse 中增加两个可选参数**

在 `rewrite.py` 的 `main()` 函数 argparse 部分（约行 220-227）增加：

```python
ap.add_argument(
    "--single-source", action="store_true",
    help="仅编辑 sourcePath（沙箱模式下无需同步 copyPath）",
)
ap.add_argument(
    "--non-interactive", action="store_true",
    help="跳过 gedit，直接读取已存在的 tmp 文件并写回",
)
```

- [ ] **Step 2: 修改 main() 中的 copyPath 写回逻辑**

在 `source_to_new` 映射构建完成后、写回 copyPath 之前（约行 296-308），用 `args.single_source` 包裹：

```python
if not args.single_source:
    if not os.path.isfile(copy_path):
        print(
            f"错误: copyPath 不存在，无法同步写回: {copy_path}\n"
            "请先执行 build.sh 生成 sessions/session1/session.jsonl，或检查 OUTPUTS_DIR。",
            file=sys.stderr,
        )
        return 1
    with open(copy_path, "r", encoding="utf-8") as f:
        raw_copy = f.readlines()

write_jsonl_with_text_mapping(source_path, raw_source, source_to_new)
if not args.single_source:
    write_jsonl_with_text_mapping(copy_path, raw_copy, source_to_new)
    print(f"已写回 sourcePath 与 copyPath。")
else:
    print(f"已写回 sourcePath（单会话模式）。")
```

- [ ] **Step 3: 修改编辑循环支持 non-interactive**

将编辑循环（约行 266-285）的 `while True:` 块用条件包裹：

```python
if args.non_interactive:
    if not os.path.isfile(tmp_path):
        print(f"错误: --non-interactive 模式下 tmp 文件不存在: {tmp_path}", file=sys.stderr)
        return 1
    try:
        new_list = load_questions_from_tmp(tmp_path)
    except ValueError as e:
        print(f"读取编辑结果失败: {e}", file=sys.stderr)
        return 1
    if len(new_list) != len(questions):
        print(
            f"错误: 行数不一致（{len(new_list)} vs {len(questions)}）",
            file=sys.stderr,
        )
        return 1
else:
    # 原有 while True gedit 循环保持不变
    ...
```

- [ ] **Step 4: Commit**

```bash
git add rewrite.py
git commit -m "feat(rewrite): add --single-source and --non-interactive modes"
```

---

### Task 12: cu/stages.py — 4 宏阶段定义与执行

**Files:**
- Create: `cu/stages.py`

- [ ] **Step 1: 创建 cu/stages.py**

```python
"""4 宏阶段定义与执行逻辑。

阶段序：bootstrap → conversation → compile → build
每个阶段函数接收 JobContext，调用 bash/python 子进程并返回成功/失败。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from cu.paths import sandbox_home, artifact_root, code_dir, snapshots_dir
from cu.sandbox import bootstrap_sandbox
from cu.env import stage_env
from cu.runner import run_script, run_python, RunResult


STAGES = ("bootstrap", "conversation", "compile", "build")


@dataclass
class JobContext:
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str = ""


def _repo_root() -> str:
    """本仓库的根目录（cu/ 的父目录）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _check(result: RunResult, stage: str, step: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"[{stage}/{step}] exit={result.returncode}\n"
            f"stdout: {result.stdout[-2000:]}\n"
            f"stderr: {result.stderr[-2000:]}"
        )


def run_bootstrap(ctx: JobContext) -> None:
    repo_root = _repo_root()
    home = bootstrap_sandbox(ctx.job_id)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        github_url=ctx.github_url,
    )
    art = artifact_root(ctx.job_id, ctx.repo)
    os.makedirs(os.path.join(art, "code"), exist_ok=True)

    result = run_script(
        os.path.join(repo_root, "download.sh"),
        args=[ctx.zip_url],
        env=env,
        cwd=repo_root,
    )
    _check(result, "bootstrap", "download")


def run_conversation(ctx: JobContext) -> None:
    repo_root = _repo_root()
    target_path = code_dir(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
    )

    result = run_script(
        os.path.join(repo_root, "loop.sh"),
        args=[target_path, ctx.repo],
        env=env,
        cwd=repo_root,
    )
    _check(result, "conversation", "loop")
    if not ctx.session_id:
        ctx.session_id = result.stdout.strip().split("\n")[-1].strip()

    result = run_script(
        os.path.join(repo_root, "clean.py"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
    )
    # clean.py is called via python3
    result = run_python(
        os.path.join(repo_root, "clean.py"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
    )
    _check(result, "conversation", "clean")

    env["BUILD_DOC_ONLY"] = "1"
    result = run_script(
        os.path.join(repo_root, "build.sh"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
    )
    _check(result, "conversation", "build-doc")


def run_compile(ctx: JobContext) -> None:
    repo_root = _repo_root()
    art = artifact_root(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
    )
    env["ARTIFACT_ROOT"] = art

    result = run_script(
        os.path.join(repo_root, "scripts", "metadata.sh"),
        args=[ctx.github_url, ctx.repo],
        env=env,
        cwd=repo_root,
    )
    _check(result, "compile", "metadata")

    result = run_script(
        os.path.join(repo_root, "scripts", "clean_artifacts.sh"),
        args=[art],
        env=env,
        cwd=repo_root,
    )
    _check(result, "compile", "clean-artifacts")

    target_path = code_dir(ctx.job_id, ctx.repo)
    if os.path.isdir(os.path.join(target_path, ".git")):
        run_script(
            "bash", args=["-c", f"cd '{target_path}' && git reset --hard HEAD && rm -rf .git"],
            env=env, cwd=target_path,
        )


def run_build(ctx: JobContext) -> None:
    repo_root = _repo_root()
    art = artifact_root(ctx.job_id, ctx.repo)
    env = stage_env(
        job_id=ctx.job_id, repo=ctx.repo,
        session_id=ctx.session_id,
        github_url=ctx.github_url,
    )
    env["ARTIFACT_ROOT"] = art

    result = run_python(
        os.path.join(repo_root, "rewrite.py"),
        args=[ctx.repo, "--single-source", "--non-interactive"],
        env=env,
        cwd=repo_root,
    )
    _check(result, "build", "rewrite")

    result = run_script(
        os.path.join(repo_root, "scripts", "export_session.sh"),
        args=[ctx.repo, ctx.session_id],
        env=env,
        cwd=repo_root,
    )
    _check(result, "build", "export-session")

    result = run_script(
        os.path.join(repo_root, "zip.sh"),
        args=[art],
        env=env,
        cwd=repo_root,
    )
    _check(result, "build", "zip")


STAGE_RUNNERS = {
    "bootstrap": run_bootstrap,
    "conversation": run_conversation,
    "compile": run_compile,
    "build": run_build,
}
```

- [ ] **Step 2: Commit**

```bash
git add cu/stages.py
git commit -m "feat(cu): add 4 macro-stage definitions and runners"
```

---

### Task 13: cu/cli.py + cu/__main__.py — CLI 入口

**Files:**
- Create: `cu/cli.py`
- Create: `cu/__main__.py`

- [ ] **Step 1: 创建 cu/cli.py**

```python
"""CLI 入口：python -m cu run <zip_url>"""
from __future__ import annotations

import argparse
import sys
import uuid

from cu.stages import JobContext, STAGES, STAGE_RUNNERS


def _parse_github_url(zip_url: str) -> tuple[str, str, str]:
    """从 zip URL 解析 user, repo, branch。"""
    import re
    m = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip",
        zip_url,
    )
    if not m:
        raise ValueError(f"URL 不符合 GitHub archive ZIP 格式: {zip_url}")
    return m.group(1), m.group(2), m.group(3)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        gh_user, repo, branch = _parse_github_url(args.zip_url)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    job_id = args.job_id or f"{repo}-{uuid.uuid4().hex[:8]}"
    github_url = f"https://github.com/{gh_user}/{repo}"

    ctx = JobContext(
        job_id=job_id,
        repo=repo,
        zip_url=args.zip_url,
        github_url=github_url,
    )

    start = STAGES.index(args.start) if args.start else 0
    end = STAGES.index(args.end) + 1 if args.end else len(STAGES)

    for stage_name in STAGES[start:end]:
        print(f"\n{'='*60}")
        print(f"  Stage: {stage_name}")
        print(f"{'='*60}")
        try:
            STAGE_RUNNERS[stage_name](ctx)
            print(f"  ✓ {stage_name} 完成")
        except RuntimeError as e:
            print(f"  ✗ {stage_name} 失败:\n{e}", file=sys.stderr)
            return 1

    print(f"\n作业 {job_id} 全部完成。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="cu", description="代码质检工作流 CLI")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="执行完整流水线")
    run_p.add_argument("zip_url", help="GitHub archive ZIP URL")
    run_p.add_argument("--job-id", help="自定义 job ID（默认自动生成）")
    run_p.add_argument("--start", choices=list(STAGES), help="从指定阶段开始")
    run_p.add_argument("--end", choices=list(STAGES), help="在指定阶段结束")

    args = parser.parse_args()
    if args.command == "run":
        return cmd_run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 创建 cu/__main__.py**

```python
from cu.cli import main

raise SystemExit(main())
```

- [ ] **Step 3: 验证 CLI help**

Run: `python -m cu --help`
Expected: 显示 `cu` 帮助信息，包含 `run` 子命令

Run: `python -m cu run --help`
Expected: 显示 `zip_url`、`--job-id`、`--start`、`--end` 参数

- [ ] **Step 4: Commit**

```bash
git add cu/cli.py cu/__main__.py
git commit -m "feat(cu): add CLI entry point with run command"
```

---

### Task 14: 阶段集成测试（mock claude）

**Files:**
- Create: `tests/test_stages.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: 创建 tests/conftest.py 共享 fixtures**

```python
"""Shared pytest fixtures."""
import os
import pytest


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """提供隔离的 CU_DATA_ROOT 和模拟 HOME。"""
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / ".claude").mkdir()
    (real_home / ".claude" / "credentials.json").write_text("{}")

    cu_root = tmp_path / "cu_data"
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("CU_DATA_ROOT", str(cu_root))
    return {
        "real_home": real_home,
        "cu_root": cu_root,
        "tmp_path": tmp_path,
    }
```

- [ ] **Step 2: 创建 tests/test_stages.py**

```python
"""阶段执行集成测试 — 使用 mock 验证调用链。"""
import os
from unittest.mock import patch, MagicMock
from cu.stages import JobContext, run_bootstrap
from cu.paths import sandbox_home, artifact_root, code_dir
from cu.runner import RunResult


def test_bootstrap_creates_sandbox_and_calls_download(isolated_env):
    ctx = JobContext(
        job_id="test-001",
        repo="my-repo",
        zip_url="https://github.com/owner/my-repo/archive/refs/heads/main.zip",
        github_url="https://github.com/owner/my-repo",
    )
    fake_result = RunResult(returncode=0, stdout="ok\n", stderr="")
    with patch("cu.stages.run_script", return_value=fake_result) as mock_run:
        run_bootstrap(ctx)

    home = sandbox_home("test-001")
    assert os.path.isdir(home)
    assert os.path.isdir(os.path.join(home, ".claude", "projects"))
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert "download.sh" in call_args[0][0]
    assert ctx.zip_url in call_args[0][1]
    assert call_args[1]["env"]["HOME"] == home
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/test_stages.py -v`
Expected: 1 passed

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_stages.py
git commit -m "test: add stage integration test with mock subprocess"
```

---

### Task 15: 端到端冒烟测试脚本

**Files:**
- Create: `tests/test_e2e_smoke.py`

- [ ] **Step 1: 创建冒烟测试**

此测试在 **真实 Ubuntu 环境** 下运行（需要 `claude`、网络等），用 `@pytest.mark.slow` 标记；日常可用 **`pytest -m "not slow"`** 跳过：

```python
"""端到端冒烟测试 — 需要真实环境（claude, 网络）。
运行: pytest tests/test_e2e_smoke.py -v -m slow
"""
import os
import subprocess
import pytest


@pytest.mark.slow
def test_cli_bootstrap_only(tmp_path, monkeypatch):
    """仅跑 bootstrap 阶段，验证沙箱和代码下载。"""
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "cu"))
    url = "https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip"
    result = subprocess.run(
        ["python", "-m", "cu", "run", url, "--start", "bootstrap", "--end", "bootstrap",
         "--job-id", "smoke-test"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    home = tmp_path / "cu" / "jobs" / "smoke-test" / "home"
    assert home.is_dir()
    code = home / "code-understand-nocode" / "code" / "nocode"
    assert code.is_dir()
    assert (code / "README.md").is_file()
```

- [ ] **Step 2: 配置 pytest markers**

在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中增加：

```toml
markers = ["slow: requires real environment (claude, network)"]
```

- [ ] **Step 3: 运行（跳过 slow）**

Run: `pytest tests/ -v -m "not slow"`
Expected: 所有非 slow 测试通过

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_smoke.py pyproject.toml
git commit -m "test: add e2e smoke test for bootstrap stage"
```

---

## 自检

1. **Spec 覆盖**：§2（沙箱 bootstrap）✓、§3（目录布局）✓、§3.4（运行时数据不在仓库下）✓、§4（4 宏阶段）✓、§5（rewrite 单会话）✓。§6（快照）、§7（Web UI）、§8（删除）属于 P2/P3。
2. **占位符扫描**：无 TBD / TODO。
3. **类型一致**：`JobContext` 在 stages.py 定义、cli.py 引用，字段名对齐；`RunResult` 在 runner.py 定义、stages.py/test 引用。`stage_env` 签名在 env.py 与 stages.py 调用一致。
4. **路径一致**：`scripts/metadata.sh`、`scripts/clean_artifacts.sh`、`scripts/export_session.sh` 在 stages.py 中通过 `_repo_root() + "scripts/..."` 引用；`download.sh`、`loop.sh`、`build.sh`、`clean.py`、`rewrite.py`、`zip.sh` 通过 `_repo_root() + filename` 引用。

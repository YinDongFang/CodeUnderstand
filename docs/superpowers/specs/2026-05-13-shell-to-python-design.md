# Shell → Python 流水线迁移 — 设计说明

**日期**：2026-05-13  
**状态**：草案（§1 已与负责人对齐）

## 目标与约束

- **范围**：仓库内 **仅保留 `install.sh`**；**其余历史 `.sh` 已删除**，能力以 **`python -m cu.*`** 为准，并与 **`cu/stages/`** + orchestrator 对齐。
- **运行环境**：生产验收以 **Ubuntu** 为准；开发可在其它 OS，代码 **不要求兼容 Windows**。
- **实现**：下载、解压、打包等 **用 Python 标准库 / 选定依赖完成**，不为省事挂 `curl`/`zip`/`bash` 子进程承载核心业务逻辑。
- **错误处理**：下载 / 解压 / 归档等 **不做细粒度分支与错误码**；失败 **抛异常**，由 **宏阶段（stage）** 边界统一落入现有 **`_check` / `RuntimeError` → orchestrator 失败语义**（含 stdout/stderr tail 的策略可按步骤微调，见 §2）。
- **迁移策略**：**横向基建（`cu/runtime/`）+ 纵向按宏阶段替换**（先做通用能力，再按 `STAGE_RUNNERS` 逐个摘掉 `run_script`）。

---

## §1 架构与错误模型（已认可）

### 模块边界

| 区域 | 职责 |
|------|------|
| **`cu/runtime/`** | **`download_github_zip`**：HTTP 拉取 → 临时 zip → **同模块内** ``ZipFile`` 解压 → 目录整理（**不对**下载树做 `git init`/`commit`）；**`archive.archive`** 将整个产物目录打成 zip（库函数，无路径过滤）。失败 **`raise`**。不设独立 **`extract`** 子包，解压即 `download` 模块职责。 |
| **`cu/stages/`** | **每个宏阶段一个模块**（`bootstrap.py`、`conversation.py`、`compile.py`、`build.py`），含 **`run_<stage>(ctx)`** 与 **`python -m cu.stages.<stage> spec.json`** 调试入口；步骤由内调用 **`cu.pipeline.*` / `cu.runtime.*`**；编排外本地一条龙见 **`cu.pipeline.run_github_zip`**、打包见 **`cu.pipeline.pack`**。 |
| **`cu/job_worker.py` + `cu/job_runner_core.py`** | **生产默认：每个作业一个子进程**（`python -m cu.job_worker`），子进程继承的 **`HOME`** 已由 **`stage_env`** 设为沙箱目录，保证 **`expanduser("~")`、Claude 路径与流水线 Python 代码一致**。阶段事件经 **`socket.socketpair`**（`CU_JOB_EVENT_FD`）回传父进程以转发 SSE。 |
| **`CU_JOB_WORKER_INLINE`** | **pytest / 需要 patch `STAGE_RUNNERS` 的集成测试**：在 fixture 中置 **`1`**，编排线程 **直接调用 `job_runner_core.run_stages_in_process`**（同进程）；生产 **不设** 该变量。 |
| **`clean` / `rewrite`** | 仅在 **`cu/pipeline/clean.py`**、**`cu/pipeline/rewrite.py`**；编排 **进程内 import**。仓库根 **无** `clean.py` / `rewrite.py` 等薄转发。 |
| **`install.sh`** | 唯一允许的 Shell 引导脚本（运维手动执行，**禁止**写入令牌/远程拉取链）；**不由 `STAGE_RUNNERS` 调用**。 |

### 错误模型

- **`cu/runtime`**：`RuntimeError("前缀: …")` 即可；首版 **不引入** 单独异常类型（若日后需要 grep / 结构化再引入 `StageExecutionError`）。
- **`stages`**：沿用 **`_check(RunResult, …)`**（仍跑子进程的步骤）与 **`RuntimeError`**（纯 Python 步骤可直接抛）；与 orchestrator **failed / log_tail** 对齐。

---

## §2 与 `cu.runner` / 子进程的边界

### 原则

1. **流水线业务步骤**：默认 **Python 库或本包模块内调用**，不经过 `bash *.sh`。
2. **`cu.runner`**：**保留**，用于仍需 **隔离进程** 的场景（见下），统一 **`pid_sink`**、**`timeout`**、**`stream`**、与 **`RunResult`**。

### 仍可使用子进程的情况（预期）

| 场景 | 机制 | 说明 |
|------|------|------|
| **真实 Claude CLI** | `subprocess` / `run_python` / 专用封装 | 业务依赖外部 `claude` 可执行文件；测试模式下走 **`python -m cu.testing.mock_claude`**（`CU_TEST_MODE` + overlay；实现见 **`cu/testing/mock_claude.py`**）。 |
| **流式日志 / cancel** | **`_spawn_and_wait(..., stream=True)`** | `cu.pipeline.build_docs` 等仍需子进程 + stream 的步骤继续 **stream**；loop 亦为 **Python 子进程**（`python -m cu.pipeline.loop`）。**clean / rewrite 题目写回** 在 **`cu.pipeline`** 进程内执行。取消以 **终止 `cu.job_worker` 子进程 PID**（及宿主编排线程内的 `pid_holder`）为主。 |
| **Editor / 可选 GUI** | **`cu.pipeline.rewrite`** 可选调 ``gedit`` 等 | 非自动化路径；**不要求**改为无子进程。 |
| **外部 git**（边界外） | `subprocess` | 仅用于 **本仓库** 的 `git pull/reset`（如 `run_github_zip` 更新工具链）、或用户自管；**不对** ZIP 解压得到的项目树在 `download_github_zip` 内做 `git init`。 |

### `cu/env.py` + `cu/test_mode.py`

- **`test_mode_subprocess_overlay()`** 向子进程注入 `*_USE_MOCK_CLAUDE`。**同名环境变量**由 **Python 版 loop/build/classify** 读取并分支到 **`cu.testing.mock_claude`**；亦可收敛为 **`CU_TEST_MODE` 单一分支**（实现阶段二选一）。
- **必须**：本地 **`pytest`**（``CU_TEST_MODE``）**不依赖** 本机安装 **`claude`**，mock 行为与历史的 mock 语义等价。

### `run_script` 归宿

- 流水线全部迁移完成后：**仓库内 `run_script` 可无调用方**（或仅保留测试/fixture）；**不删除 API** 亦可，以免外部插件依赖。
- **`install.sh`**：继续由运维/文档手动执行，**不走 `run_script`**。

---

## §3 测试策略

1. **`tests/test_stages.py`**  
   - patch 各 **`cu.stages.<stage>`** 中的子进程封装（例如 **`conversation.run_module`**）。  

2. **`cu/runtime/` 单元测试**  
   - **download**：mock `urlopen`，断言解压目录布局；非 200 抛错（不做重试 / 429 细分）。解压语义包含在 **`download_github_zip`** 内，不单测虚构的 extract 模块。  
   - **archive**：`tmp_path` 下构造最小目录树并 zip，断言 **`archive.archive`** 产物路径与条目。

3. **回归**  
   - 保留 **e2e smoke**（``slow`` 标记）；若有 **真机 Claude**，仅在显式关掉 `CU_TEST_MODE` 的测试中运行。

---

## CLI 清册（仓库内已无除 `install.sh` 外的 `.sh`）

| 能力 | 入口 |
|------|------|
| GitHub ZIP 下载 + 解压（不对解压树 git init） | 库调用 ``cu.runtime.download.download_github_zip`` |
| 双 Agent loop | ``python -m cu.pipeline.loop`` |
| 文档生成（含 ``BUILD_DOC_ONLY``） | ``python -m cu.pipeline.build_docs`` |
| 体量评估 | ``python -m cu.pipeline.evaluate`` |
| 项目分类 | ``python -m cu.pipeline.classify`` |
| 产物目录 zip | 库调用 ``cu.runtime.archive.archive`` |
| metadata / questions | ``python -m cu.pipeline.metadata``（需 ``ARTIFACT_ROOT``） |
| 产物树清理 | ``python -m cu.pipeline.clean_artifacts`` |
| 会话导出到产物树 | ``python -m cu.pipeline.export_session``（需 ``ARTIFACT_ROOT`` 等） |
| Mock Claude | ``python -m cu.testing.mock_claude`` |
| 打包（复制 code + metadata + rewrite + zip） | ``python -m cu.pipeline.pack`` |
| 本地一条龙（ZIP URL → … → pack） | ``python -m cu.pipeline.run_github_zip`` |
| 按 repo 清理本机 Claude/output/zip | ``python -m cu.pipeline.cleanup_outputs`` |
| **唯一 Shell：引导安装** | **`install.sh`**（手动执行，编排不调用） |

---

## 自检清单

- [ ] 设计与 **§1 约束**（仅保留 install、全 Python 流水线、stage 级错误）无冲突。  
- [ ] **§2** 明确：**业务不走 bash**；**Claude / 流式 / cancel** 仍可子进程；**mock 去 shell 化**。  
- [ ] **§3** 覆盖 stages 集成测试与 runtime 单元测试分工。  
- [ ] **CLI 清册**：除 **`install.sh`** 外仓库根无 `.sh`；能力与上表一致。

---

## 后续（不在本文档内展开）

- **`writing-plans`**：按宏阶段纵向切片拆 PR（bootstrap → conversation → compile → build）。  
- **依赖**：若 HTTP 仅用标准库 **`urllib`**，可不新增依赖；若统一 **`httpx`**，需在 `pyproject` / `requirements` 明示并锁定用途。

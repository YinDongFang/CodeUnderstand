# P2 · Ubuntu 真机验证清单

> 在 Ubuntu 上从 git 拉取最新 `mindflow` 后逐项执行。
> 所有命令默认在仓库根目录运行。生产默认数据根为 **`$HOME/.code-understand`**；可用环境变量 **`CU_DATA_ROOT`** 覆盖（须与仓库目录分离）。
>
> API 字段与 CLI 子命令综述见：**`docs/superpowers/specs/p2-api-reference.md`**。

---

## 0. 准备

```bash
cd ~ && git clone <repo> CodeUnderstand && cd CodeUnderstand
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
# 可选：确认 cu 指向本仓库 venv
which cu && cu --help | head -5

# 验证外部依赖（按你实际流水线需要）
which claude jq wget unzip zip curl git bash python3
# gedit 仅 rewrite 交互用；rewrite 未纳入 P2 编排时可缺省
which gedit 2>/dev/null || true
```

预期：`pip install -e ".[test]"` 成功后本地出现可编辑安装的元数据目录（形如 `*.egg-info/`，已由 `.gitignore` 忽略）；`cu` 子命令列表含 `run,list,show,rerun,cancel,delete,serve`。

---

## 1. 单元 + 集成测试

```bash
python -m pytest tests/ -v -m "not slow"
```

预期：**pytest 收集约 67 passed, 1 deselected**（以你本机输出为准；随版本会略增）。

---

## 2. P1 e2e 冒烟（bootstrap 真跑）

```bash
python -m pytest tests/test_e2e_smoke.py -v -m slow
```

**说明：** 测试里通过 `monkeypatch` 将 **`CU_DATA_ROOT`** 指到 pytest 临时目录（例如 `/tmp/pytest-*/…/cu`），**不是**默认的 `~/.code-understand`。因此断言的是「脚本退出码与临时目录下的目录树」，而非主目录路径。

预期：**1 passed**；临时根下存在（`$CU_DATA_ROOT` 由测试注入，通常为 `tmp_path/cu`）：

```text
jobs/smoke-test/home/code-understand-nocode/code/nocode/README.md
```

（及以下载产生的 `.git/` 等，与 ``cu.runtime.download.download_github_zip`` 行为一致。）

CLI 等价命令（可自行在 shell 验证，数据将落在 **`CU_DATA_ROOT` / 默认 `~/.code-understand`**）：

```bash
export CU_DATA_ROOT="${CU_DATA_ROOT:-$HOME/.code-understand/manual-smoke}"
python -m cu run \
  https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip \
  --start bootstrap --end bootstrap \
  --job-id smoke-test
```

预期：**退出码 0**（区间内阶段均成功时，作业行可能仍为 `pending`，属正常现象，见 **`p2-api-reference.md`** 中「部分阶段跑完」语义）。

---

## 环境与测试接缝（可选，缩短耗时）

以下内容不影响「完成标准」，但适合你**不想调用真实 long-running ``cu.pipeline.loop``** 时做快速自检。

| 方式 | 作用 |
|------|------|
| **`CU_TEST_MODE=1`** | 由 **`cu.env.stage_env()`** 向子进程注入 **`LOOP_USE_MOCK_CLAUDE=1`**、**`BUILD_USE_MOCK_CLAUDE=1`**、**`CLASSIFY_USE_MOCK_CLAUDE=1`**：**仍执行** **`python -m cu.pipeline.loop`** 与 conversation 内的 **`python -m cu.pipeline.build_docs`（BUILD_DOC_ONLY）**、`compile` 中的 **`classify_main_type`**，但 Claude 调用统一走 **`python -m cu.testing.mock_claude`**。实现与真值表见 **`cu/test_mode.py`**。**不要**将此结果当作生产等价验证。 |
| **`cu run --start STAGE [--end STAGE]`** | 只跑闭合区间 **`[start, end]`**；`end` 早于 `build` 时成功后作业 **`status` 常为 `pending`**，CLI 按区间内阶段是否全 `success` 决定退出码。 |
| **`pytest` + patch** | 不被子进程继承；最快回归用 **`tests/test_orchestrator.py`**、**`tests/test_stages.py`**、**`tests/test_api.py`**（FastAPI `TestClient`）。 |

示例（子进程仍会跑后续 Python 模块，但整体比真实多轮 Claude **短得多**）：

```bash
export CU_TEST_MODE=1

cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id u-fast
```

---

## 3. P2 CLI 端到端（四阶段全跑）

### 3.1 真实 loop（耗时最长）

```bash
unset CU_TEST_MODE    # 或 export CU_TEST_MODE=0 —— 确保子进程不因测试模式走 mock

cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id u-001
```

预期：

- stdout 实时打印 **`[bootstrap]` … `[build]`** 形如 **`step:`** / **`done`** 前缀的阶段事件。
- 全链跑完后作业 **`success`**。
- 目录与快照：

```bash
ls "$HOME/.code-understand/jobs/u-001/"    # home/  snapshots/（若改过 CU_DATA_ROOT 则替换路径）
ls "$HOME/.code-understand/jobs/u-001/snapshots/"
# post-bootstrap.tar  post-conversation.tar  post-compile.tar
ls "$HOME/.code-understand/jobs/u-001/home/"
```

```bash
cu show u-001
```

预期：四阶段均 **`success`**，`attempt` 均为 **1**（除非前面重跑过）。

### 3.2 可选：配合 `CU_TEST_MODE` 的 mock 全链冒烟

若在 **§环境与测试接缝** 中已 **`export CU_TEST_MODE=1`**，再执行与 **§3.1** 相同的 `cu run`，用于验证 **`download → conversation（mock loop/doc）→ compile（mock classify）→ build`** 的编排与落盘；**仍会启动** **`cu.pipeline.loop`** 子进程，但不会调用真实 `claude`。**不要**将此结果当成生产等价验证。

---

## 4. P2 重跑（恢复快照）

```bash
# 故意污染 home/，再 rerun compile（路径按你的 CU_DATA_ROOT 调整）
echo "junk" > "$HOME/.code-understand/jobs/u-001/home/JUNK.txt"
cu rerun u-001 compile
cu show u-001
```

预期：

- `JUNK.txt` 不再存在（被 **`post-conversation`** 快照恢复覆盖）。
- `compile.attempt == 2`，`build.attempt == 2`。
- `bootstrap.attempt == 1`，`conversation.attempt == 1`（未被重跑条目递增规则影响时保持 1）。

---

## 5. P2 REST API

启动服务：

```bash
cu serve --port 8765 &
SERVE_PID=$!
sleep 2
```

5.1 列表（应至少含 `u-001`，若本节前曾删库则可能没有）：

```bash
curl -s http://127.0.0.1:8765/api/v1/jobs | jq '.[].job_id'
```

5.2 新建 + 触发 + 等待：

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"zip_url":"https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip","job_id":"api-001"}' | jq

curl -s -X POST http://127.0.0.1:8765/api/v1/jobs/api-001/stages/bootstrap/run
```

**等待四阶段：** 取决于会话是否仍在跑真实 Claude（可另开终端对 **api-001** 调 `cu show`，或拉长 `sleep`）。使用 **`CU_TEST_MODE=1`** 时，`cu serve` 进程必须通过 **同一 shell export / systemd `Environment=`** 继承该变量，否则子进程仍会尝试真实 `claude`。）

```bash
# 粗略等待后拉详情（时间请按环境调整）
sleep 300
curl -s http://127.0.0.1:8765/api/v1/jobs/api-001 | jq '.status, [.stages[] | {stage, status}]'
```

预期：

- **`status: "success"`**
- 四个 **`stage`** 均为 **`success`**

5.3 重跑 API：

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs/api-001/stages/build/rerun
sleep 60
curl -s http://127.0.0.1:8765/api/v1/jobs/api-001 | jq '.stages[] | select(.stage=="build") | {status, attempt}'
```

预期：`build` 再次 **`success`**，`attempt == 2`。

5.4 **取消**：在长跑阶段另开终端执行 **`cu cancel <job_id>`**；或后续 P3 前再补自动化。

收尾：

```bash
kill $SERVE_PID
```

---

## 6. 删除

```bash
cu delete api-001
ls "$HOME/.code-understand/jobs/"   # 应不再含 api-001（路径随 CU_DATA_ROOT）
sqlite3 "$HOME/.code-understand/db.sqlite" "SELECT job_id FROM jobs;"
sqlite3 "$HOME/.code-understand/db.sqlite" "SELECT COUNT(*) FROM stage_runs WHERE job_id='api-001';"   # 应为 0
```

---

## 7. 并行

```bash
unset CU_TEST_MODE    # 或保持 export CU_TEST_MODE=1，使两作业均走 Claude mock（仍跑完整编排）

cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id par-1 &
cu run https://github.com/sherlock-project/sherlock/archive/refs/heads/main.zip --job-id par-2 &
wait
cu list
```

预期：

- 两个作业互不干扰，各自独立沙箱、独立 DB 行。
- 未失败时两条记录终态均可为 **`success`**。
- 各自的 **`home/`** 与 **`snapshots/`** 与对方隔离。

**说明：** 未设 **`CU_TEST_MODE`** 或设为 **`0`** 时本节耗时会显著增加；可改用其它小仓库或在开发机先 **`export CU_TEST_MODE=1`** 做「编排并行」验证。

---

## 8. 失败回归（人为破坏验证错误处理）

```bash
git stash push -m "p2-fail-smoke" -- cu/runtime/download.py
cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id fail-001
cu show fail-001
git stash pop
```

预期：

- 终态 **`failed`**，**`bootstrap`** 阶段 **`failed`**，**`conversation` / `compile` / `build`** 仍为 **`pending`**。
- **`log_tail`** 含 **`bootstrap`** / **`download`** 相关信息。

---

## 9. 数据库迁移健壮性（重启服务）

```bash
cu serve --port 8765 &
SERVE_PID=$!
sleep 2
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"zip_url":"https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip","job_id":"restart-001"}'
kill $SERVE_PID
sleep 2

cu serve --port 8765 &
SERVE_PID=$!
sleep 2
curl -s http://127.0.0.1:8765/api/v1/jobs/restart-001 | jq '.status'   # "pending"
kill $SERVE_PID
```

预期：作业记录在 SQLite 中持久化；服务重启后通过 API 仍可读出。

**已知限制（P2）：** 运行中作业**不会**因进程重启自动续跑；用户需再次 **`cu run`** 或 **`POST .../stages/.../run`**。

---

## 完成标准

- 按你选择的范围：**§1–§2** 为最低基线；**§3 真实 loop** 与 **§5–§7** 视环境是否具备 **`claude` / 网络** 与可接受时长选做。
- 使用 **`CU_TEST_MODE=1`**，或单独设置 **`LOOP_USE_MOCK_CLAUDE` / `BUILD_USE_MOCK_CLAUDE` / `CLASSIFY_USE_MOCK_CLAUDE`** 时，须在记录中注明「非生产等价验证」。
- 无未预期的 stack trace（**§8** 人为失败除外）。
- **`cu list`** 时间戳为 ISO8601（UTC）；数据根与仓库目录分离。

完成后可在本仓库开 issue、在 spec/plan 末尾打勾，或继续 **P3 Web UI**：见 **`docs/superpowers/plans/2026-05-13-p3-web-ui-sse-rewrite.md`**。

# P2 · Ubuntu 真机验证清单

> 在 Ubuntu 上从 git 拉取最新 `mindflow` 后逐项执行。
> 所有命令默认在仓库根目录运行。生产环境路径 `~/.code-understand/` 不能位于仓库内。

---

## 0. 准备

```bash
cd ~ && git clone <repo> CodeUnderstand && cd CodeUnderstand
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
# 验证外部依赖
which claude jq wget unzip zip curl gedit
```

预期：以上命令都有结果；如 `gedit` 缺失，rewrite 交互暂不影响 P2 验证（rewrite 已延后到 P3）。

---

## 1. 单元 + 集成测试

```bash
python -m pytest tests/ -v -m "not slow"
```

预期：**64 passed, 1 deselected**（与 Windows 开发机基线一致；如有新增以测试输出为准）。

---

## 2. P1 e2e 冒烟（bootstrap 真跑）

```bash
python -m pytest tests/test_e2e_smoke.py -v -m slow
```

预期：1 passed；磁盘上出现：

```
~/.code-understand/jobs/smoke-test/home/code-understand-nocode/code/nocode/README.md
~/.code-understand/jobs/smoke-test/home/code-understand-nocode/code/nocode/.git/
```

---

## 3. P2 CLI 端到端（4 阶段全跑）

```bash
cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id u-001
```

预期：
- stdout 实时打印 `[bootstrap]/[conversation]/[compile]/[build]` 进度。
- 终态 `success`。
- 沙箱产出：

```bash
ls ~/.code-understand/jobs/u-001/
# home/  snapshots/
ls ~/.code-understand/jobs/u-001/snapshots/
# post-bootstrap.tar  post-conversation.tar  post-compile.tar
ls ~/.code-understand/jobs/u-001/home/
# code-understand-nocode/  code-understand-nocode.zip  .claude/  logs/  loop_logs/  tmp/
```

```bash
cu show u-001
```

预期：4 阶段均 `success`，attempt 都为 1。

---

## 4. P2 重跑（恢复快照）

```bash
# 故意污染 home/，再 rerun compile
echo "junk" > ~/.code-understand/jobs/u-001/home/JUNK.txt
cu rerun u-001 compile
cu show u-001
```

预期：
- `JUNK.txt` 不再存在（被 `post-conversation.tar` 覆盖恢复）。
- `compile.attempt == 2`，`build.attempt == 2`。
- `bootstrap.attempt == 1`，`conversation.attempt == 1`（未被波及）。

---

## 5. P2 REST API

启动服务：

```bash
cu serve --port 8765 &
SERVE_PID=$!
sleep 2
```

5.1 列表（应至少含 u-001）：

```bash
curl -s http://127.0.0.1:8765/api/v1/jobs | jq '.[].job_id'
```

5.2 新建 + 触发 + 等待：

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"zip_url":"https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip","job_id":"api-001"}' | jq

curl -s -X POST http://127.0.0.1:8765/api/v1/jobs/api-001/stages/bootstrap/run
# 等待 4 阶段完成（nocode 仓库 ~3-5 分钟）
sleep 300
curl -s http://127.0.0.1:8765/api/v1/jobs/api-001 | jq '.status, [.stages[] | {stage, status}]'
```

预期：
- `status: "success"`
- 4 个 stage 都是 `success`

5.3 重跑 API：

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs/api-001/stages/build/rerun
sleep 60
curl -s http://127.0.0.1:8765/api/v1/jobs/api-001 | jq '.stages[] | select(.stage=="build") | {status, attempt}'
```

预期：`build` 再次 `success`，`attempt == 2`。

5.4 取消（构造一个会自然完成的快作业不便演示；可在 conversation 长跑时另开终端 `cu cancel <job>` 验证）。

收尾：

```bash
kill $SERVE_PID
```

---

## 6. 删除

```bash
cu delete api-001
ls ~/.code-understand/jobs/  # 应不再含 api-001
sqlite3 ~/.code-understand/db.sqlite "SELECT job_id FROM jobs;"   # 应只剩 u-001
sqlite3 ~/.code-understand/db.sqlite "SELECT COUNT(*) FROM stage_runs WHERE job_id='api-001';"   # 应为 0
```

---

## 7. 并行

```bash
cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id par-1 &
cu run https://github.com/sherlock-project/sherlock/archive/refs/heads/master.zip --job-id par-2 &
wait
cu list
```

预期：
- 两个作业互不干扰，各自独立沙箱、独立 DB 行。
- 两条记录终态都是 `success`。
- 各自的 `home/` 与 `snapshots/` 与对方完全隔离。

---

## 8. 失败回归（人为破坏验证错误处理）

```bash
# 删除 download.sh 触发 bootstrap 失败
git stash push -- download.sh
cu run https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip --job-id fail-001
cu show fail-001
git stash pop
```

预期：
- 终态 `failed`，`bootstrap` 阶段 `failed`，`conversation/compile/build` 仍 `pending`。
- `log_tail` 含 `bootstrap`/`download`。

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

# 再启动一次，确认作业仍可见
cu serve --port 8765 &
SERVE_PID=$!
sleep 2
curl -s http://127.0.0.1:8765/api/v1/jobs/restart-001 | jq '.status'   # "pending"
kill $SERVE_PID
```

预期：作业记录在 SQLite 中持久化；服务重启后通过 API 仍可读出。
（运行中作业不会自动续跑——线程不持久化，是 P2 已知限制；用户需用 `run` 或 `/run` 重新触发。）

---

## 完成标准

- 所有上述 9 节均通过。
- 无 stack trace 出现在 stdout/stderr（除 8 节人为构造的失败）。
- `cu list` 输出可读，时间戳为 ISO8601 UTC。
- `~/.code-understand/` 不与仓库目录混在一起。

完成后在本仓库提一个 issue 或在 spec 末尾打勾。

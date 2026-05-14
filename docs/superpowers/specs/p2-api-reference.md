# P2 · 编排器 API 速查表

> 实现 commit 链：见 `mindflow` 分支 `5ae8a8d` 以后的 14 个提交。
> 适用版本：`code-understand 0.3.0`。

---

## P3 · Web UI（补充端点）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/jobs/{job_id}/events` | SSE（`text/event-stream`）；首帧 comment；事件 `data:` JSON `ts`,`stage`,`message` |
| `GET` | `/api/v1/jobs/{job_id}/rewrite/questions` | 即时解析会话 JSONL 的题目列表 `{ "lines": [...] }`（需 `compile` success）|
| `PUT` | `/api/v1/jobs/{job_id}/rewrite/questions` | `{"lines":[...]}` 直接写回会话（同上）|
| `GET` | `/api/v1/jobs/{job_id}/artifacts/zip` | 返回 `zip` 文件下载（不存在则 404）|
| `GET` | `/api/v1/meta` | `{ version, web_ui }` |

- **触发 `POST .../stages/build/run` / `rerun`**（经由当前进程 API）且 **stage=`build`** 时，服务端会在 **export+zip** 前自动跑一次 **`cu.pipeline.rewrite` 的 stdin-lines 写回**（stdin 投喂当前会话题目，常为幂等等同写回）。**CLI `cu run` 不受影响**。
- **`GET/`**：若构建了 `web/dist/index.html`，`cu serve` 同时托管静态 UI（SPA Hash 路由 `#/`）。

## CLI 子命令

```bash
cu run <zip_url> [--job-id ID] [--start STAGE]
cu list [--status STATUS]
cu show <job_id>
cu rerun <job_id> <stage>
cu cancel <job_id>
cu delete <job_id>
cu serve [--host HOST] [--port PORT]
```

`<stage>` 取值：`bootstrap | conversation | compile | build`

`run` 会自动创建作业并启动 `--start`（缺省 `bootstrap`），阻塞直到结束。
进度通过回调实时输出到 stdout，格式 `[stage] message`。

---

## REST API (`/api/v1`)

| 方法 | 路径 | 说明 | 失败码 |
|---|---|---|---|
| `POST` | `/jobs` | 创建作业（仅 pending，不自动启动） | 400 非 GitHub archive ZIP URL |
| `GET` | `/jobs[?status=...]` | 列表，可按 status 过滤 | - |
| `GET` | `/jobs/{job_id}` | 详情（含 4 阶段记录） | 404 |
| `POST` | `/jobs/{job_id}/stages/{stage}/run` | 触发某阶段执行 | 404 job not found / 409 依赖未满足 / 400 stage 无效 |
| `POST` | `/jobs/{job_id}/stages/{stage}/rerun` | 从某阶段重跑（恢复 post-prev 快照） | 同上 + 409 missing snapshot |
| `POST` | `/jobs/{job_id}/cancel` | 取消运行中作业（SIGTERM 当前子进程） | 404 无运行中 |
| `DELETE` | `/jobs/{job_id}` | 删除作业（取消 + 清沙箱与快照 + DB CASCADE） | 404 |

P3 **新增**的路径与语义见本节上方 **「P3 · Web UI（补充端点）」**；`JobDTO` 含 `artifact_zip_path`（若 zip 尚未生成则为空字符串）。

---

## 数据模型

### `JobDTO`

```json
{
  "job_id": "demo-repo-ab12cd34",
  "repo": "demo-repo",
  "zip_url": "https://github.com/u/demo-repo/archive/refs/heads/main.zip",
  "github_url": "https://github.com/u/demo-repo",
  "session_id": "deadbeef-...",
  "claude_project_dir": "/home/x/.code-understand/jobs/.../home/.claude/projects/encoded-cwd",
  "status": "running",
  "created_at": "2026-05-13T08:00:00+00:00",
  "updated_at": "2026-05-13T08:05:12+00:00",
  "notes": "",
  "stages": [ /* 4 个 StageDTO，按 JOB_STAGES 顺序 */ ]
}
```

### `StageDTO`

```json
{
  "stage": "bootstrap",
  "status": "success",
  "started_at": "2026-05-13T08:00:00+00:00",
  "ended_at":   "2026-05-13T08:00:42+00:00",
  "exit_code":  null,
  "log_tail":   "",
  "attempt":    1
}
```

`status` 取值：`pending | running | success | failed | cancelled`。
`exit_code` 在 P2 阶段保留为 null；具体子进程退出码会在 P3 接入。

---

## 状态机

阶段序列：`bootstrap → conversation → compile → build`

可执行性规则：
- `bootstrap`：始终可执行。
- 其它阶段：要求所有前序为 `success`。
- 重跑同样要求所有前序为 `success`；不关心该阶段本身当前状态。

重跑副作用：
- 把该阶段及后续全部置 `pending`（attempt 仅在新一次运行成功/失败时 +1）。
- 非 `bootstrap` 重跑前先 `extractall` 还原 `home/`（用 `post-<prev>.tar`）。
- `bootstrap` 重跑时，删除 `home/` 与 `snapshots/`，再从 0 开始（无前置快照）。

作业整体状态：
- 任一阶段 running → job running。
- 任一阶段 failed → job failed。
- 用户取消 → job cancelled。
- `build` 阶段成功 → job success。

---

## 快照

| 项 | 值 |
|---|---|
| 位置 | `<CU_DATA_ROOT>/jobs/<job_id>/snapshots/post-<stage>.tar` |
| 格式 | 无压缩 tar；包含整棵 `<job_id>/home/`（arcname=`home`） |
| 何时拍 | `bootstrap`、`conversation`、`compile` 三个阶段成功后；`build` 后不拍 |
| 恢复 | `rmtree(home)` + `extractall(<job_dir>, filter='data')` |

---

## 并发与取消

- 一进程一 Orchestrator，进程内 `_ACTIVE` 字典登记每个运行中作业。
- 一作业一守护线程（`threading.Thread(daemon=True)`）。
- 同一作业不可并行启动（`RuntimeError: job <id> 已在运行`）。
- 不同作业完全独立（隔离沙箱、独立数据库行、独立线程），并行无相互影响。
- `cancel`：`Event.set()` + `os.kill(pid, SIGTERM)`；线程在两个阶段之间检查 flag。
  - Linux 行为正确；Windows 上 SIGTERM 语义有限，建议生产用 Ubuntu。

---

## 运行时数据布局

```
$CU_DATA_ROOT/                       # 默认 ~/.code-understand
├── db.sqlite                        # 作业 + 阶段记录
└── jobs/
    └── <job_id>/
        ├── home/                    # 假 $HOME；P1 沙箱
        │   ├── .claude/             # Claude 配置（projects/ 不复制）
        │   ├── code-understand-<repo>/
        │   │   ├── code/<repo>/
        │   │   ├── docs/
        │   │   ├── sessions/
        │   │   ├── metadata.json
        │   │   └── questions.json
        │   ├── code-understand-<repo>.zip
        │   ├── logs/
        │   ├── loop_logs/
        │   └── tmp/
        └── snapshots/
            ├── post-bootstrap.tar
            ├── post-conversation.tar
            └── post-compile.tar
```

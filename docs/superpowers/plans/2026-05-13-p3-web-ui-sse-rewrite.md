# P3 · Web UI + SSE + Rewrite 多轮 — 实现计划

> **前置**：P2 编排器、CLI、REST API、快照/重跑已完成；P2 全流程真机验证可按 `p2-ubuntu-verification-checklist.md` **选做**或使用 `CU_SKIP_LOOP` 加速。**本计划不要求 P2「真实 loop」全绿后再开工。**
>
> **设计依据**：`docs/superpowers/specs/2026-05-13-code-quality-workflow-design.md`（§5 rewrite、§7 UI/API）。
>
> **For agent workers**：按任务顺序 TDD；每任务独立 commit；大段实现前自检与 spec 对齐。

---

## Goal

单机 **同一进程**（`cu serve` / uvicorn）同时提供：

1. **静态 Web UI**：作业列表、详情（4 宏阶段 + 时间线）、触发 run/rerun/cancel/delete、新建作业（ZIP URL）。
2. **SSE**：`GET /api/v1/jobs/{job_id}/events`，实时推送阶段进度与步骤事件（与 CLI 的 `on_event` 语义对齐）。
3. **Rewrite 多轮**：在宏阶段 **`build` 内**先 **rewrite**（只改沙箱内单份 session JSONL），支持多轮编辑后再 **export_session + zip**；与 `rewrite.py` 的 `--single-source` / `--non-interactive` 对接。
4. **产物**：详情页展示 zip 路径或提供 **只读下载**（小文件 `FileResponse` 或 `X-Accel-Redirect` 留作后续）。

**非目标（P3）**：真实 dispatch、认证/多租户（可预留 `CU_API_TOKEN` 可选中间件）、移动端适配。

---

## 技术选型（默认可改）

| 层面 | 选择 | 说明 |
|------|------|------|
| 后端 | 现有 FastAPI | 增补路由、SSE、`StaticFiles` |
| SSE 桥接 | `asyncio.Queue` + 后台线程向 queue 投递 | orchestrator 在 **POSIX 线程**中跑，`StreamingResponse` 在事件循环中用 `wait_for(queue.get)` 或 `anyio.from_thread` 投递 |
| 事件源 | **`JobEventHub`**（模块级单例） | `publish(job_id, event)`；SSE 订阅者 per-job 建立一个 queue，hub fan-out 或按需过滤 |
| 前端 | **Vite + TypeScript + 原生 Fetch + EventSource** | 体量可控；打包输出 `web/dist`，由 FastAPI `mount("/", StaticFiles(directory=..., html=True))`，API 仍为 `/api/v1` |
| Rewrite 持久化临时题面 | `$HOME/tmp/`（沙箱已有）或 `jobs/<id>/rewrite/`（平台侧） | 与 `rewrite.py` 非交互模式读写的临时文件路径约定死在 `cu/paths.py` 或 env |

---

## 与当前代码的差异点

| 现状 | P3 目标 |
|------|---------|
| `cu/stages.py` `run_build` **跳过** `rewrite.py` | 接入 rewrite：**非交互**：UI 写好题目文本 → 写入 tmp → `run_python(rewrite.py, --single-source --non-interactive ...)` → 可多轮直至用户点「提交并导出」 |
| `orch.run_stage` 未把 `on_event` 记入 hub | `_run_thread` 把 `orch` 传来的 `on_event` **包装**：除 `ctx.fire` 外 **`hub.publish(job_id, {...})`** |
| API 无 SSE / 无 rewrite | 新增端点（见下文） |

---

## API 形状（草案）

路径前缀保持不变 **`/api/v1`**。

### SSE

- **`GET /api/v1/jobs/{job_id}/events`**
  - `Accept: text/event-stream`
  - 事件：`data: {"ts":"...", "stage":"conversation", "message":"step:loop"}\n\n`
  - 可选：首包 `retry:`、`id:` 占位；断开重连：**全量状态**仍以 `GET /jobs/{id}` 为准（SSE 仅增量）。

### Rewrite（前置：`session_id`、`claude_project_dir` 已就绪，且 **`compile` success**）

- **`GET /api/v1/jobs/{job_id}/rewrite/questions`**
  - 返回当前从 session 抽取的题目列表 JSON（只读草稿；不负责写盘）。
  - 实现可读 **已有 tmp** 或临时调 `rewrite.py` 导出逻辑拆解 — **优先**：复用小函数从 JSONL dump 题目（避免再启 Python 过重时可内联简化版解析，与 `rewrite.py` 行为一致待定）。

- **`PUT /api/v1/jobs/{job_id}/rewrite/draft`**（或 `PATCH`）
  - Body：`{ "lines": ["Q1...", "Q2..."] }` 写入编排器约定的 **`tmp`/题目文件**，供下一轮 `rewrite.py --non-interactive` 读取。

- **`POST /api/v1/jobs/{job_id}/rewrite/apply`**
  - 执行一轮：`rewrite.py ...` 读上述 tmp，写回 **单份** session；返回 `{ "ok": true, "log_tail": "..." }`；失败 409 + 明细。

- **`POST /api/v1/jobs/{job_id}/build/finish`**
  - **语义**：用户在 UI **确认 Rewrite 结束** → 服务端顺序 **`export_session.sh` → `zip.sh`**（与当前 **`run_build`** 中与 rewrite 无关的后半段一致）。命名可在项目内统一为 `complete-rewrite` 等别名路由，但 **只能保留一个canonical 路径**，避免混淆。

**与编排器 / CLI 对齐（P3 v1 敲定）：**

- **不改变** SQLite 宏阶段仍为 4 段；不设 `build_subphase`。
- **`cu run` 全链**：`build` **仅** `export_session` + zip，**不调** **`rewrite.py`**（与现行 P2 一致，兼容无人值守/CI）。
- **Web**：`compile` success 之后，rewrite 全部由 **`rewrite/*` API** 多轮驱动；用户在 UI 确认后调用 **`POST .../build/finish`** → export + zip。实现时 **`cu/stages.py`** 拆分出可复用的 **`run_export_zip(ctx)`**（及 **`run_rewrite_round(ctx)`**），供 **`run_build`**（CLI 路径）与 **API**（Web 路径）共用。
- **P4**：若需 CLI rewrite，另增 **`cu rewrite`**。

---

## `build` 与 Web 的职责划分（小结）

| 入口 | Rewrite | Export + Zip |
|------|---------|--------------|
| `cu run` 全流程 | 否 | 是 |
| Web `rewrite/*` + `build/finish` | 是（多轮） | 由 finish 触发 |

---

## UI 页面结构（草案）

1. **`/`**：作业表（job_id、repo、status、创建时间、持续时间）；按钮新建、跳转详情。
2. **`/jobs/{id}`**：四阶段阶梯 + 进度条（来自 REST + SSE）；按钮 Run/Rerun/Cancel/Delete（按依赖 enable）。
3. **`/jobs/{id}/rewrite`**（或详情页 Tab）：题目列表编辑器（textarea 或表单列表）；「应用一轮」「预览」「生成 zip」（调用 export）。

---

## 任务分解（编号实现顺序）

| ID | 任务 | 产出 |
|----|------|------|
| T1 | **`cu/events.py`**：`JobEventHub` + 单元测试 | `publish` / `subscribe(job_id)` 异步迭代器 |
| T2 | **orchestrator** 接线：`_run_thread` 包装 `on_event` → `hub.publish` | 不改变现有语义 |
| T3 | **FastAPI** `GET /jobs/{id}/events`：`StreamingResponse` | `test_api.py` 用 `TestClient.stream` / httpx async 冒烟 |
| T4 | **前端脚手架** `web/`：Vite+TS，`npm run build` → `dist/` | `package.json`、CI 可选用 `SKIP_WEB=1` |
| T5 | **FastAPI `StaticFiles`**：`mount("/", ...)`，`/api` 优先 | SPA `fallback index.html` |
| T6 | **列表页 + API 粘合**：读取 `GET /jobs` | 纯静态请求即可 |
| T7 | **详情页 + EventSource**：订阅 SSE，合并轮询兜底 | |
| T8 | **rewrite API + `run_python(rewrite.py)`**：路径与 env 与设计一致 | `tests/` mock 子进程 |
| T9 | **拆分 `run_build`** 导出段 + CLI 文档 | 最小化行为变化 |
| T10 | **验收清单** `docs/.../p3-ubuntu-verification-checklist.md` | |

---

## 自检（P3 MR 合并前）

- [ ] SSE 在 Chrome/Firefox 单作业运行时可连续收到事件，无内存泄漏（取消订阅断开 queue）。
- [ ] 两作业并行时 SSE 互不串台（`job_id` 过滤）。
- [ ] Rewrite 三轮修改后导出 zip，解压结构符合 §3.1 干净产物。
- [ ] `python -m pytest tests/ -m "not slow"` 全绿；`slow` 仍可选。
- [ ] **`p2-api-reference.md`** 追加 P3 端点或通过 **本计划 + 自动生成 OpenAPI `/docs`** 视为足够。

---

## 后续（P4 占位）

- dispatch stub → 真实平台；上传 zip；
- OAuth2 / Token；
- `/jobs/{id}/artifacts/zip` 大文件分块下载。

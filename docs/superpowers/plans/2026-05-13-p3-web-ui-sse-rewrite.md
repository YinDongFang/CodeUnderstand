# P3 · Web UI + SSE + Rewrite（单遍 build） — 实现计划

> **前置**：P2 编排器、CLI、REST API、快照/重跑已完成；P2 全流程真机验证可按 `p2-ubuntu-verification-checklist.md` **选做**或在本机 **`export CU_TEST_MODE=1`**（子进程 Claude 一律走 **`testing/mock_claude.sh`**）加速。**本计划不要求 P2「真实 loop」全绿后再开工。**
>
> **设计依据**：`docs/superpowers/specs/2026-05-13-code-quality-workflow-design.md`（§5 rewrite、§7 UI/API）。
>
> **For agent workers**：按任务顺序 TDD；每任务独立 commit；大段实现前自检与 spec 对齐。

---

## Goal

单机 **同一进程**（`cu serve` / uvicorn）同时提供：

1. **静态 Web UI**：作业列表、详情（4 宏阶段 + 时间线）、触发 run/rerun/cancel/delete、新建作业（ZIP URL）。
2. **SSE**：`GET /api/v1/jobs/{job_id}/events`，实时推送阶段进度与步骤事件（与 CLI 的 `on_event` 语义对齐）。
3. **Rewrite + 导出**：宏阶段 **`build` 单次执行**为 **rewrite → export_session → zip**（只维护沙箱内**单份** session JSONL 真源）。**不在**编排器/P3 Web 路径为题目维护 **`tmp` 草稿文件**：列表页按需**即时解析**会话；用户在 UI 的修改 **直接写回**该 JSONL（或等价地由服务端调用 `rewrite.py` 仅从内存/stdin 入参写入，不落临时题面）。若需对已导出结果再做人工修正，用户应 **`rerun build`** 整张宏阶段重来，**不支持**在同一 `build` 运行会话内做多轮 rewrite/apply。
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
| Rewrite 题面存储 | **无独立 tmp 题面**：真源仅为沙箱 `.claude/projects/.../<session>.jsonl`；`GET` 时即时解析，`PUT`/保存时直接写回 | `rewrite.py` 若仍需 CLI 兼容，可走 stdin/`--questions-json` 等**无磁盘草稿**契约（具体 flag 实现时敲定），**不设** `$HOME/tmp/` 题库文件 |

---

## 与当前代码的差异点

| 现状 | P3 目标 |
|------|---------|
| `cu/stages.py` `run_build` **跳过** `rewrite.py` | **手动触发 `build` 阶段**时：`run_rewrite`（内存/直接写会话，**无 tmp 题面**）→ **`run_export_zip`**；整段单次执行。**再改题目** → 用户 **`rerun build`**，非阶段内循环 |
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

### Rewrite / 会话编辑（前置：`session_id`、`claude_project_dir` 已就绪，且 **`compile` success**）

- **`GET /api/v1/jobs/{job_id}/rewrite/questions`**
  - 返回从 **当前会话 JSONL** **即时解析**的题目列表 JSON（不落盘草稿、不依赖 `tmp`）。
  - 实现：复用与 `rewrite.py` 一致的抽取逻辑（Python 模块或薄封装），**每次请求重新读会话文件**。

- **`PUT /api/v1/jobs/{job_id}/rewrite/questions`**（或语义等价的 `PATCH`）
  - Body：`{ "lines": ["Q1...", "Q2..."] }`。服务端将题目 **直接写回**该作业的 **单份** session JSONL（校验失败返回 409）。
  - **禁止**写入 `$HOME/tmp/` 或单独的 draft 文件作为中间真源。

- **不再拆分**「多轮 **`POST .../rewrite/apply`** + 最后 **`build/finish`**」：导出与打包由 **`POST .../stages/build/run`**（或 **`rerun`**）驱动的 **`run_build`** **一次跑完**。UI 在用户点击「保存题目」时已写回会话；点击「运行 build」仅执行 **`rewrite.py`（若 CLI 仍存在且需要幂等规整）→ `export_session` → `zip`** 的线性序列，中间**无**额外「第 N 轮 apply」API。

- ~~`POST .../build/finish`~~：**删除独立 finish 语义**，避免与「整阶段 `build`」双轨混淆；等价能力 = **`POST /jobs/{id}/stages/build/run`**。

**与编排器 / CLI 对齐（P3 v1 敲定）：**

- **不改变** SQLite 宏阶段仍为 4 段；不设 `build_subphase`。
- **`cu run` 全链**：`build` **仅** `export_session` + zip，**不调** **`rewrite.py`**（与现行 P2 一致，兼容无人值守/CI）。
- **Web**：`compile` success 之后，**读写题目**走 **`GET/PUT .../rewrite/questions`**（会话真源仅此一份）；**执行交付**统一走 **`POST .../stages/build/run`**（或 **`rerun build`**），内部 **`run_build`** = **单次** rewrite 规整（如需要）+ export + zip。**再次人工改题并重新出包** = 先 **`PUT` 会话**（或仅用 UI 保存），再 **`rerun build`**，**不设**同一 build 会话内的多轮 apply。实现时 **`cu/stages.py`** 拆分 **`run_rewrite_into_session`**（可无操作若题目已在 UI 写回）与 **`run_export_zip(ctx)`**，由 **`run_build`** 串联调用。
- **P4**：若需 CLI rewrite，另增 **`cu rewrite`**。

---

## `build` 与 Web 的职责划分（小结）

| 入口 | 题目编辑 | Export + Zip |
|------|-----------|--------------|
| `cu run` 全流程 | — | `build` 内仅 export + zip |
| Web `GET/PUT .../rewrite/questions` + **`stages/build/run`** | UI 即时读会话 / 直接写回 JSONL | 单次 **`build`** 内顺序执行 |
| 需对已导出包再改版 | **`PUT`** 更正后再 **`rerun build`** | 整阶段重跑（非嵌套子轮次） |

---

## UI 页面结构（草案）

1. **`/`**：作业表（job_id、repo、status、创建时间、持续时间）；按钮新建、跳转详情。
2. **`/jobs/{id}`**：四阶段阶梯 + 进度条（来自 REST + SSE）；按钮 Run/Rerun/Cancel/Delete（按依赖 enable）。
3. **`/jobs/{id}/rewrite`**（或详情页 Tab）：题目列表编辑器；**保存**即 `PUT` 写回会话；**生成 zip** 与详情页「运行 build」一致，调用 **`POST .../stages/build/run`**（无单独「多轮应用」按钮）。

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
| T8 | **`GET/PUT .../rewrite/questions`** + 即时解析/直写 JSONL；**`run_build`** 串联 rewrite（无 tmp）+ export + zip | `tests/` mock 子进程 |
| T9 | **拆分 `run_build`**（`run_export_zip` 等）+ 更新 CLI/REST 文档 | 最小化对 `cu run` 行为变化 |
| T10 | **验收清单** `docs/.../p3-ubuntu-verification-checklist.md` | |

---

## 自检（P3 MR 合并前）

- [ ] SSE 在 Chrome/Firefox 单作业运行时可连续收到事件，无内存泄漏（取消订阅断开 queue）。
- [ ] 两作业并行时 SSE 互不串台（`job_id` 过滤）。
- [ ] UI 修改题目 → 写回会话 → **单次** `build` 导出 zip；若再改题目并出包，依赖 **`rerun build`**；解压结构符合 §3.1 干净产物。
- [ ] `python -m pytest tests/ -m "not slow"` 全绿；`slow` 仍可选。
- [ ] **`p2-api-reference.md`** 追加 P3 端点或通过 **本计划 + 自动生成 OpenAPI `/docs`** 视为足够。

---

## 后续（P4 占位）

- dispatch stub → 真实平台；上传 zip；
- OAuth2 / Token；
- `/jobs/{id}/artifacts/zip` 大文件分块下载。

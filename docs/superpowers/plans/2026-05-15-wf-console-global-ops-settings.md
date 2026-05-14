# Global ops settings (cookie / authorization) — Implementation Plan

> **For agentic workers:** This plan has been **executed**. Steps below are retained as an audit trail (`- [x]`).

**交付状态（2026-05-15）：** 已全部完成。实现合并于 `3786458` / `7695563` 及后续提交；以 `docs/superpowers/specs/2026-05-15-wf-console-global-ops-settings-design.md` **最新版**为准（统一 `console_settings`、`GET/PUT /settings`、`NodeContext.settings`）。

**Goal:** Persist `cookie` and `authorization` in SQLite `settings` table; expose `GET/PUT /settings/ops`; inject `ops_globals` into `NodeContext` on each `run_once` entry; add Web settings modal.

**Architecture:** `SqliteStore` owns `settings` rows; dedicated small `routes_settings` router; `run_once` calls `store.get_ops_globals()` once per invocation; `NodeContext` gains required `ops_globals` dict (normalized keys).

**Tech Stack:** Python 3.12, SQLite, FastAPI, Pydantic, pytest, React + TypeScript.

**Spec:** `docs/superpowers/specs/2026-05-15-wf-console-global-ops-settings-design.md`

---

## File map

| File | Change |
|------|--------|
| `wf_engine/store/sqlite.py` | `CREATE TABLE settings`; `get_ops_globals` / `set_ops_globals` |
| `wf_engine/server/routes_settings.py` | **Create:** router `GET/PUT /settings/ops` |
| `wf_engine/server/app.py` | `include_router(routes_settings.router)` |
| `wf_engine/context.py` | `ops_globals: dict[str, str]` on `NodeContext` |
| `wf_engine/runner.py` | Load ops at start of `run_once`; pass into `NodeContext` |
| `tests/wf_engine/test_sqlite_store.py` | Store tests for ops globals |
| `tests/wf_engine/test_settings_api.py` | **Create:** ASGI tests for settings API |
| `tests/wf_engine/test_runner_context.py` (or new) | Assert `ctx.ops_globals` visible in node |
| `web/src/api.ts` | `fetchOpsSettings`, `saveOpsSettings` |
| `web/src/App.tsx` | Settings button, modal, form state |
| `web/src/App.css` | Modal/textarea tweaks if needed |

---

### Task 1: SQLite `settings` + store API

**Files:** `wf_engine/store/sqlite.py`, `tests/wf_engine/test_sqlite_store.py`

- [x] **Step 1:** In `init_schema`, after tasks migration, `CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)`.

- [x] **Step 2:** Add constants `OPS_GLOBALS_KEY = "ops_globals"` and helpers:
  - `get_ops_globals(self) -> dict[str, str]`: read row; parse JSON; return `{"cookie": str(row.get("cookie","")), "authorization": str(...)}`; missing row → both `""`.
  - `set_ops_globals(self, cookie: str, authorization: str) -> None`: `UPSERT` or replace insert with `_dumps({"cookie": cookie, "authorization": authorization})`.

- [x] **Step 3:** Tests: empty DB returns empty strings; set then get roundtrip; invalid JSON in DB (manual corrupt) yields safe fallback or replace on next set (document choice—prefer read empty strings if parse fails).

- [x] **Step 4:** Run `uv run pytest tests/wf_engine/test_sqlite_store.py -q`

- [x] **Step 5:** Commit `feat(wf_engine): settings table and ops_globals store` — _实际与后续任务一并并入 `3786458`。_

---

### Task 2: FastAPI `GET/PUT /settings/ops`

**Files:** `wf_engine/server/routes_settings.py` (new), `wf_engine/server/app.py`, `tests/wf_engine/test_settings_api.py` (new)

- [x] **Step 1:** Pydantic model `OpsGlobalsBody` with `cookie: str`, `authorization: str`.

- [x] **Step 2:** `GET /settings/ops` → `cp.store.get_ops_globals()` as JSON.

- [x] **Step 3:** `PUT /settings/ops` body validated → `set_ops_globals`; return `200` + same shape body.

- [x] **Step 4:** `create_app` includes router (prefix none, paths full `/settings/ops`).

- [x] **Step 5:** ASGI tests with temp db + app: GET default empty; PUT values; GET matches.

- [x] **Step 6:** `uv run pytest tests/wf_engine/test_settings_api.py -v`

- [x] **Step 7:** Commit `feat(wf_engine): settings ops HTTP API` — _并入 `3786458`。_

---

### Task 3: `NodeContext` + `run_once`

**Files:** `wf_engine/context.py`, `wf_engine/runner.py`, one test file extending runner context

- [x] **Step 1:** Add to dataclass: `ops_globals: dict[str, Any]` or `dict[str, str]` (use `dict[str, str]` per spec).

- [x] **Step 2:** At start of `run_once` (after early returns for `WAITING_HUMAN`? **No** — spec says every entry; but if status is WAITING_HUMAN we return immediately before loop—so no NodeContext. For running path: after layout mkdirs / before node loop), `ops_snapshot = store.get_ops_globals()`.

- [x] **Step 3:** Each `NodeContext(..., ops_globals=ops_snapshot)` — same object reused per invocation OK (immutable intent).

- [x] **Step 4:** Test: `test_runner_context.py` or new: register workflow whose node asserts `ctx.ops_globals["cookie"] == "a"` after `store.set_ops_globals` equivalent via store API.

- [x] **Step 5:** `uv run pytest tests/wf_engine/ -q`

- [x] **Step 6:** Commit `feat(wf_engine): inject ops_globals into NodeContext per run_once` — _并入 `3786458`。_

---

### Task 4: Web UI

**Files:** `web/src/api.ts`, `web/src/App.tsx`, optional `App.css`

- [x] **Step 1:** Types `export type OpsGlobals = { cookie: string; authorization: string }` and `fetchOpsSettings()`, `saveOpsSettings(body: OpsGlobals)`.

- [x] **Step 2:** Left header: add button 「设置」 next to 新建任务; `settingsModalOpen` state.

- [x] **Step 3:** Modal: two `textarea`s (or auth as password + toggle); load on open; Save → PUT; error line; 关闭. — _实现为双 `textarea`，未做密码态切换。_

- [x] **Step 4:** `npm run build`

- [x] **Step 5:** Commit `feat(web): global ops settings modal` — _并入 `3786458`。_

---

### Task 5: Spec link + suite

- [x] **Step 1:** Edit spec `2026-05-15-wf-console-global-ops-settings-design.md`: set **实现计划** to `docs/superpowers/plans/2026-05-15-wf-console-global-ops-settings.md`; remove duplicate “下一步” ambiguity. — _已在 `32e7d25` 完成。_

- [x] **Step 2:** `uv run pytest tests/wf_engine/ -q` && `npm run build` (from `web/`) — _于 `3786458` 前验证通过。_

- [x] **Step 3:** Commit `docs: link global ops settings plan` — _对应 `32e7d25`（与代码提交分离）。_

---

## Self-review

| Spec § | Task |
|--------|------|
| §2 settings | Task 1 |
| §3 API | Task 2 |
| §4 NodeContext / run_once | Task 3 |
| §5 UI | Task 4 |

No TBD; `ops_globals` naming matches spec.

---

**本文件：** 计划已执行完毕；新工作请开新 plan 或在本 spec 上改版后再拆任务。

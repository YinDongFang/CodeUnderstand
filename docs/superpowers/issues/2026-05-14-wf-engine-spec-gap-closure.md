# wf_engine 规范缺口收口 — Issue 索引

设计规格：`docs/superpowers/specs/2026-05-14-workflow-orchestration-engine-design.md`  
审查记录：对话「整库对照 spec」结论。

| ID | 优先级 | 标题 | 状态 |
|----|--------|------|------|
| **OE-001** | P0 | §4 白名单缺件须硬失败（打包前校验 glob 至少命中一文件） | 已关闭 |
| **OE-002** | P0 | `rerun` 与活跃 worker 互斥（租约有效且进程存活则 409） | 已关闭 |
| **OE-003** | P0 | 租约过期 / worker 失联 → `stalled` + 节点 `worker_lost`；节点间刷新租约 | 已关闭 |
| **OE-004** | P1 | §3.5.2 `workdir_relative` 不得逃出 workspace | 已关闭 |
| **OE-005** | P1 | §4 软策略 B：多写文件打 warning 日志 | 已关闭 |
| **OE-006** | P2 | HTTP 错误体与 §3.5.7 对齐（顶层 `error`） | 已关闭 |
| **OE-007** | P2 | `rerun` 准备：`interrupt_seq` 归零；`failed` 任务允许 `rerun`（文档化） | 已关闭 |

提交请在 message 的 trailer 中加：`Refs: docs/superpowers/issues/2026-05-14-wf-engine-spec-gap-closure.md#oe-00x`

完成后将上表「状态」改为 **已关闭**。

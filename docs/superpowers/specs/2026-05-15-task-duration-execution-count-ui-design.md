# Task active duration, execution count, and English detail UI

**状态：** 定稿（2026-05-15）  
**前置：** `wf_engine` 控制面、interrupt、节点 rerun、`worker_generation` 既有语义；本规格**不**将 `worker_generation` 直接暴露为「执行次数」。  
**实现计划：** `docs/superpowers/plans/2026-05-15-task-duration-execution-count-ui.md`

---

## 1. 目标

1. **任务总用时（排除 interrupt）**：在 **列表** 与 **详情** 展示「活跃墙钟秒数」——即自创建起至「当前时刻或任务终态的 `updated_at`」之间，**扣除** 所有处于 `waiting_human` 的区间（含当前尚未 resolve 的半段）。
2. **任务执行次数**：创建并成功调度 **首次** worker 后基线为 **1**；每出现一次 **新的 worker 执行轮次** 再 **+1**：具体在 **`prepare_task_for_rerun_execution`（POST `/tasks/{id}/rerun`）** 与 **`apply_resolve`（interrupt resolve）** 各 **+1**（与「resolve 后再次拉起 worker」语义一致）。
3. **详情页文案**：右侧 **详情列** 内 **标题与标签一律英文**（`h2`/`h3`、`meta` 的 `dt`、区块标题、该区域按钮与 `title`）。左侧任务列表顶栏、新建任务 **模态框** 维持 **中文**（除非后续全站英文化需求）。

---

## 2. 数据模型（`tasks` 表）

| 列 | 类型 | 说明 |
|----|------|------|
| `execution_count` | `INTEGER NOT NULL DEFAULT 0` | 执行次数（见 §2.1）；新建任务在首次调度 worker 后置为 `1`。 |
| `interrupt_wall_seconds_accumulated` | `INTEGER NOT NULL DEFAULT 0` | 已 **完结** 的 `waiting_human` 区间墙钟秒数之和（仅在 `apply_resolve` 时累加）。 |
| `waiting_human_since` | `TEXT NULL` | 进入当前「待人」区间的 UTC ISO 起点；非 `waiting_human` 时为 `NULL`。 |

### 2.1 `execution_count` 更新规则

- **`create_task` 路径**：在现有逻辑完成「初始化任务目录 + 节点行 + 置为 running + spawn 首次 worker」之后，将 **`execution_count` 设为 `1`**（表示已计一次自动运行）。
- **`prepare_task_for_rerun_execution`**：`execution_count += 1`。
- **`apply_resolve`**：`execution_count += 1`。

不在 **`worker_generation` 自增的所有其它位置** 重复 +1，除非将来产品定义扩展。

### 2.2 Interrupt 时长累积

- **`open_interrupt`**（进入 `waiting_human`）：将 **`waiting_human_since`** 设为当前 UTC ISO（与 `updated_at` 同一时钟来源）。
- **`apply_resolve`**：在事务内  
  - `interrupt_wall_seconds_accumulated += max(0, seconds_between(waiting_human_since, now))`（`since` 缺失时按 `0` 追加，避免自损）；  
  - **`waiting_human_since := NULL`**；  
  - 然后执行既有 payload / status / `worker_generation` 更新。

**双次 interrupt 未 resolve：** 若在未 resolve 时再次进入 interrupt（异常或未来扩展），实现应保证不会静默丢段：至少 **在进入新的 `waiting_human` 前**，将上一段 `since` 累加进 `accumulated`（或等价地强制 resolve）。当前主线为单中断序列；若仅调用 `open_interrupt` 覆盖 `since`，需在实现计划中写清单测或明确定义为「不支持嵌套interrupt」。

---

## 3. 对外计算：`active_duration_seconds`

**输入：** `created_at`、`updated_at`、`status`、`interrupt_wall_seconds_accumulated`、`waiting_human_since`、服务端当前 UTC `now`。

**结束时刻 `end`**：

- 若 `status` 为终态（`succeeded` / `failed` / `stalled`）之一，则 `end = updated_at`（解析为 UTC）。
- 否则 `end = now`。

**未完结 interrupt 秒数 `open_intr`**：

- 若 `status == waiting_human` 且 `waiting_human_since` 非空：`open_intr = max(0, seconds_between(waiting_human_since, end))`。
- 否则 `open_intr = 0`。

**输出：**

```text
active_duration_seconds = max(0,
  seconds_between(created_at, end)
  - interrupt_wall_seconds_accumulated
  - open_intr)
```

**口径边界：** 仅排除 **`waiting_human`**；**不**剔除 worker 排队、租约间隙、调度延迟等其它空闲。

---

## 4. HTTP API

### 4.1 `GET /tasks`

每条摘要增加：

| 字段 | 类型 | 说明 |
|------|------|------|
| `execution_count` | `int` | 见 §2.1 |
| `active_duration_seconds` | `int` | 见 §3，按请求处理时刻计算 |

### 4.2 `GET /tasks/{id}`

详情 JSON 同样包含 **`execution_count`**、**`active_duration_seconds`**（与列表同一公式）。可选：增加只读 **`interrupt_wall_seconds_accumulated`** 供排障（非必须，YAGNI 可省略）。

---

## 5. Web UI

| 区域 | 内容 |
|------|------|
| 左侧列表行 | 展示 **Active duration**（或短标签 + 格式化时长）与 **Run count** / `execution_count`；版式由实现定（副行或 `title` 提示）。 |
| 右侧详情 meta | 增加 **`Execution count`**、**`Active duration`**（排除 interrupt）；**本节及详情内全部标题/标签英文**。 |
| 详情列 | 现有中文标题（如「创建时间」「节点」「日志」等）改为英文对应项。 |
| 模态框 / 左栏 | **保持中文**。 |

**展示格式：** 时长可采用 `Xm Ys` 或 `HH:MM:SS`；与现有时区展示策略一致（ISO 来自后端，前端 `toLocaleString` 可与时长格式化统一）。

---

## 6. 测试要点（实现计划落地）

- 迁移：`ALTER` 新列 + 存量 **`execution_count`** 回填策略（建议：存量无精确历史时设为 **至少 1** 若任务曾运行，否则 **0**——具体由实现选保守方案并文档化）。
- `create_task` 后 `execution_count == 1`；一次 **rerun**、一次 **resolve** 各 +1（与路径顺序无关的独立用例）。
- `waiting_human` 停留 N 秒后 resolve：`active_duration_seconds` 比粗算墙钟少约 N；终态任务用 `updated_at` 作 `end`。
- API 集成测试：列表与详情均含新字段。
- UI：详情标题英文快照或轻量 e2e（可选）。

---

## 7. 规格自检（2026-05-15）

- 无 **TBD** 占位；执行次数与 interrupt 扣除口径已单一释义。
- 与既有 **`worker_generation`** 解耦：本规格不修改其语义，仅并行维护 **`execution_count`**。
- 范围限于：**SQLite 迁移**、**store/routes**、**Web 详情英文化 + 列表摘要**；不扩展事件表。

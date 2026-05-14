# wf_engine 控制台全局 Ops 配置（cookie / authorization）

**状态：** 定稿（2026-05-15）  
**前置：** `wf_engine` SQLite 控制面、现有任务 `context_json`（每任务运行期上下文）、`NodeContext`、`run_once`。  
**下一步：** 审阅本 spec 后使用 `writing-plans` 产出实现计划。

---

## 1. 目标与边界

1. 在 Web 控制台提供 **全局配置** 两项：`cookie`、`authorization`（字符串；允许多行）。  
2. **与任务上下文分离**：**不**写入 `tasks.context_json`，**不**与 `input` 混用；语义为 **运维/控制台默认值**，**不是**浏览器访问 FastAPI 时的 HTTP Cookie / Authorization 头（文档与命名需避免误导为「请求头」）。  
3. **任务执行过程中可读**：工作流节点通过 **`NodeContext` 上的只读（约定）字段** 读取；值在 **每次 `run_once` 入口** 从服务端存储 **读取最新** 快照，再注入当次执行（含 rerun 的新 worker pass）。  
4. **UI**：左侧栏增加 **「设置」**（或齿轮图标 + `aria-label`），打开 **Modal**，加载 `GET`、保存 `PUT`；与「新建任务」Modal 独立。

---

## 2. 数据模型

### 2.1 存储（SQLite）

- **专用表**（建议）：`settings`  
  - `key TEXT PRIMARY KEY`  
  - `value_json TEXT NOT NULL`  
- **固定 key**：`ops_globals`  
  - **JSON 对象**：`{"cookie": "<string>", "authorization": "<string>"}`  
  - 缺省键视为 `""`（读时归一化，写时整对象替换）。  
- 迁移：在 `SqliteStore.init_schema` / `_migrate_*` 中与现有迁移同风格创建表；无 row 时 `GET` 等价于两个空串。

### 2.2 与 `tasks.context_json` 的关系

- **禁止**：把 `ops_globals` 合并进 `context_json` 作为唯一来源。  
- **允许**：节点代码 **只读** `ctx.ops_globals`（或文档中确定的一致命名），与 `ctx.context` 并列。

---

## 3. HTTP API（控制面）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/settings/ops` | 返回 `{ "cookie": string, "authorization": string }` |
| `PUT` | `/settings/ops` | Body 同上，**整对象替换** 持久化；响应 `200` + 当前持久化体（或与项目其它 PUT 惯例一致） |

- **鉴权**：与当前控制面一致（首版可仍为内网无鉴权；spec 注明生产需收紧）。  
- **错误**：无效 JSON、`authorization`/`cookie` 非字符串时可 `422`（实现可选用宽松校验：强制为 string）。

---

## 4. 执行路径：`run_once` 与 `NodeContext`

### 4.1 读取时机

- **每次进入 `run_once`**：在组装各节点 `NodeContext` **之前**，从 `SqliteStore` **读取当前 `ops_globals`**（无则空对象）。  
- **同一 `run_once` 内** 所有节点共享同一次快照；**下一次** `run_once`（含 interrupt 后继续、rerun 后新 pass）**再次读取**，因此能拿到 **最新** 全局配置。

### 4.2 `NodeContext` 扩展

- 新增字段（名称实现阶段固定，建议）：  
  `ops_globals: dict[str, str]`  
  形态：`{"cookie": "...", "authorization": "..."}`，缺省键为 `""`。  
- **约定**：节点 **不得** 通过此字段写回数据库；任务可变状态仍用 `ctx.context` 与既有 store API。

### 4.3 `SqliteStore` 方法（建议）

- `get_ops_globals() -> dict[str, str]`  
- `set_ops_globals(obj: dict[str, str]) -> None`  

内部读写 `settings` 表 `ops_globals` 行。

---

## 5. Web UI

- **位置**：左侧栏头部区域，与「新建任务」并列或紧邻（具体布局实现阶段确定）。  
- **文案**：按钮「设置」；Modal 标题「全局配置」或「Ops 全局设置」；字段标签 **Cookie**、**Authorization**（可用 `textarea` + 字号与等宽字体便于粘贴）。  
- **可选**：`authorization` 使用 `type="password"` 或可切换「显示/隐藏」，降低肩窥风险。  
- **行为**：打开 Modal 时 `GET /settings/ops`；保存时 `PUT`，成功后关闭或 toast（项目内一致性）。  
- **不**在「新建任务」表单中自动把两项写入 `POST /tasks` 的 `context`（执行侧统一从 `ops_globals` 读）。

---

## 6. 安全与运维

- 数据库中持久化 **敏感串**：等同于凭据；部署侧需限制 DB 文件权限、网络隔离；后续可加控制面登录与审计。  
- 规格 **不** 要求首版加密 At Rest；如需可另开 spec。

---

## 7. 测试要点

- Store：`get`/`set` roundtrip；缺行时 `get` 返回空串。  
- API：`GET`/`PUT` 集成测试（ASGI）。  
- Runner：`run_once` 注入 `NodeContext.ops_globals`（可用 mock store 或临时写入 settings 后断言节点可读）。  
- UI：可选轻量手测或后续 e2e。

---

## 8. 规格自检（2026-05-15）

- **全局 vs 任务 context**：分离存储与字段，已写明。  
- **读最新**：已固定为 **每次 `run_once` 入口** 从 DB 读。  
- **非请求语义**：§1 已声明与浏览器访问 API 的头无关。  
- 无 **TBD** 占位；实现计划可拆：迁移 + store、API 路由、`NodeContext` + `run_once`、Web Modal。

---

## 9. 实现计划

审阅通过后：`docs/superpowers/plans/2026-05-15-wf-console-global-ops-settings.md`（由 `writing-plans` 生成）。

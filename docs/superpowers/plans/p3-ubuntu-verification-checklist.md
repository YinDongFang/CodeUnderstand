# P3 · Ubuntu 真机验收清单（Web UI + SSE + Rewrite API）

> 假定 **P2 编排/SQLite/快照** 已通过；本节只验证 **P3 增补能力**。  
> 数据根：**`CU_DATA_ROOT`**（默认 **`~/.code-understand`**）。  
> **API/P3 摘要**：**`docs/superpowers/specs/p2-api-reference.md`** §「P3 · Web UI（补充端点）」。

---

## 1. 准备与测试

```bash
cd ~/CodeUnderstand && source .venv/bin/activate
pip install -e ".[test]"

python -m pytest tests/ -m "not slow" -q
```

预期：全绿（数量随版本递增）。

前端构建（**无 **`web/dist`** 时** `GET /` 会显示占位说明页）：

```bash
cd web && npm ci && npm run build && cd ..
# 或使用已提交到仓库的 web/dist/
```

可选：通过 **`CU_WEB_DIST=/abs/path/to/dist`** 指定静态资源目录。

---

## 2. `cu serve` · OpenAPI · Meta

```bash
cu serve --port 8765 &
sleep 1
curl -s http://127.0.0.1:8765/api/v1/meta | jq
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/docs
kill %1       # 或记录 PID 后 kill
```

预期：**`meta.web_ui`** 在存在 **`web/dist/index.html`** 时为 **`true`**；**`/docs`** 返回 **200**。

---

## 3. SSE（单作业冒烟）

需在 **Ubuntu + 前台或后台**保持 **`cu serve`**，另开终端：

```bash
JOB_ID="p3-sse-1"
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"zip_url":"https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip","job_id":"'"$JOB_ID"'"}' | jq .job_id

# 监听事件（手动 Ctrl+C 结束）；运行作业后应陆续出现 step: 类 message
curl -Ns http://127.0.0.1:8765/api/v1/jobs/$JOB_ID/events
```

另终端触发阶段（可先 **`export CU_TEST_MODE=1`** 缩短 Claude 耗时）：

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs/$JOB_ID/stages/bootstrap/run
```

预期：SSE **`data:`** 中含 **`stage`** / **`message`** / **`ts`** JSON；断开连接后服务端不再持有该订阅（无长期泄漏）。

**并行作业**：对不同 **`job_id`** 各开一个 **`curl .../events`**，确认互不串事件。

---

## 4. 浏览器 Web UI（Hash 路由）

1. **`http://127.0.0.1:8765/`** — 列表、新建作业。  
2. **`#/jobs/<job_id>`** — 详情、勾选 SSE、触发 **run / rerun build**。  
3. **`#/jobs/<job_id>/rewrite`** — **compile success** 后加载题目 **PUT** 写回会话，再触发 **build**。

预期：**`/api`** 不受影响；前端请求同源 **`/api/v1/...`**。

---

## 5. Rewrite + Build（与设计对齐）

前提：**conversation + compile** 已成功，DB 中含 **`session_id`** 与 **`claude_project_dir`**。

```bash
curl -s http://127.0.0.1:8765/api/v1/jobs/$JOB_ID/rewrite/questions | jq
# 按需 PUT 后在 UI 或直接 curl 触发 build：
curl -s -X POST http://127.0.0.1:8765/api/v1/jobs/$JOB_ID/stages/build/run
```

预期：**compile 未完成** 时 **GET/PUT rewrite** 返回 **409**。经 **API** 跑的 **build** 在 **export+zip** 前会跑一次 **`cu.pipeline.rewrite` stdin-lines**（stdin 为当前题目，常为幂等等同写回）。**再改题目并出包** → 再次 **PUT** 后 **`rerun build`**。

zip 就绪后：**`GET /api/v1/jobs/{id}/artifacts/zip`** 或 **`JobDTO.artifact_zip_path`** 指明路径。

---

## 完成标准

- §1 pytest 与本清单 §2–§5 中与你的环境相匹配的子集均通过记录。  
- **说明**：本节**不**取代 P2 §3「真实 loop」长耗时验证；可与 **`CU_TEST_MODE=1`** 组合缩短路径。

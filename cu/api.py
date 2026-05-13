"""FastAPI 应用：REST API 暴露 Orchestrator 能力。

路由前缀 ``/api/v1/``；P3 增补 SSE、rewrite 题目读写、静态 Web UI（``web/dist``）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cu import orchestrator as orch
from cu.events import sse_event_line
from cu.events import subscribe as events_subscribe
from cu.models import JOB_STAGES
from cu.paths import artifact_root
from cu.session_questions import apply_question_lines, extract_question_lines


class CreateJobRequest(BaseModel):
    zip_url: str
    job_id: Optional[str] = None
    notes: Optional[str] = ""


class StageDTO(BaseModel):
    stage: str
    status: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    log_tail: str = ""
    attempt: int = 0


class JobDTO(BaseModel):
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str
    claude_project_dir: str
    status: str
    created_at: str
    updated_at: str
    notes: str = ""
    stages: list[StageDTO] = []
    artifact_zip_path: str = ""


class QuestionsDTO(BaseModel):
    lines: list[str] = Field(default_factory=list)


def _web_dist_dir() -> Path:
    """查找含 ``index.html`` 的静态资源目录。

    顺序：**CU_WEB_DIST** → 当前工作目录下 ``web/dist``（便于在仓库根执行 ``cu serve``）→
    ``cu`` 包上一级目录 ``web/dist``（``pip install -e .`` 典型布局）。
    若均不存在可用文件，仍返回最常见路径便于排查。
    """
    raw = os.environ.get("CU_WEB_DIST", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()

    candidates = [
        Path.cwd() / "web" / "dist",
        Path(__file__).resolve().parents[1] / "web" / "dist",
    ]
    for c in candidates:
        try:
            if (c / "index.html").is_file():
                return c.resolve()
        except OSError:
            continue
    return candidates[1].resolve()


def _artifact_zip_path(job_id: str, repo: str) -> str:
    root = artifact_root(job_id, repo)
    return os.path.join(os.path.dirname(root), f"code-understand-{repo}.zip")


def _session_jsonl_from_record(rec: object) -> str:
    sid = getattr(rec, "session_id", "") or ""
    cdir = getattr(rec, "claude_project_dir", "") or ""
    if not sid or not cdir:
        return ""
    if sid.lower().endswith(".jsonl"):
        sid = sid[:-6]
    return os.path.join(cdir, f"{sid}.jsonl")


def _require_compile_success(job_id: str) -> None:
    st = orch.get_stages(job_id).get("compile")
    if st is None or st.status != "success":
        raise HTTPException(
            status_code=409,
            detail="需要先成功完成 compile 阶段才能读写题目",
        )


def _job_to_dto(job_id: str) -> JobDTO | None:
    rec = orch.get_job(job_id)
    if rec is None:
        return None
    stages = orch.get_stages(job_id)
    stage_dtos = [
        StageDTO(
            stage=s,
            **{
                k: v
                for k, v in stages[s].to_dict().items()
                if k not in ("job_id", "stage")
            },
        )
        for s in JOB_STAGES
    ]
    zip_p = _artifact_zip_path(rec.job_id, rec.repo)
    if not os.path.isfile(zip_p):
        zip_p = ""
    data = rec.to_dict()
    data["artifact_zip_path"] = zip_p
    return JobDTO(**data, stages=stage_dtos)


def create_app() -> FastAPI:
    app = FastAPI(title="CodeUnderstand Orchestrator", version="0.3.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    orch.ensure_db()

    @app.post("/api/v1/jobs", response_model=JobDTO, status_code=201)
    def create_job(req: CreateJobRequest):
        try:
            rec = orch.create_job(req.zip_url, job_id=req.job_id, notes=req.notes or "")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _job_to_dto(rec.job_id)

    @app.get("/api/v1/jobs", response_model=list[JobDTO])
    def list_jobs(status: Optional[str] = None):
        return [_job_to_dto(r.job_id) for r in orch.list_jobs(status=status)]

    @app.get("/api/v1/jobs/{job_id}", response_model=JobDTO)
    def get_job(job_id: str):
        dto = _job_to_dto(job_id)
        if dto is None:
            raise HTTPException(status_code=404, detail="job not found")
        return dto

    @app.get("/api/v1/jobs/{job_id}/rewrite/questions", response_model=QuestionsDTO)
    def get_rewrite_questions(job_id: str):
        _require_compile_success(job_id)
        rec = orch.get_job(job_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="job not found")
        path = _session_jsonl_from_record(rec)
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="session JSONL 不存在")
        return QuestionsDTO(lines=extract_question_lines(path))

    @app.put("/api/v1/jobs/{job_id}/rewrite/questions", response_model=QuestionsDTO)
    def put_rewrite_questions(job_id: str, body: QuestionsDTO):
        _require_compile_success(job_id)
        rec = orch.get_job(job_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="job not found")
        path = _session_jsonl_from_record(rec)
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="session JSONL 不存在")
        try:
            apply_question_lines(path, body.lines)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return QuestionsDTO(lines=extract_question_lines(path))

    def _api_web_build_rewrite_for(stage: str) -> bool:
        return stage == "build"

    @app.post("/api/v1/jobs/{job_id}/stages/{stage}/run")
    def run_stage_ep(job_id: str, stage: str):
        if stage not in JOB_STAGES:
            raise HTTPException(status_code=400, detail="invalid stage")
        try:
            orch.run_stage(
                job_id,
                stage,
                api_web_build_rewrite=_api_web_build_rewrite_for(stage),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"started": True}

    @app.post("/api/v1/jobs/{job_id}/stages/{stage}/rerun")
    def rerun_stage_ep(job_id: str, stage: str):
        if stage not in JOB_STAGES:
            raise HTTPException(status_code=400, detail="invalid stage")
        try:
            orch.rerun_from(
                job_id,
                stage,
                api_web_build_rewrite=_api_web_build_rewrite_for(stage),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"started": True}

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        ok = orch.cancel(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="no active run")
        return {"cancelled": True}

    @app.delete("/api/v1/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        ok = orch.delete_job(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="job not found")
        return None

    @app.get("/api/v1/jobs/{job_id}/events")
    async def job_events_sse(job_id: str):
        if orch.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")

        async def gen():
            import asyncio

            loop = asyncio.get_running_loop()
            q, unsub = events_subscribe(job_id, loop=loop)
            yield b": connected\n\n"
            try:
                while True:
                    item = await q.get()
                    yield sse_event_line(item).encode("utf-8")
            finally:
                unsub()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/jobs/{job_id}/artifacts/zip")
    def download_zip(job_id: str):
        rec = orch.get_job(job_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="job not found")
        p = _artifact_zip_path(job_id, rec.repo)
        if not os.path.isfile(p):
            raise HTTPException(status_code=404, detail="zip 不存在或未生成")
        return FileResponse(
            p,
            filename=os.path.basename(p),
            media_type="application/zip",
        )

    @app.get("/api/v1/meta")
    def meta():
        dist = _web_dist_dir()
        has = (dist / "index.html").is_file()
        return {
            "version": "0.3.0",
            "web_ui": has,
            "web_dist": str(dist),
        }

    dist = _web_dist_dir()
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")
    else:

        @app.get("/", include_in_schema=False)
        def root_placeholder() -> HTMLResponse:
            hint = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><title>Web UI 未就绪</title></head>
<body style="font-family:system-ui;max-width:42rem;margin:2rem auto;padding:0 1rem">
<h1>未找到前端构建产物</h1>
<p>根路径需要先存在 <strong>web/dist/index.html</strong> 才会挂载界面。你当前看到的是说明页（不是 API 报错）。</p>
<h2>任选其一：</h2>
<ol>
<li>在本仓库目录执行：<br/><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></li>
<li>使用可编辑安装，使磁盘上 <code>web/dist</code> 与 <code>cu</code> 包同级：<br/><code>pip install -e .</code></li>
<li>或设置环境变量 <code>CU_WEB_DIST</code> 指向<strong>已完成构建</strong>的 <code>dist</code> 目录</li>
</ol>
<p>然后从<strong>仓库根目录</strong>再执行：<code>cu serve</code>（这样也会匹配「当前目录下的 web/dist」）</p>
<p>API 文档：<a href="/docs">OpenAPI /docs</a></p>
<p><a href="/api/v1/meta">GET /api/v1/meta</a>（查看 <code>web_dist</code> 解析路径与 <code>web_ui</code>）</p>
</body></html>"""
            return HTMLResponse(content=hint, media_type="text/html; charset=utf-8")

    return app


app = create_app()

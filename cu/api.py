"""FastAPI 应用：REST API 暴露 Orchestrator 能力。

路由前缀 /api/v1/。
P3 将在此基础上增加 SSE /api/v1/jobs/{id}/events 与静态 UI。
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from cu import orchestrator as orch
from cu.models import JOB_STAGES


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


def _job_to_dto(job_id: str) -> JobDTO | None:
    rec = orch.get_job(job_id)
    if rec is None:
        return None
    stages = orch.get_stages(job_id)
    stage_dtos = [
        StageDTO(stage=s, **{k: v for k, v in stages[s].to_dict().items() if k not in ("job_id", "stage")})
        for s in JOB_STAGES
    ]
    return JobDTO(**rec.to_dict(), stages=stage_dtos)


def create_app() -> FastAPI:
    app = FastAPI(title="CodeUnderstand Orchestrator", version="0.2.0")
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

    @app.post("/api/v1/jobs/{job_id}/stages/{stage}/run")
    def run_stage(job_id: str, stage: str):
        if stage not in JOB_STAGES:
            raise HTTPException(status_code=400, detail="invalid stage")
        try:
            orch.run_stage(job_id, stage)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"started": True}

    @app.post("/api/v1/jobs/{job_id}/stages/{stage}/rerun")
    def rerun_stage(job_id: str, stage: str):
        if stage not in JOB_STAGES:
            raise HTTPException(status_code=400, detail="invalid stage")
        try:
            orch.rerun_from(job_id, stage)
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

    return app


app = create_app()

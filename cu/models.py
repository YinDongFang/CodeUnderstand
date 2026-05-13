"""作业与阶段记录 dataclass + 字典互转。"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping, Optional


JOB_STAGES: tuple[str, str, str, str] = ("bootstrap", "conversation", "compile", "build")

JOB_STATUSES = ("pending", "running", "success", "failed", "cancelled")
STAGE_STATUSES = ("pending", "running", "success", "failed", "cancelled")


@dataclass
class JobRecord:
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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping) -> "JobRecord":
        notes = row["notes"] if "notes" in row.keys() else ""
        return cls(
            job_id=row["job_id"],
            repo=row["repo"],
            zip_url=row["zip_url"],
            github_url=row["github_url"],
            session_id=row["session_id"],
            claude_project_dir=row["claude_project_dir"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            notes=notes,
        )


@dataclass
class StageRunRecord:
    job_id: str
    stage: str
    status: str = "pending"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    log_tail: str = ""
    attempt: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping) -> "StageRunRecord":
        return cls(
            job_id=row["job_id"],
            stage=row["stage"],
            status=row["status"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            exit_code=row["exit_code"],
            log_tail=row["log_tail"],
            attempt=row["attempt"],
        )

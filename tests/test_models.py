from cu.models import JobRecord, StageRunRecord, JOB_STAGES


def test_job_record_to_dict_roundtrip():
    now = "2026-05-13T10:00:00+00:00"
    j = JobRecord(
        job_id="abc", repo="r", zip_url="u", github_url="g",
        session_id="", claude_project_dir="",
        status="pending", created_at=now, updated_at=now, notes="",
    )
    d = j.to_dict()
    j2 = JobRecord.from_row(d)
    assert j2 == j


def test_stage_run_record_default():
    s = StageRunRecord(job_id="abc", stage="bootstrap")
    assert s.status == "pending"
    assert s.attempt == 0
    assert s.exit_code is None
    assert s.started_at is None


def test_job_stages_constant():
    assert JOB_STAGES == ("bootstrap", "conversation", "compile", "build")


def test_stage_record_from_row_dict():
    row = dict(
        job_id="j", stage="bootstrap", status="success",
        started_at="t1", ended_at="t2",
        exit_code=0, log_tail="ok", attempt=1,
    )
    s = StageRunRecord.from_row(row)
    assert s.status == "success"
    assert s.exit_code == 0
    assert s.attempt == 1

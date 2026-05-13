"""阶段执行集成测试 — mock subprocess 验证调用链。"""
import os
from unittest.mock import patch

from cu.runner import RunResult
from cu.stages import JobContext, run_bootstrap
from cu.paths import sandbox_home


def test_bootstrap_creates_sandbox_and_calls_download(isolated_env):
    ctx = JobContext(
        job_id="test-001",
        repo="my-repo",
        zip_url="https://github.com/owner/my-repo/archive/refs/heads/main.zip",
        github_url="https://github.com/owner/my-repo",
    )
    fake_result = RunResult(returncode=0, stdout="ok\n", stderr="")
    with patch("cu.stages.run_script", return_value=fake_result) as mock_run:
        run_bootstrap(ctx)

    home = sandbox_home("test-001")
    assert os.path.isdir(home)
    assert os.path.isdir(os.path.join(home, ".claude", "projects"))

    mock_run.assert_called_once()
    pos_args = mock_run.call_args.args
    kwargs = mock_run.call_args.kwargs

    script_path = pos_args[0]
    assert script_path.endswith("download.sh") or script_path.endswith("download.sh")
    assert "download.sh" in script_path.replace("\\", "/")

    args_value = kwargs.get("args") if "args" in kwargs else (
        pos_args[1] if len(pos_args) > 1 else None
    )
    assert args_value is not None
    assert ctx.zip_url in args_value

    env = kwargs.get("env")
    assert env is not None
    assert env["HOME"] == home


def test_bootstrap_invokes_on_event(isolated_env):
    events: list[tuple[str, str]] = []
    ctx = JobContext(
        job_id="test-evt",
        repo="r",
        zip_url="https://github.com/o/r/archive/refs/heads/main.zip",
        github_url="https://github.com/o/r",
        on_event=lambda s, m: events.append((s, m)),
    )
    fake_result = RunResult(returncode=0, stdout="ok\n", stderr="")
    with patch("cu.stages.run_script", return_value=fake_result):
        run_bootstrap(ctx)
    stages = [s for s, _ in events]
    msgs = [m for _, m in events]
    assert stages == ["bootstrap", "bootstrap", "bootstrap"]
    assert msgs[0] == "start"
    assert msgs[1].startswith("step:")
    assert msgs[-1] == "done"


def test_bootstrap_propagates_failure(isolated_env):
    ctx = JobContext(
        job_id="test-fail",
        repo="my-repo",
        zip_url="https://github.com/owner/my-repo/archive/refs/heads/main.zip",
        github_url="https://github.com/owner/my-repo",
    )
    fake_result = RunResult(returncode=2, stdout="", stderr="boom")
    with patch("cu.stages.run_script", return_value=fake_result):
        import pytest
        with pytest.raises(RuntimeError) as exc:
            run_bootstrap(ctx)
        assert "bootstrap" in str(exc.value)
        assert "download" in str(exc.value)


def test_full_pipeline_mock(isolated_env, monkeypatch):
    """mock 全链：4 阶段顺序调用，conversation 后注入 CLAUDE_PROJECT_DIR。"""
    from cu import stages as st
    from cu.paths import sandbox_home

    job_id = "pipe-001"
    repo = "demo-repo"
    session_id = "deadbeef-0000-0000-0000-000000000000"

    calls = []

    def fake_run_script(script, args=None, **kwargs):
        env = kwargs.get("env")
        calls.append(("script", script, list(args or []), dict(env or {})))
        from cu.runner import RunResult
        if script.endswith("loop.sh"):
            sandbox = sandbox_home(job_id)
            proj = os.path.join(sandbox, ".claude", "projects", "encoded-cwd-x")
            os.makedirs(proj, exist_ok=True)
            with open(os.path.join(proj, f"{session_id}.jsonl"), "w") as f:
                f.write("{}\n")
            return RunResult(0, f"info\n{session_id}\n", "")
        return RunResult(0, "ok\n", "")

    def fake_run_python(script, args=None, **kwargs):
        env = kwargs.get("env")
        calls.append(("python", script, list(args or []), dict(env or {})))
        from cu.runner import RunResult
        return RunResult(0, "", "")

    monkeypatch.setattr(st, "run_script", fake_run_script)
    monkeypatch.setattr(st, "run_python", fake_run_python)

    ctx = st.JobContext(
        job_id=job_id, repo=repo,
        zip_url=f"https://github.com/u/{repo}/archive/refs/heads/main.zip",
        github_url=f"https://github.com/u/{repo}",
    )
    for name in st.STAGES:
        st.STAGE_RUNNERS[name](ctx)

    assert len(calls) == 8, f"expected 8 calls, got {len(calls)}: {[c[1] for c in calls]}"

    script_seq = [os.path.basename(c[1]) for c in calls]
    assert script_seq == [
        "download.sh",
        "loop.sh", "clean.py", "build.sh",
        "metadata.sh", "clean_artifacts.sh",
        "export_session.sh", "zip.sh",
    ], f"unexpected sequence: {script_seq}"

    build_call = calls[3]
    assert build_call[3].get("BUILD_DOC_ONLY") == "1"
    assert build_call[3].get("CLAUDE_PROJECT_DIR", "").endswith("encoded-cwd-x")

    export_call = calls[6]
    assert export_call[3].get("CLAUDE_PROJECT_DIR", "").endswith("encoded-cwd-x")
    assert export_call[3].get("ARTIFACT_ROOT", "").endswith(f"code-understand-{repo}")

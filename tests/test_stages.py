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

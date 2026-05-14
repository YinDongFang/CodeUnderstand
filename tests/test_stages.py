"""阶段执行集成测试 — mock subprocess / pipeline 验证调用链。"""
import os
from unittest.mock import MagicMock, patch

from cu.paths import artifact_root, sandbox_home
from cu.runner import RunResult
from cu.stages import JobContext, run_bootstrap


def test_bootstrap_creates_sandbox_and_calls_download(isolated_env):
    ctx = JobContext(
        job_id="test-001",
        repo="my-repo",
        zip_url="https://github.com/owner/my-repo/archive/refs/heads/main.zip",
        github_url="https://github.com/owner/my-repo",
    )
    mock_dl = MagicMock()
    with patch("cu.stages.bootstrap.download_github_zip", mock_dl):
        run_bootstrap(ctx)

    home = sandbox_home("test-001")
    assert os.path.isdir(home)
    assert os.path.isdir(os.path.join(home, ".claude", "projects"))

    mock_dl.assert_called_once()
    zip_url, projects_dir = mock_dl.call_args[0]
    assert zip_url == ctx.zip_url
    assert projects_dir == os.path.join(
        artifact_root("test-001", "my-repo"), "code"
    )


def test_bootstrap_invokes_on_event(isolated_env):
    events: list[tuple[str, str]] = []
    ctx = JobContext(
        job_id="test-evt",
        repo="r",
        zip_url="https://github.com/o/r/archive/refs/heads/main.zip",
        github_url="https://github.com/o/r",
        on_event=lambda s, m: events.append((s, m)),
    )
    with patch("cu.stages.bootstrap.download_github_zip", MagicMock()):
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

    def boom(u, d):
        raise RuntimeError("boom")

    with patch("cu.stages.bootstrap.download_github_zip", boom):
        import pytest
        with pytest.raises(RuntimeError) as exc:
            run_bootstrap(ctx)
        assert "bootstrap" in str(exc.value)
        assert "download" in str(exc.value)


def test_run_conversation_runs_loop_with_cu_test_mode_overlay(isolated_env, monkeypatch):
    """CU_TEST_MODE=1 时执行 Python loop/build_docs；stage_env 注入 *USE_MOCK_CLAUDE。"""
    monkeypatch.setenv("CU_TEST_MODE", "1")

    from cu import stages as st
    from cu.sandbox import bootstrap_sandbox
    from cu.paths import code_dir, sandbox_home

    jid = "conv-mock"
    repo = "demo-repo"
    session_id = "deadbeef-0000-0000-0000-000000000000"
    bootstrap_sandbox(jid)
    os.makedirs(code_dir(jid, repo), exist_ok=True)

    modules: list[str] = []

    def fake_run_module(module, module_args=None, **kwargs):
        env = kwargs.get("env") or {}
        modules.append(module)
        if module == "cu.pipeline.loop":
            assert env.get("LOOP_USE_MOCK_CLAUDE") == "1"
            assert env.get("BUILD_USE_MOCK_CLAUDE") == "1"
            assert env.get("CLASSIFY_USE_MOCK_CLAUDE") == "1"
            sandbox = sandbox_home(jid)
            proj = os.path.join(sandbox, ".claude", "projects", "mock-proj")
            os.makedirs(proj, exist_ok=True)
            with open(os.path.join(proj, f"{session_id}.jsonl"), "w") as f:
                f.write("{}\n")
            return RunResult(0, f"info\n{session_id}\n", "")
        if module == "cu.pipeline.build_docs":
            assert env.get("BUILD_USE_MOCK_CLAUDE") == "1"
            assert env.get("BUILD_DOC_ONLY") == "1"
        return RunResult(0, "", "")

    monkeypatch.setattr(
        "cu.stages.conversation.run_module",
        fake_run_module,
    )

    def noop_dedupe(target: str, sid: str):
        return {"ok": True, "deleted": 0}

    monkeypatch.setattr("cu.stages.conversation.run_dedupe_session", noop_dedupe)

    ctx = JobContext(
        job_id=jid,
        repo=repo,
        zip_url=f"https://github.com/u/{repo}/archive/refs/heads/main.zip",
        github_url=f"https://github.com/u/{repo}",
    )
    st.run_conversation(ctx)

    assert ctx.session_id == session_id
    assert "mock-proj" in ctx.claude_project_dir.replace("\\", "/")

    assert modules == ["cu.pipeline.loop", "cu.pipeline.build_docs"]


def test_full_pipeline_mock(isolated_env, monkeypatch):
    """mock 全链：Python loop/build_docs + 其余阶段；校验顺序与环境变量。"""
    monkeypatch.setenv("CU_TEST_MODE", "1")
    from cu import stages as st
    from cu.paths import sandbox_home

    job_id = "pipe-001"
    repo = "demo-repo"
    session_id = "deadbeef-0000-0000-0000-000000000000"

    order: list[str] = []

    monkeypatch.setattr(
        "cu.stages.bootstrap.download_github_zip",
        lambda u, d: order.append("download"),
    )

    build_env_holder: dict = {}

    def fake_run_module(module, module_args=None, **kwargs):
        env = kwargs.get("env") or {}
        from cu.runner import RunResult
        if module == "cu.pipeline.loop":
            order.append("loop")
            assert env.get("LOOP_USE_MOCK_CLAUDE") == "1"
            sandbox = sandbox_home(job_id)
            proj = os.path.join(sandbox, ".claude", "projects", "encoded-cwd-x")
            os.makedirs(proj, exist_ok=True)
            with open(os.path.join(proj, f"{session_id}.jsonl"), "w") as f:
                f.write("{}\n")
            return RunResult(0, f"info\n{session_id}\n", "")
        if module == "cu.pipeline.build_docs":
            order.append("build_doc")
            build_env_holder.update(env)
            return RunResult(0, "ok\n", "")
        return RunResult(0, "ok\n", "")

    monkeypatch.setattr("cu.stages.conversation.run_module", fake_run_module)

    export_kw: dict = {}

    def capture_export(**kw):
        export_kw.update(kw)
        order.append("export")

    monkeypatch.setattr("cu.stages.conversation.run_dedupe_session", lambda *a: {"ok": True})
    monkeypatch.setattr(
        "cu.stages.compile.run_metadata",
        lambda **kw: order.append("metadata"),
    )
    monkeypatch.setattr(
        "cu.stages.compile.clean_artifact_tree",
        lambda ar: order.append("clean"),
    )
    monkeypatch.setattr("cu.stages.build.export_session", capture_export)
    monkeypatch.setattr(
        "cu.stages.build.archive",
        lambda p: order.append("zip"),
    )

    ctx = st.JobContext(
        job_id=job_id, repo=repo,
        zip_url=f"https://github.com/u/{repo}/archive/refs/heads/main.zip",
        github_url=f"https://github.com/u/{repo}",
    )
    for name in st.STAGES:
        st.STAGE_RUNNERS[name](ctx)

    assert order == [
        "download", "loop", "build_doc", "metadata", "clean", "export", "zip",
    ]

    assert build_env_holder.get("BUILD_DOC_ONLY") == "1"
    assert build_env_holder.get("BUILD_USE_MOCK_CLAUDE") == "1"
    assert (build_env_holder.get("CLAUDE_PROJECT_DIR") or "").replace("\\", "/").endswith(
        "encoded-cwd-x"
    )

    assert (export_kw.get("claude_project_dir") or "").replace("\\", "/").endswith(
        "encoded-cwd-x"
    )
    art = export_kw.get("artifact_root") or ""
    assert art.replace("\\", "/").endswith(f"code-understand-{repo}")

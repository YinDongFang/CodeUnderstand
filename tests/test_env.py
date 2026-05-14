import os
from cu.env import stage_env


def _clear_cu_test_overlay(monkeypatch):
    """避免继承宿主机环境中的 mock / 测试开关干扰断言。"""
    for key in (
        "CU_TEST_MODE",
        "LOOP_USE_MOCK_CLAUDE",
        "BUILD_USE_MOCK_CLAUDE",
        "CLASSIFY_USE_MOCK_CLAUDE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_stage_env_sets_home(tmp_path, monkeypatch):
    _clear_cu_test_overlay(monkeypatch)
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    env = stage_env(
        job_id="j1",
        repo="react",
        session_id="sess-uuid",
        github_url="https://github.com/facebook/react",
    )
    expected_home = str(tmp_path / "jobs" / "j1" / "home")
    assert env["HOME"] == expected_home
    assert env["PROJECTS_DIR"] == os.path.join(
        expected_home, "code-understand-react", "code"
    )
    assert env["OUTPUTS_DIR"] == expected_home
    assert env["CODE_UNDERSTAND_STATE_ROOT"] == expected_home
    assert env["CU_JOB_PIPELINE"] == "1"
    assert env["GH_ARCHIVE_PROXY_PREFIX"].startswith("http")
    assert env["SESSION_ID"] == "sess-uuid"
    assert "PATH" in env


def test_stage_env_cu_test_mode_injects_mock_claude_flags(tmp_path, monkeypatch):
    _clear_cu_test_overlay(monkeypatch)
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CU_TEST_MODE", "1")
    env = stage_env(job_id="j1", repo="r1")
    assert env["LOOP_USE_MOCK_CLAUDE"] == "1"
    assert env["BUILD_USE_MOCK_CLAUDE"] == "1"
    assert env["CLASSIFY_USE_MOCK_CLAUDE"] == "1"


def test_stage_env_cu_test_mode_off_does_not_inject_overlay(tmp_path, monkeypatch):
    _clear_cu_test_overlay(monkeypatch)
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CU_TEST_MODE", "0")
    env = stage_env(job_id="j2", repo="r2")
    assert "LOOP_USE_MOCK_CLAUDE" not in env
    assert "BUILD_USE_MOCK_CLAUDE" not in env
    assert "CLASSIFY_USE_MOCK_CLAUDE" not in env

import os
from cu.env import stage_env


def test_stage_env_sets_home(tmp_path, monkeypatch):
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
    assert env["SESSION_ID"] == "sess-uuid"
    assert "PATH" in env

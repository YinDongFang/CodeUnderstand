"""测试共享 fixture。"""
import pytest


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    """把 TASKLINE_STATE_PATH 指向一个 tmp 文件，返回该 Path。"""
    p = tmp_path / "state.json"
    monkeypatch.setenv("TASKLINE_STATE_PATH", str(p))
    return p

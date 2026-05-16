"""测试共享 fixture。"""
import pytest


@pytest.fixture
def state_path(tmp_path):
    """返回一个 tmp 文件路径，作为 Flow(state_path=...) 参数传入。"""
    return tmp_path / "state.json"

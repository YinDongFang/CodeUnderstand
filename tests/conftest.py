"""Shared pytest fixtures."""
import os

import pytest


def pytest_configure(config):
    """未显式 export 时默认开启测试模式，使 stage_env() 注入 *USE_MOCK_CLAUDE。

    必须用真实 Claude 的用例可在模块或测试中 ``monkeypatch.setenv('CU_TEST_MODE', '0')``。
    """
    os.environ.setdefault("CU_TEST_MODE", "1")


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """提供隔离的 CU_DATA_ROOT 与模拟 HOME。

    返回字典：{"real_home": Path, "cu_root": Path, "tmp_path": Path}
    并在 ~/.claude 下放一个示例配置文件，方便沙箱复制逻辑测试。
    """
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / ".claude").mkdir()
    (real_home / ".claude" / "credentials.json").write_text("{}")

    cu_root = tmp_path / "cu_data"
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("CU_DATA_ROOT", str(cu_root))
    return {
        "real_home": real_home,
        "cu_root": cu_root,
        "tmp_path": tmp_path,
    }

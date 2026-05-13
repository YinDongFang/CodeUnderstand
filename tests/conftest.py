"""Shared pytest fixtures."""
import pytest


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

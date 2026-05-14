"""端到端冒烟测试 — 需要真实环境（claude, 网络, wget, jq）。

仅在 Ubuntu/Linux 上运行：`pytest tests/test_e2e_smoke.py -v -m slow`
日常 CI 用 `-m "not slow"` 跳过。
"""
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="慢速冒烟依赖 Linux/Git Bash 路径与工具链；见模块说明，请在 Linux CI 或 WSL 下执行",
)
@pytest.mark.slow
def test_cli_bootstrap_only(tmp_path, monkeypatch):
    """仅跑 bootstrap 阶段，验证沙箱建立与代码下载。"""
    monkeypatch.setenv("CU_DATA_ROOT", str(tmp_path / "cu"))

    url = "https://github.com/kelseyhightower/nocode/archive/refs/heads/master.zip"
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    result = subprocess.run(
        [
            sys.executable, "-m", "cu", "run", url,
            "--start", "bootstrap", "--end", "bootstrap",
            "--job-id", "smoke-test",
        ],
        capture_output=True, text=True, timeout=180,
        cwd=repo_root,
        env={**os.environ},
    )
    assert result.returncode == 0, (
        f"exit={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    home = tmp_path / "cu" / "jobs" / "smoke-test" / "home"
    assert home.is_dir(), f"沙箱 home 不存在: {home}"

    code = home / "code-understand-nocode" / "code" / "nocode"
    assert code.is_dir(), f"代码目录不存在: {code}"
    assert (code / "README.md").is_file(), "README.md 未下载"

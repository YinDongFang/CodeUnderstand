import subprocess
import sys

from cu.runner import run_script


def _bash_path(win_path: str) -> str:
    """Convert a Windows path to a form usable by the locally-installed `bash`.

    On Linux this is a no-op. On Windows the `bash` executable on PATH may be
    Git Bash (`/c/...` MSYS mount) or WSL bash (`/mnt/c/...` 9p mount). We probe
    once per test session by asking bash to stat both forms.
    """
    if sys.platform != "win32":
        return win_path
    posix = win_path.replace("\\", "/")
    drive = posix[0].lower()
    tail = posix[2:]
    for candidate in (f"/mnt/{drive}{tail}", f"/{drive}{tail}", posix):
        probe = subprocess.run(
            ["bash", "-c", f"test -e '{candidate}'"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return candidate
    return posix


def test_run_script_success(tmp_path):
    script = tmp_path / "ok.sh"
    script.write_bytes(b"#!/bin/bash\necho hello\n")
    script.chmod(0o755)
    result = run_script(
        _bash_path(str(script)),
        env=None,
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_script_failure(tmp_path):
    script = tmp_path / "fail.sh"
    script.write_bytes(b"#!/bin/bash\nexit 42\n")
    script.chmod(0o755)
    result = run_script(
        _bash_path(str(script)),
        env=None,
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 42

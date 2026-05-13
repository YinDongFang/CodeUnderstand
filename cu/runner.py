"""子进程执行：运行 bash 脚本/python 脚本并捕获输出。"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def run_script(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    stream: bool = False,
) -> RunResult:
    """通过 bash 解释器执行脚本。

    stream=True 时不重定向 stdout/stderr，子进程直接打印到父进程控制台，
    返回的 RunResult.stdout / stderr 为空串。
    """
    cmd = ["bash", script] + (args or [])
    if stream:
        try:
            proc = subprocess.run(cmd, env=env, cwd=cwd, timeout=timeout)
            return RunResult(returncode=proc.returncode, stdout="", stderr="")
        except subprocess.TimeoutExpired:
            return RunResult(returncode=-1, stdout="", stderr=f"timeout after {timeout}s")
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return RunResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(
            returncode=-1,
            stdout=e.stdout or "",
            stderr=e.stderr or f"timeout after {timeout}s",
        )


def run_python(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    stream: bool = False,
) -> RunResult:
    """通过 python3 解释器执行脚本。

    stream=True 时不重定向 stdout/stderr，子进程直接打印到父进程控制台，
    返回的 RunResult.stdout / stderr 为空串。
    """
    cmd = ["python3", script] + (args or [])
    if stream:
        try:
            proc = subprocess.run(cmd, env=env, cwd=cwd, timeout=timeout)
            return RunResult(returncode=proc.returncode, stdout="", stderr="")
        except subprocess.TimeoutExpired:
            return RunResult(returncode=-1, stdout="", stderr=f"timeout after {timeout}s")
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return RunResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(
            returncode=-1,
            stdout=e.stdout or "",
            stderr=e.stderr or f"timeout after {timeout}s",
        )

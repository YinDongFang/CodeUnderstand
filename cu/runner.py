"""子进程执行：运行 bash 脚本/python 脚本并捕获输出。

stream=True：实时透传 stdout/stderr 到父进程，不收集，RunResult.stdout/stderr 为空串。
pid_sink：可选回调，启动子进程后立刻把 PID 喂给调用方（用于 orchestrator cancel）。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


PidSink = Optional[Callable[[int], None]]


def _spawn_and_wait(
    cmd: list[str],
    *,
    env: dict[str, str] | None,
    cwd: str | None,
    timeout: int | None,
    stream: bool,
    pid_sink: PidSink,
) -> RunResult:
    """统一通过 Popen 启动子进程，立即抛 PID 给 pid_sink；wait/communicate 等收尾。"""
    if stream:
        proc = subprocess.Popen(cmd, env=env, cwd=cwd)
        if pid_sink is not None:
            pid_sink(proc.pid)
        try:
            rc = proc.wait(timeout=timeout)
            return RunResult(returncode=rc, stdout="", stderr="")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return RunResult(returncode=-1, stdout="", stderr=f"timeout after {timeout}s")

    proc = subprocess.Popen(
        cmd, env=env, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if pid_sink is not None:
        pid_sink(proc.pid)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return RunResult(
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return RunResult(
            returncode=-1,
            stdout=stdout or "",
            stderr=(stderr or "") + f"\ntimeout after {timeout}s",
        )


def run_script(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    stream: bool = False,
    pid_sink: PidSink = None,
) -> RunResult:
    """通过 bash 解释器执行脚本。"""
    return _spawn_and_wait(
        ["bash", script] + (args or []),
        env=env, cwd=cwd, timeout=timeout,
        stream=stream, pid_sink=pid_sink,
    )


def run_python(
    script: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    stream: bool = False,
    pid_sink: PidSink = None,
) -> RunResult:
    """通过 python3 解释器执行脚本。"""
    return _spawn_and_wait(
        ["python3", script] + (args or []),
        env=env, cwd=cwd, timeout=timeout,
        stream=stream, pid_sink=pid_sink,
    )

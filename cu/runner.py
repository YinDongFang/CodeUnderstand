"""子进程执行：运行 bash 脚本/python 脚本并捕获输出。

stream=True：实时透传 stdout/stderr 到父进程，不收集，RunResult.stdout/stderr 为空串。
pid_sink：可选回调，启动子进程后立刻把 PID 喂给调用方（用于 orchestrator cancel）。
"""
from __future__ import annotations

import subprocess
import sys
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
    stdin_data: str | bytes | None = None,
) -> RunResult:
    """统一通过 Popen 启动子进程；``stdin_data`` 仅在非 stream 模式下写入。"""
    if stream:
        if stdin_data is not None:
            raise ValueError("stream=True 不支持 stdin_data")
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

    stdin_arg = subprocess.PIPE if stdin_data is not None else None
    text_mode = stdin_data is None or isinstance(stdin_data, str)
    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=cwd,
        stdin=stdin_arg,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text_mode,
        encoding="utf-8" if text_mode else None,
        errors="replace" if text_mode else None,
    )
    if pid_sink is not None:
        pid_sink(proc.pid)
    try:
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
        rc = proc.returncode if proc.returncode is not None else -1
        return RunResult(returncode=rc, stdout=stdout or "", stderr=stderr or "")
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        tail = stderr or stdout or ""
        return RunResult(
            returncode=-1,
            stdout=stdout or "",
            stderr=tail + f"\ntimeout after {timeout}s",
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
        stream=stream, pid_sink=pid_sink, stdin_data=None,
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
    stdin_data: str | bytes | None = None,
) -> RunResult:
    """通过 python3 解释器执行脚本。``stdin_data`` 写入子进程 stdin（仅非 ``stream`` 模式）。"""
    return _spawn_and_wait(
        ["python3", script] + (args or []),
        env=env, cwd=cwd, timeout=timeout,
        stream=stream, pid_sink=pid_sink,
        stdin_data=stdin_data,
    )


def run_module(
    module: str,
    module_args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    stream: bool = False,
    pid_sink: PidSink = None,
) -> RunResult:
    """``python -m <module>``（解释器与当前进程一致）。"""
    return _spawn_and_wait(
        [sys.executable, "-m", module] + (module_args or []),
        env=env,
        cwd=cwd,
        timeout=timeout,
        stream=stream,
        pid_sink=pid_sink,
        stdin_data=None,
    )

"""宏阶段包：``bootstrap`` → ``conversation`` → ``compile`` → ``build``。

各阶段实现位于同目录下模块；单阶段调试：``python -m cu.stages.<stage> spec.json``。
"""
from __future__ import annotations

from cu.stages.bootstrap import run_bootstrap
from cu.stages.build import run_build, run_rewrite_stdin_mirror
from cu.stages.compile import run_compile
from cu.stages.context import JobContext, STAGES, EventCallback, PidSink
from cu.stages.conversation import run_conversation

STAGE_RUNNERS = {
    "bootstrap": run_bootstrap,
    "conversation": run_conversation,
    "compile": run_compile,
    "build": run_build,
}

__all__ = [
    "JobContext",
    "STAGES",
    "EventCallback",
    "PidSink",
    "STAGE_RUNNERS",
    "run_bootstrap",
    "run_conversation",
    "run_compile",
    "run_build",
    "run_rewrite_stdin_mirror",
]

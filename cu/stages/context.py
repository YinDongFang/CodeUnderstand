"""JobContext 与宏阶段元数据。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

STAGES = ("bootstrap", "conversation", "compile", "build")

EventCallback = Optional[Callable[[str, str], None]]  # (stage, message)
PidSink = Optional[Callable[[int], None]]


@dataclass
class JobContext:
    job_id: str
    repo: str
    zip_url: str
    github_url: str
    session_id: str = ""
    claude_project_dir: str = ""
    on_event: EventCallback = None
    pid_sink: PidSink = None
    #: API 驱动的 ``build`` 为 True：该段内先做会话题目幂等写回（与 ``cu run`` 默认跳过不同）。
    build_with_rewrite: bool = False

    def fire(self, stage: str, message: str) -> None:
        if self.on_event is not None:
            self.on_event(stage, message)

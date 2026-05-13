"""SSE 用作业事件中心：POSIX 线程里 `publish`， asyncio 线程里订阅 `Queue`。

`JobEventHub` 为模块级逻辑单例：`subscribe(job_id)` 返回 `(queue, unsub)`，`publish`/`publish_job_stage_event`
从 orchestrator worker 线程安全投递到 asyncio 侧的 queue。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable


_lock = threading.RLock()

# job_id -> 订阅：(loop, queue, unsub hook id)
_Subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]], int]]] = {}

_unsub_counter = itertools.count(1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_put(q: asyncio.Queue[dict[str, Any]], item: dict[str, Any]) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        # 丢弃：避免 worker 饿死；SSE 仍可依赖 GET job 兜底
        pass


def publish_job_stage_event(job_id: str, stage: str, message: str) -> None:
    """从任意线程调用；将向该 job_id 的全部订阅投递一条事件。"""
    payload = {"ts": _now_iso(), "stage": stage, "message": message}
    publish(job_id, payload)


def publish(job_id: str, payload: dict[str, Any]) -> None:
    """向订阅者投递已构造的字典（应含 ts/stage/message 等 SSE 负载）。"""
    with _lock:
        triples = list(_Subscribers.get(job_id, []))
    for loop, queue, _ in triples:

        def _put(q: asyncio.Queue[dict[str, Any]] = queue, p: dict[str, Any] = payload) -> None:
            _safe_put(q, p)

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            # loop 可能已关闭
            continue


def subscribe(
    job_id: str,
    *,
    maxsize: int = 128,
    loop: asyncio.AbstractEventLoop | None = None,
) -> tuple[asyncio.Queue[dict[str, Any]], Callable[[], None]]:
    """在 **当前 asyncio 事件循环**（或传入的 loop）上注册一个 queue。"""
    if loop is None:
        loop = asyncio.get_running_loop()

    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
    sub_id = next(_unsub_counter)

    with _lock:
        _Subscribers.setdefault(job_id, []).append((loop, q, sub_id))

    def unsub() -> None:
        with _lock:
            subs = _Subscribers.get(job_id)
            if not subs:
                return
            _Subscribers[job_id] = [t for t in subs if t[2] != sub_id]
            if not _Subscribers[job_id]:
                del _Subscribers[job_id]

    return q, unsub


def sse_event_line(payload: dict[str, Any]) -> str:
    """单条 SSE `data:` 行（不含结尾双换行，由路由统一补）。"""
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

"""应用级日志配置：stderr、[logger名][时间] 行格式。

级别由环境变量 ``CU_LOG_LEVEL`` 或 ``LOG_LEVEL`` 指定（默认 ``INFO``）。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Final

_DATEFMT: Final = "%Y-%m-%d %H:%M:%S"
_FMT: Final = "[%(name)s][%(asctime)s] %(message)s"


def line_formatter() -> logging.Formatter:
    return logging.Formatter(_FMT, datefmt=_DATEFMT)


def _coerce_level(level: int | str | None) -> int:
    if level is None:
        raw = os.environ.get("CU_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO"))
        return _coerce_level(raw)
    if isinstance(level, int):
        return level
    name = str(level).strip().upper()
    return int(getattr(logging, name, logging.INFO))


def configure_logging(level: int | str | None = None, *, force: bool = False) -> None:
    """为根 logger 附加 stderr Handler；幂等（除非 ``force=True``）。"""
    lvl = _coerce_level(level)
    root = logging.getLogger()
    if root.handlers and not force:
        root.setLevel(lvl)
        return

    if force:
        for h in root.handlers[:]:
            root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(line_formatter())
    root.addHandler(handler)
    root.setLevel(lvl)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

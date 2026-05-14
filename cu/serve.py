"""uvicorn 启动入口。"""
from __future__ import annotations

import uvicorn

from cu.logging_config import configure_logging


def main(host: str = "127.0.0.1", port: int = 8765) -> int:
    configure_logging()
    uvicorn.run("cu.api:app", host=host, port=port, log_level="info")
    return 0

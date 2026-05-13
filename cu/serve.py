"""uvicorn 启动入口。"""
from __future__ import annotations

import uvicorn


def main(host: str = "127.0.0.1", port: int = 8765) -> int:
    uvicorn.run("cu.api:app", host=host, port=port, log_level="info")
    return 0

#!/usr/bin/env python3
"""本地启动 wf_engine 控制面，并注册 ``workflow.demo`` 示例工作流。

运行（仓库根目录）::

    py -3 run_wf_engine.py

另开终端::

    cd web && npm run dev

浏览器打开 Vite 提示的地址；创建任务时 ``workflow_key`` 填 ``demo_pipeline``。
"""

from __future__ import annotations

from pathlib import Path

from wf_engine import Engine

from workflow.demo import register_all


def main() -> None:
    root = Path(__file__).resolve().parent
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)

    engine = Engine()
    register_all(engine)

    engine.serve(
        host="127.0.0.1",
        port=8000,
        db_path=data / "wf_engine.sqlite",
        tasks_root=data / "tasks",
        registry_module="workflow.demo",
    )


if __name__ == "__main__":
    main()

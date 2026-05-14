"""统一测试模式开关：`CU_TEST_MODE`。

当 ``CU_TEST_MODE`` 为真（``1`` / ``true`` / ``yes`` / ``on``，大小写不敏感）时，编排器通过 ``stage_env()`` 向子进程注入下列变量，
使 **conversation** 阶段的 ``cu.pipeline.loop`` / ``cu.pipeline.build_docs`` 以及 ``compile`` 中的 ``classify_main_type`` 走 mock（``python -m cu.testing.mock_claude``），不调用真实 ``claude`` CLI：

- ``LOOP_USE_MOCK_CLAUDE=1`` —— loop 编排内 Claude 调用
- ``BUILD_USE_MOCK_CLAUDE=1`` —— doc 生成阶段
- ``CLASSIFY_USE_MOCK_CLAUDE=1`` —— ``classify_main_type``（metadata / classify CLI）

设为 ``0`` / ``false`` / ``no`` / ``off`` 视为关闭。未设置时：**非测试模式**，**不注入** mock 变量。

pytest 推荐使用 ``tests/conftest.py`` 的全局会话钩子在未显式导出时默认 ``CU_TEST_MODE=1``，
以便集成测试也不依赖本机 Claude。若某条集成测试必须使用真实 Claude，可在该模块或测试中 ``monkeypatch.setenv("CU_TEST_MODE", "0")``。
"""
from __future__ import annotations

import os

_FALSEY = frozenset({"", "0", "false", "no", "off"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_cu_test_mode() -> bool:
    raw = os.environ.get("CU_TEST_MODE", "").strip().lower()
    if raw in _FALSEY:
        return False
    if raw in _TRUTHY:
        return True
    if raw:
        return False
    return False


def test_mode_subprocess_overlay() -> dict[str, str]:
    """传给 ``stage_env()`` 的子进程环境与 Claude mock 对齐。"""
    if not is_cu_test_mode():
        return {}
    return {
        "LOOP_USE_MOCK_CLAUDE": "1",
        "BUILD_USE_MOCK_CLAUDE": "1",
        "CLASSIFY_USE_MOCK_CLAUDE": "1",
    }


def loop_mock_overlay() -> dict[str, str]:
    """兼容旧名；等价于 ``test_mode_subprocess_overlay()``。"""
    return test_mode_subprocess_overlay()

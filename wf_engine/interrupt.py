from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Never


@dataclass
class ControlledInterrupt(Exception):
    expected_schema: dict[str, Any] | None
    ui: dict[str, Any] | None
    checkpoint: dict[str, Any] | None


def interrupt(
    *,
    expected_schema: dict[str, Any] | None,
    ui: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> Never:
    raise ControlledInterrupt(
        expected_schema=expected_schema,
        ui=ui,
        checkpoint=checkpoint,
    )

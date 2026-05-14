import pytest
from wf_engine.interrupt import ControlledInterrupt, interrupt


def test_interrupt_raises_controlled():
    with pytest.raises(ControlledInterrupt) as ei:
        interrupt(expected_schema=None, ui={"title": "x"})
    assert ei.value.expected_schema is None

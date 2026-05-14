import pytest

from wf_engine.engine import Engine
from wf_engine.workflow import Workflow


def test_duplicate_workflow_key_rejected():
    eng = Engine()
    wf = Workflow(key="demo")

    def n1(ctx):
        pass

    wf.add_node("a", n1)
    eng.register_workflow(wf)
    with pytest.raises(ValueError, match="workflow_key"):
        eng.register_workflow(wf)


def test_engine_lists_registered_workflows():
    eng = Engine()
    wf = Workflow(key="a")

    def n(ctx):
        pass

    wf.add_node("x", n)
    eng.register_workflow(wf)
    assert eng.list_workflows() == [
        {"key": "a", "input_schema": None},
    ]
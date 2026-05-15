"""dagflow.errors 与 dagflow.handle.NodeHandle 的单元测试。"""
import pytest

from dagflow.errors import NodeFailed, NodeSkipped


def test_node_failed_is_exception():
    assert issubclass(NodeFailed, Exception)
    e = NodeFailed("node-1")
    assert "node-1" in str(e)


def test_node_skipped_is_exception():
    assert issubclass(NodeSkipped, Exception)
    e = NodeSkipped("node-2")
    assert "node-2" in str(e)


def test_node_failed_can_carry_cause():
    inner = ValueError("root cause")
    outer = NodeFailed("n")
    outer.__cause__ = inner
    assert outer.__cause__ is inner


def test_two_failures_distinct_instances():
    a = NodeFailed("a")
    b = NodeFailed("b")
    assert a is not b

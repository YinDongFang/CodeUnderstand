"""节点失败 / 跳过的异常类型。"""


class NodeFailed(Exception):
    """节点 fn 抛异常时由框架抛出，__cause__ 指向 fn 抛的原异常。"""


class NodeSkipped(Exception):
    """因上游 FAILED/CANCELLED/SKIPPED 被跳过，__cause__ 指向最近上游的异常。"""

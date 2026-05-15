"""taskline 自定义异常。"""


class StateMismatchError(RuntimeError):
    """持久化文件与当前程序的 submit 序列不一致。"""

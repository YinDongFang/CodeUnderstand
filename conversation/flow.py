"""一个用 taskline 构建的 5 节点串行流水线。

每个节点把自己的 id 同时追加到 <qa>/output.txt 与 <repo>/output.txt。
每个节点完成后把 qa/repo 目录快照到 backup 目录（覆盖）；崩溃重启时，
resume 点节点的 before-hook 把 backup 中的快照还原回 qa/repo，
丢弃崩溃节点的部分写入。
"""
from __future__ import annotations

import os
import shutil

from taskline import Flow


async def run(qa_dir: str, repo_dir: str, backup_dir: str) -> str:
    """跑一条 5 节点的串行流水线。

    Args:
        qa_dir:     QA 目录路径，节点会向其下 output.txt 追加 id。
        repo_dir:   Repo 目录路径，节点会向其下 output.txt 追加 id。
        backup_dir: 备份目录路径，里面会建 `qa/` 与 `repo/` 两个子目录，
                    分别镜像 qa_dir 与 repo_dir 最近一次"节点成功后"的状态。

    Returns:
        最后一个节点（node5）的返回值。

    流水线形状：node1 → node2 → node3 → node4 → node5（线性串）。

    崩溃恢复语义：每个节点成功后 after-hook 把 qa/repo 拷到 backup（覆盖）；
    若进程在某节点中途崩溃，重启同一程序时该节点为 resume 点，其 before-hook
    把 backup 拷回 qa/repo（覆盖），抹掉崩溃节点的脏写入，再重新执行该节点。
    """
    os.makedirs(qa_dir, exist_ok=True)
    os.makedirs(repo_dir, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)

    qa_out = os.path.join(qa_dir, "output.txt")
    repo_out = os.path.join(repo_dir, "output.txt")
    qa_backup = os.path.join(backup_dir, "qa")
    repo_backup = os.path.join(backup_dir, "repo")

    # TODO: 填一个合适的持久化路径（同一程序重启时要读到同一个文件才能恢复）
    state_path = "./state.json"

    def write_id(node_id: str) -> None:
        """把 node_id 追加写入两个 output.txt（各一行）。"""
        for p in (qa_out, repo_out):
            with open(p, "a", encoding="utf-8") as f:
                f.write(node_id + "\n")

    def copy_overwrite(src: str, dst: str) -> None:
        """把 src 目录的内容镜像到 dst（dst 存在则先整个清掉），保证完全覆盖。"""
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    async def before_hook(ctx) -> None:
        # 仅恢复点节点 resuming=True：把 backup 还原回 qa/repo，
        # 抹掉上次崩溃节点的脏写入。
        if ctx.resuming:
            copy_overwrite(qa_backup, qa_dir)
            copy_overwrite(repo_backup, repo_dir)

    async def after_hook(ctx) -> None:
        # 节点成功后：把当前 qa/repo 快照到 backup（完全覆盖）。
        copy_overwrite(qa_dir, qa_backup)
        copy_overwrite(repo_dir, repo_backup)

    async def node1() -> str:
        write_id("node1#0")
        return "n1"

    async def node2(prev: str) -> str:
        write_id("node2#0")
        return f"{prev}->n2"

    async def node3(prev: str) -> str:
        write_id("node3#0")
        return f"{prev}->n3"

    async def node4(prev: str) -> str:
        write_id("node4#0")
        return f"{prev}->n4"

    async def node5(prev: str) -> str:
        write_id("node5#0")
        return f"{prev}->n5"

    flow = Flow(state_path, before_hook=before_hook, after_hook=after_hook)
    h1 = flow.submit(node1)
    h2 = flow.submit(node2, h1)
    h3 = flow.submit(node3, h2)
    h4 = flow.submit(node4, h3)
    h5 = flow.submit(node5, h4)
    await flow.wait_all()
    return await h5

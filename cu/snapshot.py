"""全量 tar 快照与恢复（spec §6）。

快照路径：<job_dir>/snapshots/post-<stage>.tar（无压缩）
内容：整棵 <job_dir>/home/（arcname='home'）
恢复：先删 <job_dir>/home/，再 extractall 至 <job_dir>，重建 home/。
"""
from __future__ import annotations

import os
import shutil
import tarfile

from cu.paths import job_dir, sandbox_home, snapshots_dir


def _snapshot_path(job_id: str, stage: str) -> str:
    return os.path.join(snapshots_dir(job_id), f"post-{stage}.tar")


def save_snapshot(job_id: str, stage: str) -> str:
    """对 <job_dir>/home/ 整树打 tar。返回 tar 绝对路径。"""
    home = sandbox_home(job_id)
    if not os.path.isdir(home):
        raise FileNotFoundError(f"沙箱 home 不存在: {home}")
    snap_dir = snapshots_dir(job_id)
    os.makedirs(snap_dir, exist_ok=True)
    out = _snapshot_path(job_id, stage)
    with tarfile.open(out, "w") as tar:
        tar.add(home, arcname="home")
    return out


def restore_snapshot(job_id: str, stage: str) -> None:
    """用 post-<stage>.tar 覆盖恢复 <job_dir>/home/。"""
    out = _snapshot_path(job_id, stage)
    if not os.path.isfile(out):
        raise FileNotFoundError(f"快照不存在: {out}")
    home = sandbox_home(job_id)
    if os.path.isdir(home):
        shutil.rmtree(home)
    with tarfile.open(out, "r") as tar:
        tar.extractall(job_dir(job_id), filter="data")


def snapshot_exists(job_id: str, stage: str) -> bool:
    return os.path.isfile(_snapshot_path(job_id, stage))


def delete_snapshots(job_id: str) -> None:
    """删除指定 job 的所有快照（用于 delete_job）。"""
    snap_dir = snapshots_dir(job_id)
    if os.path.isdir(snap_dir):
        shutil.rmtree(snap_dir)

"""清理产物树中不应打包的文件。"""
from __future__ import annotations

import os
import shutil
import sys
import time


def clean_artifact_tree(artifact_root: str) -> int:
    """删除 ``.DS_Store``、``._*``、``.gitkeep`` 及名为 ``__MACOSX`` 的目录树。返回删除项计数。"""
    root = os.path.abspath(artifact_root)
    if not os.path.isdir(root):
        raise RuntimeError(f"[compile/clean-artifacts] 目录不存在: {root}")

    deleted = 0
    dirs_to_rm: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        base = os.path.basename(dirpath)
        if base == "__MACOSX":
            dirs_to_rm.append(dirpath)
            continue

        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if fn == ".DS_Store" or fn.startswith("._") or fn == ".gitkeep":
                try:
                    os.remove(fp)
                    deleted += 1
                except OSError:
                    pass

    for d in sorted(dirs_to_rm, key=len, reverse=True):
        try:
            shutil.rmtree(d)
            deleted += 1
        except OSError:
            pass

    return deleted


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 1:
        print("用法: python -m cu.pipeline.clean_artifacts <artifact_root>", file=sys.stderr)
        return 1
    try:
        n = clean_artifact_tree(argv[0])
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[clean_artifacts][{ts}]已清理 {n} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

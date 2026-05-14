from __future__ import annotations

import os
import subprocess


def zip(folder: str, target: str) -> str:
    try:
        subprocess.run(["zip", "-r", target, folder], check=True)
    except Exception as e:
        raise RuntimeError(f"打包失败: {e}") from e
    finally:
        os.remove(target)
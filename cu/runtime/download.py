from __future__ import annotations

import os
import subprocess

def download(url: str, path: str, name: str) -> None:
    folder = os.path.abspath(path)
    os.makedirs(folder, exist_ok=True)

    zip_path = os.path.join(folder, f"{name}.zip")

    try:
        subprocess.run(["wget", "-O", zip_path, url], check=True)
    except Exception as e:
        raise RuntimeError(f"下载失败: {e}") from e
    finally:
        os.remove(zip_path)

    try:
        subprocess.run(["unzip", zip_path, "-d", folder], check=True)
    except Exception as e:
        raise RuntimeError(f"解压失败: {e}") from e
    finally:
        os.remove(zip_path)

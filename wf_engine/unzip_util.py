from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZipFile


class UnsafeArchiveError(ValueError):
    pass


def extract_zip_safely(data: bytes, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = dest_dir.resolve()
    with ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            target = (dest_dir / name).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                raise UnsafeArchiveError(name)
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))

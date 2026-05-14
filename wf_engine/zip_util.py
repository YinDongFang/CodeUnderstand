from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def pack_whitelist_zip(*, parent: Path, include_globs: tuple[str, ...], dest_zip: Path) -> None:
    if not include_globs:
        return
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(dest_zip, "w", ZIP_DEFLATED) as zf:
        for pattern in include_globs:
            for p in parent.glob(pattern):
                if p.is_file():
                    zf.write(p, p.relative_to(parent).as_posix())

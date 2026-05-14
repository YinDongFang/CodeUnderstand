"""Zip read/write helpers (whitelist packing, zip-slip–safe extract)."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

log = logging.getLogger(__name__)


class WhitelistPackError(ValueError):
    """Raised when a whitelist glob matches no files (spec §4 hard fail)."""

    def __init__(self, message: str, *, patterns: tuple[str, ...]) -> None:
        super().__init__(message)
        self.patterns = patterns


class UnsafeArchiveError(ValueError):
    """Archive entry path escapes ``dest_dir`` (zip-slip)."""

    def __init__(self, entry_name: str) -> None:
        super().__init__(f"unsafe archive path: {entry_name!r}")
        self.entry_name = entry_name


def pack_whitelist_zip(
    *,
    parent: Path,
    include_globs: tuple[str, ...],
    dest_zip: Path,
) -> frozenset[str]:
    """Write a zip of files matching ``include_globs`` under ``parent``.

    Each glob must match at least one file. Returns relative POSIX paths archived.
    """
    if not include_globs:
        return frozenset()

    missing = [pat for pat in include_globs if not any(parent.glob(pat))]
    if missing:
        raise WhitelistPackError(
            f"whitelist pattern(s) matched no files: {missing!r}",
            patterns=tuple(missing),
        )

    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for pattern in include_globs:
        paths.extend(p for p in parent.glob(pattern) if p.is_file())

    seen: set[Path] = set()
    ordered_unique: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        ordered_unique.append(p)

    written: set[str] = set()
    with ZipFile(dest_zip, "w", ZIP_DEFLATED) as zf:
        for p in ordered_unique:
            rel = p.relative_to(parent).as_posix()
            zf.write(p, rel)
            written.add(rel)

    return frozenset(written)


def warn_extraneous_workspace_files(
    *,
    node_workdir: Path,
    packed_rel_paths: frozenset[str],
) -> None:
    """Log warning for files under ``node_workdir`` not included in the snapshot (spec §4 soft B)."""
    if not node_workdir.is_dir():
        return
    for path in node_workdir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(node_workdir).as_posix()
        if rel not in packed_rel_paths:
            log.warning(
                "non-whitelist file in node workdir (not packed): %s",
                rel,
            )


def extract_zip_bytes(*, archive_bytes: bytes, dest_dir: Path) -> None:
    """Expand a zip byte payload into ``dest_dir``; reject paths that escape ``dest_dir``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = dest_dir.resolve()
    with ZipFile(io.BytesIO(archive_bytes)) as zf:
        for member_name in zf.namelist():
            target_path = (dest_dir / member_name).resolve()
            try:
                target_path.relative_to(base)
            except ValueError:
                raise UnsafeArchiveError(member_name) from None
            if member_name.endswith("/"):
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(zf.read(member_name))

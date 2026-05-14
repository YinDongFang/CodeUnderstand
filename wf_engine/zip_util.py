from __future__ import annotations

import logging
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

log = logging.getLogger(__name__)


class WhitelistPackError(ValueError):
    """Raised when a whitelist glob matches no files (spec §4 hard fail)."""

    def __init__(self, message: str, *, patterns: tuple[str, ...]) -> None:
        super().__init__(message)
        self.patterns = patterns


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
    for p in node_workdir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(node_workdir).as_posix()
        if rel not in packed_rel_paths:
            log.warning(
                "non-whitelist file in node workdir (not packed): %s",
                rel,
            )

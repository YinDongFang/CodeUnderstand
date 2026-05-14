import io
import tempfile
import zipfile
from pathlib import Path

import pytest

from wf_engine.utils.archives import (
    UnsafeArchiveError,
    WhitelistPackError,
    extract_zip_bytes,
    pack_whitelist_zip,
)


def test_pack_whitelist_includes_only_matches():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_text("A", encoding="utf-8")
        (root / "b.txt").write_text("B", encoding="utf-8")
        z = root / "out.zip"
        pack_whitelist_zip(parent=root, include_globs=("a.txt",), dest_zip=z)
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
        assert names == {"a.txt"}


def test_pack_whitelist_raises_when_pattern_misses():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_text("A", encoding="utf-8")
        z = root / "out.zip"
        with pytest.raises(WhitelistPackError):
            pack_whitelist_zip(parent=root, include_globs=("missing.txt",), dest_zip=z)


def test_extract_rejects_zip_slip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "x")
    buf.seek(0)
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td)
        with pytest.raises(UnsafeArchiveError):
            extract_zip_bytes(archive_bytes=buf.getvalue(), dest_dir=dest)

"""cu.runtime.archive：打包目录。"""
from __future__ import annotations

from zipfile import ZipFile

from cu.runtime.archive import archive


def test_archive_writes_zip(tmp_path):
    root = tmp_path / "code-understand-demo"
    root.mkdir()
    (root / "readme.txt").write_text("hi", encoding="utf-8")
    nested = root / "src"
    nested.mkdir()
    (nested / "a.py").write_text("#", encoding="utf-8")

    zp = archive(str(root))

    assert zp.endswith("code-understand-demo.zip")
    with ZipFile(zp, "r") as zf:
        names = set(zf.namelist())
    assert "code-understand-demo/readme.txt" in names
    assert "code-understand-demo/src/a.py" in names

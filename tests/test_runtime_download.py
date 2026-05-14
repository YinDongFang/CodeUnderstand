"""cu.runtime.download：URL 校验与解压链（网络 IO mock）。"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from cu.runtime.download import download_github_zip


def test_download_github_zip_bad_url_raises():
    with pytest.raises(RuntimeError, match="不符合"):
        download_github_zip("https://example.com/x.zip", "/tmp/p")


def test_download_github_zip_happy_path(tmp_path):
    import zipfile as zf_mod

    projects = tmp_path / "projects"
    projects.mkdir()

    zbuf = io.BytesIO()
    with zf_mod.ZipFile(zbuf, "w") as zfw:
        zfw.writestr("my-repo-main/README.md", "hello")
    zdata = zbuf.getvalue()

    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(zdata)
    cm.__exit__.return_value = None

    url = "https://github.com/org/my-repo/archive/refs/heads/main.zip"
    with patch("cu.runtime.download.urllib.request.urlopen", return_value=cm):
        download_github_zip(url, str(projects))

    readme = projects / "my-repo" / "README.md"
    assert readme.is_file()
    assert readme.read_text(encoding="utf-8") == "hello"

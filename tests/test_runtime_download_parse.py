"""cu.runtime.download URL 解析。"""
from __future__ import annotations

import pytest

from cu.runtime.download import parse_github_archive_zip


def test_parse_github_archive_zip_ok():
    owner, repo, branch = parse_github_archive_zip(
        "https://github.com/acme/widget/archive/refs/heads/main.zip"
    )
    assert owner == "acme"
    assert repo == "widget"
    assert branch == "main"


def test_parse_github_archive_zip_rejects_bad_url():
    with pytest.raises(ValueError):
        parse_github_archive_zip("https://example.com/x.zip")

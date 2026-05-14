import io
import zipfile
from pathlib import Path
import tempfile
import pytest
from wf_engine.utils.unzip_util import UnsafeArchiveError, extract_zip_safely


def test_extract_rejects_zip_slip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "x")
    buf.seek(0)
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td)
        with pytest.raises(UnsafeArchiveError):
            extract_zip_safely(buf.getvalue(), dest)

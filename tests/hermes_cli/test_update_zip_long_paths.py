"""ZIP extraction handles deep trees without an OS policy change and still refuses unsafe members.

The full update under ``LongPathsEnabled = 0`` is proven by ``test_update_zip_long_paths_windows_live``."""
import os
from pathlib import Path
import shutil
import stat
import zipfile

import pytest

from hermes_cli import update_cmd_zip as zip_update


def _extended(path):
    value = os.path.abspath(path)
    return value if value.startswith("\\\\?\\") else "\\\\?\\" + value


def _member():
    return "hermes-agent-main/website/" + "/".join(["nested-docs-" + "x" * 30] * 8) + "/page.md"


@pytest.mark.platforms("windows")
def test_deep_zip_extracts_without_long_path_policy(tmp_path):
    archive = tmp_path / "source.zip"
    destination = tmp_path / "extract"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(_member(), b"new documentation")
    error = None
    try:
        try:
            zip_update._extract_zip_safely(str(archive), str(destination))
        except OSError as exc:
            error = exc
        assert error is None, f"valid deep ZIP extraction failed: {error}"
        assert Path(_extended(destination / _member())).read_bytes() == b"new documentation"
    finally:
        shutil.rmtree(_extended(destination), ignore_errors=True)


@pytest.mark.parametrize("name", ["../outside.txt", "/outside.txt", "link"])
def test_zip_still_rejects_unsafe_members_before_writing(tmp_path, name):
    archive = tmp_path / "unsafe.zip"
    destination = tmp_path / "extract"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("hermes-agent-main/README.md", b"must not be extracted")
        info = zipfile.ZipInfo(name)
        if name == "link":
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"outside")
    with pytest.raises(ValueError):
        zip_update._extract_zip_safely(str(archive), str(destination))
    assert not destination.exists()
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("payload", [None, b"normal documentation"])
def test_empty_and_normal_archives(tmp_path, payload):
    archive = tmp_path / "source.zip"
    destination = tmp_path / "extract"
    with zipfile.ZipFile(archive, "w") as zf:
        if payload is not None:
            zf.writestr("hermes-agent-main/README.md", payload)
    zip_update._extract_zip_safely(str(archive), str(destination))
    if payload is None:
        assert not destination.exists()
    else:
        assert (destination / "hermes-agent-main" / "README.md").read_bytes() == payload

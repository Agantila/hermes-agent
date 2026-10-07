"""Live Windows proof that a ZIP ``hermes update`` applies under ``LongPathsEnabled = 0`` (#129299).

Runs ONLY on a real Windows host whose long-path policy is off (the Windows default). The on-demand
``wine2e/**`` lane (``.github/workflows/windows-venv-e2e.yml``) turns it off in an earlier step, and
``-m "platforms and integration"`` selects this file (the ordinary OS lane has long paths on).

Nothing under test is mocked: a real archive (``git archive``'s commit comment, docs members as deep as
the repo's ``website/i18n/zh-Hans/...``) is downloaded over ``file://`` and the real
``update_cmd_zip`` extract + journaled stage + swap + cleanup runs against an install root laid out
like the Desktop/bootstrap one, so root + member + ``.hermes-update-staging`` exceeds MAX_PATH. The
test reaches its own fixtures through ``\\\\?\\`` spellings it builds itself: plain ``Path.exists``
silently answers False beyond 260 characters.
"""

from __future__ import annotations

import ntpath
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.platforms("windows"), pytest.mark.integration]

MAX_PATH = 260
VERBATIM = "\\\\?\\"
SHA = "8192da90e0afb20010a1c2f5da83db305d05ac5a"
# The repo's deepest member (159 chars) when #129299 was filed.
DEEP = ("website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/skills/bundled/"
        "software-development/software-development-hermes-agent-skill-authoring.md")
SUFFIX = ".hermes-update-staging"


def _long_paths_enabled() -> bool:
    import ctypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.RtlAreLongPathsEnabled.restype = ctypes.c_ubyte
    return bool(ntdll.RtlAreLongPathsEnabled())


def _verbatim(path) -> Path:
    text = ntpath.abspath(os.fspath(path))
    return Path(text if text.startswith(VERBATIM) else VERBATIM + text)


def _write(path: Path, data: str) -> None:
    target = _verbatim(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")


def _install_root(tmp_path: Path) -> Path:
    """``%LOCALAPPDATA%\\hermes\\installs\\<16hex>\\environments\\<32hex>\\workspace``'s shape, padded
    until the staging copy of the deepest member is past MAX_PATH."""
    root = tmp_path / "hermes" / "installs" / ("a" * 16) / "environments" / ("b" * 32) / "workspace"
    while len(str(root)) + 1 + len(DEEP) + len(SUFFIX) <= MAX_PATH + 10:
        root = root.parent / ("p" + root.name)
    root.mkdir(parents=True)
    assert len(str(root / DEEP)) + len(SUFFIX) > MAX_PATH
    return root


@pytest.fixture
def long_paths_off():
    assert not _long_paths_enabled(), "harness: this proof needs LongPathsEnabled = 0"


def test_deep_zip_update_extracts_stages_swaps_and_cleans(tmp_path, monkeypatch, long_paths_off):
    from hermes_cli import main, update_cmd_commit, update_cmd_zip
    from hermes_cli._early_recovery_zip import ZIP_SWAP_JOURNAL

    root = _install_root(tmp_path)
    _write(root / DEEP, "old documentation")
    _write(root / "website" / "old-only.md", "removed upstream")
    _write(root / "pyproject.toml", '[project]\nversion="1.0"\n')
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr(f"hermes-agent-{SHA}/{DEEP}", "new documentation")
        out.writestr(f"hermes-agent-{SHA}/pyproject.toml", '[project]\nversion="2.0"\n')
        out.comment = SHA.encode()
    # The download lands under a deep temp dir too, so extraction itself crosses MAX_PATH.
    downloads = tmp_path / ("d" * 80)
    downloads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(downloads))
    monkeypatch.setattr(main, "PROJECT_ROOT", root)

    update_cmd_commit.begin_update_attempt()
    try:
        assert update_cmd_zip._download_and_swap_zip("main", archive.as_uri(), SHA) == SHA
    finally:
        update_cmd_commit.begin_update_attempt()

    assert _verbatim(root / DEEP).read_text(encoding="utf-8-sig") == "new documentation"
    assert (root / "pyproject.toml").read_text(encoding="utf-8-sig") == '[project]\nversion="2.0"\n'
    assert not _verbatim(root / "website" / "old-only.md").exists(), "the old tree was swapped out"
    leftovers = [p.name for p in _verbatim(root).iterdir() if ".hermes-update-" in p.name
                 and p.name != ZIP_SWAP_JOURNAL + ".lock"]
    assert leftovers == [], f"the swap left its own copies behind: {leftovers}"
    assert not any(_verbatim(downloads).iterdir()), "the extraction dir was not removed"


def test_recovery_drops_a_killed_swaps_deep_staging_tree(tmp_path, long_paths_off):
    """The next launch settles a stage killed mid-copy: its staging tree is past MAX_PATH too."""
    from hermes_cli._early_recovery_zip import (
        ZIP_SWAP_JOURNAL, restore_interrupted_zip_swap, write_zip_swap_journal, zip_entry_identity)

    root = _install_root(tmp_path)
    _write(root / DEEP, "live documentation")
    staging = root / ("website" + SUFFIX)
    _write(staging / DEEP.split("/", 1)[1], "half-copied documentation")
    write_zip_swap_journal(root, "staging", [["website", True, zip_entry_identity(staging), ""]], "0" * 12)

    restore_interrupted_zip_swap(root)

    assert not _verbatim(staging).exists(), "recovery left the killed stage's tree behind"
    assert not (root / ZIP_SWAP_JOURNAL).exists(), "the journal stayed, so every launch retries"
    assert _verbatim(root / DEEP).read_text(encoding="utf-8-sig") == "live documentation"

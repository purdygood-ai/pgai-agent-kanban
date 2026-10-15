"""
test_lib_atomic_write.py
========================
Behavioural tests for pgai_agent_kanban.lib.atomic_write.atomic_write, the
one atomic writer every Python module uses to replace a file's contents.

Covered: the target ends up holding exactly the new content (fresh and
overwritten), no scratch file survives a successful write, an unwritable
target raises OSError and leaves the old content in place, a failure at
the rename step removes the scratch file it created, and preserve_mode keeps
the replaced file's permission bits.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from pgai_agent_kanban.lib.atomic_write import atomic_write


def test_creates_a_new_file_with_exact_content(tmp_path: Path) -> None:
    target = tmp_path / "ledger.md"
    atomic_write(target, "line one\nline two\n")
    assert target.read_text(encoding="utf-8") == "line one\nline two\n"


def test_replaces_existing_content_completely(tmp_path: Path) -> None:
    target = tmp_path / "status.md"
    target.write_text("old content that is longer than the new\n", encoding="utf-8")
    atomic_write(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"


def test_accepts_a_string_path(tmp_path: Path) -> None:
    target = tmp_path / "queue.md"
    atomic_write(str(target), "- [ ] TASK\n")
    assert target.read_text(encoding="utf-8") == "- [ ] TASK\n"


def test_leaves_no_scratch_file_after_success(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    target = work / "status.md"
    target.write_text("old\n", encoding="utf-8")
    atomic_write(target, "new\n", prefix=".probe_tmp_")
    assert sorted(p.name for p in work.iterdir()) == ["status.md"]


def test_unicode_content_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    atomic_write(target, "status — done ✓\n")
    assert target.read_text(encoding="utf-8") == "status — done ✓\n"


def test_read_only_directory_raises_oserror_and_keeps_old_content(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked / "status.md"
    target.write_text("old\n", encoding="utf-8")
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OSError):
            atomic_write(target, "new\n")
        assert target.read_text(encoding="utf-8") == "old\n"
        assert sorted(p.name for p in locked.iterdir()) == ["status.md"]
    finally:
        locked.chmod(stat.S_IRWXU)


def test_parent_that_is_a_file_raises_oserror(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "plain-file"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        atomic_write(not_a_dir / "status.md", "new\n")


def test_missing_parent_directory_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        atomic_write(tmp_path / "absent" / "status.md", "new\n")
    assert not (tmp_path / "absent").exists()


def test_failed_rename_removes_its_scratch_file(tmp_path: Path) -> None:
    # A non-empty directory at the target path makes os.replace fail after
    # the scratch file has been written.
    work = tmp_path / "work"
    work.mkdir()
    target = work / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("k", encoding="utf-8")
    with pytest.raises(OSError):
        atomic_write(target, "new\n", prefix=".probe_tmp_")
    assert sorted(p.name for p in work.iterdir()) == ["occupied"]
    assert (target / "keep.txt").read_text(encoding="utf-8") == "k"


def test_written_bytes_are_visible_through_a_fresh_descriptor(tmp_path: Path) -> None:
    target = tmp_path / "ledger.jsonl"
    atomic_write(target, '{"a": 1}\n')
    fd = os.open(target, os.O_RDONLY)
    try:
        assert os.read(fd, 100) == b'{"a": 1}\n'
    finally:
        os.close(fd)


def test_default_write_gives_the_replacement_the_scratch_files_0600(tmp_path: Path) -> None:
    target = tmp_path / "kanban.cfg"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o640)
    atomic_write(target, "new\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_preserve_mode_keeps_the_replaced_files_permission_bits(tmp_path: Path, mode: int) -> None:
    work = tmp_path / "work"
    work.mkdir()
    target = work / "project.cfg"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(mode)
    atomic_write(target, "new\n", prefix=".probe_tmp_", preserve_mode=True)
    assert target.read_text(encoding="utf-8") == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert sorted(p.name for p in work.iterdir()) == ["project.cfg"]


def test_preserve_mode_on_a_new_file_writes_0600(tmp_path: Path) -> None:
    target = tmp_path / "fresh.cfg"
    atomic_write(target, "new\n", preserve_mode=True)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

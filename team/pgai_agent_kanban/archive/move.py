"""
move.py — move one file or directory between the live tree and the archive.

``move_path(source, destination)`` is the single move every archive and
restore step uses, for item files, task directories and training logs:

- The destination must not exist. An existing destination is never
  replaced or merged into; the move is refused with
  :class:`ArchiveMoveError` and nothing changes.
- The destination's parent directories are created as needed.
- On one filesystem the move is ``os.replace`` — a single rename, so the
  item is at exactly one of the two paths at every instant.
- Across filesystems (``os.replace`` fails with ``EXDEV``) the source is
  copied to a hidden staging name beside the destination, every copied
  file and directory is fsynced, the staging copy is renamed onto the
  destination, the destination's directory is fsynced, and only then is
  the source removed. An interruption before the rename leaves the source
  untouched and a staging copy the next move of the same item clears; an
  interruption after it leaves the item at both paths, which the next
  move refuses as an existing destination rather than guessing which copy
  is right.

Nothing here deletes an item without a complete copy of it in place first.

Public API
----------
``move_path(source, destination) -> str``
    Move and return how: :data:`MOVED_BY_RENAME` or :data:`MOVED_BY_COPY`.

``ArchiveMoveError``
    The dual-channel refusal for a destination that already exists, and
    the wrapper for any filesystem error during a move.
"""

from __future__ import annotations

import errno
import os
import pathlib
import shlex
import shutil
from typing import Union

from pgai_agent_kanban.lib.error_contract import format_error_contract

__all__ = ["ArchiveMoveError", "MOVED_BY_COPY", "MOVED_BY_RENAME", "move_path"]

PathLike = Union[str, "os.PathLike[str]"]

#: ``move_path`` results: how the item reached its destination.
MOVED_BY_RENAME = "rename"
MOVED_BY_COPY = "copy"

_STAGING_SUFFIX = ".archive-staging"

#: The rename a move tries first. A test replaces it with one that raises
#: ``EXDEV`` to drive the cross-filesystem copy on a single filesystem.
_rename = os.replace


class ArchiveMoveError(Exception):
    """A move did not happen; the source is still in place.

    ``str(exc)`` is the three-line dual-channel block.

    Attributes:
        error:       Stable machine key: ``archive.destination_exists`` or
                     ``archive.move_failed``.
        message:     What was not moved, where to, and why.
        remedy:      A runnable command to inspect the two paths.
        source:      The path that was to move.
        destination: Where it was to go.
    """

    def __init__(
        self, error: str, message: str, remedy: str, source: pathlib.Path, destination: pathlib.Path
    ) -> None:
        self.error = error
        self.message = message
        self.remedy = remedy
        self.source = source
        self.destination = destination
        super().__init__(format_error_contract(error, message, remedy))


def _fsync_path(path: pathlib.Path) -> None:
    """fsync a file or directory by path."""
    fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() and not path.is_symlink() else 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: pathlib.Path) -> None:
    """fsync every regular file and directory under *root*, and *root* itself."""
    if root.is_symlink() or root.is_file():
        if root.is_file() and not root.is_symlink():
            _fsync_path(root)
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        base = pathlib.Path(dirpath)
        for name in filenames:
            path = base / name
            if path.is_file() and not path.is_symlink():
                _fsync_path(path)
        _fsync_path(base)


def _remove(path: pathlib.Path) -> None:
    """Remove a file, symlink or directory tree at *path*."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_then_remove(source: pathlib.Path, destination: pathlib.Path) -> None:
    """The cross-filesystem move: copy, fsync, rename into place, then remove the source."""
    staging = destination.with_name(f".{destination.name}{_STAGING_SUFFIX}")
    if os.path.lexists(staging):
        # A staging copy left by an interrupted earlier move of this item:
        # never at the destination path, and the source is still complete.
        _remove(staging)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, staging, symlinks=True)
    else:
        shutil.copy2(source, staging, follow_symlinks=False)
    _fsync_tree(staging)
    os.replace(staging, destination)
    _fsync_path(destination.parent)
    _remove(source)
    _fsync_path(source.parent)


def move_path(source: PathLike, destination: PathLike) -> str:
    """Move *source* to *destination*, which must not exist.

    Args:
        source:      An existing file or directory.
        destination: The path it moves to; parent directories are created.

    Returns:
        :data:`MOVED_BY_RENAME` or :data:`MOVED_BY_COPY`.

    Raises:
        ArchiveMoveError: *destination* already exists (nothing changed),
            or a filesystem error stopped the move (the source is intact
            unless the error came from removing it after a complete copy
            was in place).
    """
    src = pathlib.Path(source)
    dst = pathlib.Path(destination)
    if os.path.lexists(dst):
        # CONVERT (docs/blocking-site-census.md): an item already at the
        # destination is never overwritten or merged. The move is refused,
        # the source stays where it was, and the archive run or restore
        # reports a failure for this item; no release or wake is held.
        # CENSUS-ANCHOR: archive-move-destination-exists-01
        raise ArchiveMoveError(
            "archive.destination_exists",
            f"archive: not moving {src} because {dst} already exists; "
            "an existing item is never overwritten, so both copies were left as they are.",
            f"diff -r {shlex.quote(str(src))} {shlex.quote(str(dst))}",
            src,
            dst,
        )
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            _rename(src, dst)
            return MOVED_BY_RENAME
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
        _copy_then_remove(src, dst)
        return MOVED_BY_COPY
    except OSError as exc:
        raise ArchiveMoveError(
            "archive.move_failed",
            f"archive: moving {src} to {dst} failed: {exc}.",
            f"ls -ld {shlex.quote(str(src))} {shlex.quote(str(dst.parent))}",
            src,
            dst,
        ) from exc

"""
atomic_write.py — the one atomic file writer for the kanban's Python code.

Every module that replaces a file's whole contents (status.md, queue files,
ledgers, bug reports, release-state.md) calls ``atomic_write`` rather than
carrying its own temp-file-and-rename copy.

How it writes
-------------
1. A scratch file is created with ``tempfile.mkstemp`` in the SAME directory
   as the target, so the final rename never crosses a filesystem boundary.
2. The content is written, flushed, and ``os.fsync``-ed before the file is
   closed, so the bytes are on disk before they become visible.
3. ``os.replace`` renames the scratch file over the target. POSIX guarantees
   that rename is atomic: a concurrent reader sees either the old complete
   file or the new complete file, never a partial one.

On any failure the scratch file is removed and the ``OSError`` propagates
unchanged. Callers that report failures through their own error type catch
``OSError`` at the call site and wrap it there.

The scratch file is created by ``mkstemp`` (mode 0600 under the usual
umask), and the rename carries that mode onto the target. With
``preserve_mode=True`` the scratch file is first given the permission bits
of the file it replaces (when that file exists), so an operator-owned file
at 0640 stays at 0640; otherwise the target's previous mode is not copied.

Public API:
    atomic_write(target, content, *, prefix=..., encoding="utf-8",
                 preserve_mode=False) -> None

Usage:
    from pgai_agent_kanban.lib.atomic_write import atomic_write

    try:
        atomic_write(status_file, new_text, prefix=".task_status_tmp_")
    except OSError as exc:
        raise MyError(f"failed writing {status_file}: {exc}") from exc
"""

from __future__ import annotations

import os
import stat
import tempfile
from typing import Union

__all__ = ["DEFAULT_PREFIX", "atomic_write"]

#: Scratch-file name prefix used when the caller does not name its own.
#: Leading dot keeps the scratch file out of casual directory listings.
DEFAULT_PREFIX = ".atomic_write_tmp_"

_PathLike = Union[str, "os.PathLike[str]"]


def atomic_write(
    target: _PathLike,
    content: str,
    *,
    prefix: str = DEFAULT_PREFIX,
    encoding: str = "utf-8",
    preserve_mode: bool = False,
) -> None:
    """Replace *target*'s contents with *content* atomically.

    The parent directory of *target* must already exist; a missing directory
    is an ``OSError`` like any other write failure.

    Args:
        target:   Destination path (``str`` or ``os.PathLike``). It need not
                  exist yet; if it does, it is replaced.
        content:  Text to write.
        prefix:   Scratch-file name prefix inside the target's directory.
                  Callers pass a module-specific prefix so a scratch file
                  left by a killed process names its writer.
        encoding: Text encoding for *content*.
        preserve_mode: When True and *target* exists, the new file keeps
                  *target*'s permission bits instead of the scratch file's
                  0600. A *target* that does not exist yet gets 0600 either
                  way.

    Raises:
        OSError: the scratch file could not be created, written, synced, or
            renamed over *target*. The scratch file has been removed and
            *target* is untouched.
    """
    target_str = os.fspath(target)
    target_dir = os.path.dirname(os.path.abspath(target_str))
    tmp_path = None
    try:
        target_mode = None
        if preserve_mode:
            try:
                target_mode = stat.S_IMODE(os.stat(target_str).st_mode)
            except FileNotFoundError:
                pass
        fd, tmp_path = tempfile.mkstemp(prefix=prefix, dir=target_dir)
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            if target_mode is not None:
                os.fchmod(handle.fileno(), target_mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target_str)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

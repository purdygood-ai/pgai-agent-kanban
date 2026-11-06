"""
sentinel.py — the one writer of a project's HALT-AFTER sentinel file.

Two callers rewrite the sentinel: the operator verb that arms it
(``pgai_agent_kanban.ops.write.halt_after``) and the bare ``rc`` drain step
that pins it to the in-flight version (``drain.check_drain``). Both call
``write_halt_after``, so the file's format and write mechanics live here
alone.

The file holds one line: the token followed by a newline (``rc\\n``,
``coder\\n``, ``rc:v1.2.3\\n``). It is replaced through the shared atomic
writer (pgai_agent_kanban.lib.atomic_write), so a reader (the wake loop, the
RC-close gate) or the other writer touching it mid-write sees the old
complete token or the new one, never an empty or partial file. An existing sentinel keeps its
permission bits.

Provenance records and rc-gate latch handling stay with the callers: each
knows its own ``op`` and kanban root, and only the operator verb clears a
latch.

Public API:
    write_halt_after(project_root, token) -> bool
"""

from __future__ import annotations

import os
import pathlib

from pgai_agent_kanban.lib.atomic_write import atomic_write

__all__ = ["HALT_AFTER_FILENAME", "write_halt_after"]

#: The sentinel's file name inside a project root (or the kanban root).
HALT_AFTER_FILENAME = "HALT-AFTER"

#: Scratch-file prefix inside the sentinel's directory; the leading dot keeps
#: it out of casual listings and it never matches ``HALT*``.
_TMP_PREFIX = ".halt_after_tmp_"


def write_halt_after(project_root: str | os.PathLike[str], token: str) -> bool:
    """Replace ``<project_root>/HALT-AFTER`` with ``token + "\\n"``, atomically.

    Args:
        project_root: Directory holding the sentinel; it must already exist.
        token:        Drain token to write (``rc``, ``coder``, ``rc:v1.2.3``,
                      ...). Written as given; the caller validates it.

    Returns:
        True when the file's bytes changed (it was absent, unreadable, or held
        other bytes), False when it already held exactly this token. Callers
        use it to decide whether to record provenance.

    Raises:
        OSError: the sentinel could not be written. The previous file, if
            any, is left intact and no scratch file remains.
    """
    path = pathlib.Path(project_root) / HALT_AFTER_FILENAME
    content = f"{token}\n"
    try:
        previous: bytes | None = path.read_bytes()
    except OSError:
        previous = None
    atomic_write(path, content, prefix=_TMP_PREFIX, encoding="utf-8", preserve_mode=True)
    return previous != content.encode("utf-8")

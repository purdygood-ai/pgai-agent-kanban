"""
sentinel_provenance.py — who wrote a pause or posture sentinel, and when.

The three sentinels (HALT, HALT-AFTER, DEFER-LOW-BUGS) carry only what their
readers parse -- an empty file, a drain token, a campaign target -- so
nothing inside one says who armed it. A sentinel that appears on the live
root while a gated suite runs therefore looks the same whether a fixture
leaked it or the operator armed it by hand. This module keeps the answer
OUTSIDE the sentinel, in one append-only log under the kanban root, so no
sentinel's bytes and no existing reader of them change.

Where
-----
    <kanban_root>/logs/sentinel-writes.log      (LOG_RELPATH)

Format
------
One line per write that changed a sentinel -- including a write that
removed one -- appended once with O_APPEND and never rewritten. Fields are
tab-separated ``key=value`` pairs, always in this order (FIELDS):

    time     ISO-8601 local time with UTC offset, seconds precision
    epoch    the same instant in whole Unix seconds
    op       the writer: halt, halt_global, halt_after or defer (the ops
             verbs); drain_capture (the HALT-AFTER drain step rewriting a
             bare ``rc`` token to the in-flight ``rc:vX.Y.Z``); promote (the
             HALT-AFTER promotion creating HALT and removing HALT-AFTER)
    path     absolute path of the sentinel that was written or removed
    sha256   digest of the bytes the writer left in it, or ``absent``
             (ABSENT_DIGEST) when the write removed the sentinel
    entry    the writing process's command line
    pid      the writing process id
    ppid     its parent's process id
    parent   the parent's command line -- for the ops CLI shim this is the
             operator script that called it, e.g. ``bash .../halt-after.sh
             --project NAME``
    user     the effective user name
    test     the pytest node id when the write ran under pytest
             (``PYTEST_CURRENT_TEST`` in the writer's environment), else ``-``

A value never contains a tab or a newline (each becomes a space) and is
capped at VALUE_CAP characters; ``-`` stands for a value that could not be
determined. Example line (tabs shown as ``<TAB>``)::

    time=2026-10-09T01:30:12+00:00<TAB>epoch=1791509412<TAB>op=halt_after<TAB>
    path=/home/op/pgai_agent_kanban/projects/demo/HALT-AFTER<TAB>sha256=...<TAB>
    entry=python3 -m pgai_agent_kanban.ops halt_after ... rc<TAB>pid=4242<TAB>
    ppid=4240<TAB>parent=bash .../scripts/halt-after.sh --project demo<TAB>
    user=op<TAB>test=-

Reader
------
``team/scripts/lib/sentinel_leak_check.sh`` reads the log after a gated
suite: a sentinel added or changed during the run whose latest record is
newer than the pre-suite snapshot and carries ``test=-`` is narrated as an
operator or agent write instead of a sentinel the suite left behind; one
whose record names a test is still reported, naming that test. A sentinel
removed during the run is attributed the same way when its latest record
says ``sha256=absent``.

Kanban root
-----------
The ops verbs know their kanban root; the HALT-AFTER drain and promote steps
are handed only the scope directory the sentinel lives in. For those,
:func:`kanban_root_for_scope` maps a project scope
(``<kanban_root>/projects/<name>``) to its kanban root and treats any other
directory -- a root-scope sentinel, or a single-project install -- as the
kanban root itself.

Failure
-------
:func:`record_sentinel_write` never raises. A record that cannot be appended
is narrated as one warning line on stderr and the caller carries on -- the
sentinel itself is already written, and provenance never blocks it.

Public API:
    LOG_RELPATH, FIELDS, VALUE_CAP, ABSENT_DIGEST
    log_path(kanban_root) -> Path
    kanban_root_for_scope(scope_root) -> Path
    record_sentinel_write(kanban_root, sentinel_path, op) -> bool
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import sys
from pathlib import Path
from typing import Optional, Union

__all__ = [
    "ABSENT_DIGEST",
    "FIELDS",
    "LOG_RELPATH",
    "VALUE_CAP",
    "kanban_root_for_scope",
    "log_path",
    "record_sentinel_write",
]

#: The log's location relative to the kanban root.
LOG_RELPATH = "logs/sentinel-writes.log"

#: Field names, in the order every record line carries them.
FIELDS = (
    "time", "epoch", "op", "path", "sha256", "entry",
    "pid", "ppid", "parent", "user", "test",
)

#: Longest value a field keeps; longer values are cut to this many characters.
VALUE_CAP = 512

#: The ``sha256`` value of a record whose write removed the sentinel.
ABSENT_DIGEST = "absent"

_UNKNOWN = "-"

#: The directory that holds one subdirectory per project under a kanban root.
_PROJECTS_DIRNAME = "projects"


def log_path(kanban_root: Union[str, "os.PathLike[str]"]) -> Path:
    """Return the sentinel-write log path for *kanban_root*."""
    return Path(kanban_root) / LOG_RELPATH


def kanban_root_for_scope(scope_root: Union[str, "os.PathLike[str]"]) -> Path:
    """Return the kanban root whose log records a write in *scope_root*.

    A project scope, ``<kanban_root>/projects/<name>``, maps to
    ``<kanban_root>``. Any other directory is a kanban root itself: the
    root-scope sentinels live there, and a single-project install keeps its
    project at the root. Pure path arithmetic -- nothing is read -- so it
    never raises for a path-like argument.
    """
    scope = Path(scope_root)
    if scope.parent.name == _PROJECTS_DIRNAME and scope.parent != scope:
        return scope.parent.parent
    return scope


def _clean(value: object) -> str:
    """One field value: tabs and newlines become spaces, capped, never empty."""
    text = str(value)
    for ch in ("\t", "\r", "\n", "\0"):
        text = text.replace(ch, " ")
    text = text.strip()[:VALUE_CAP]
    return text or _UNKNOWN


def _cmdline(pid: int) -> Optional[str]:
    """The command line of process *pid* from /proc, or None when unreadable."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]
    return " ".join(parts) or None


def _user() -> str:
    """The effective user name, or ``-`` when it cannot be resolved."""
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError, OSError):
        return os.environ.get("USER", "") or _UNKNOWN


def _digest(path: Path) -> str:
    """sha256 of *path*'s bytes; ``absent`` when no file is there (the write
    removed it); ``-`` when it is there but cannot be read."""
    if not path.exists():
        return ABSENT_DIGEST
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return _UNKNOWN


def _record_line(sentinel_path: Path, op: str) -> str:
    """Build one record line (with its trailing newline) for a write just made."""
    now = _dt.datetime.now().astimezone()
    pid = os.getpid()
    ppid = os.getppid()
    entry = _cmdline(pid) or " ".join([sys.executable, *sys.argv])
    values = {
        "time": now.isoformat(timespec="seconds"),
        "epoch": int(now.timestamp()),
        "op": op,
        "path": sentinel_path,
        "sha256": _digest(sentinel_path),
        "entry": entry,
        "pid": pid,
        "ppid": ppid,
        "parent": _cmdline(ppid) or _UNKNOWN,
        "user": _user(),
        "test": os.environ.get("PYTEST_CURRENT_TEST", "") or _UNKNOWN,
    }
    return "\t".join(f"{name}={_clean(values[name])}" for name in FIELDS) + "\n"


def record_sentinel_write(
    kanban_root: Union[str, "os.PathLike[str]"],
    sentinel_path: Union[str, "os.PathLike[str]"],
    op: str,
) -> bool:
    """Append one provenance record for a sentinel write that just happened.

    Call it only after the sentinel write succeeded, and only when that
    write changed the file, so the log names real changes and a repeated
    identical arm leaves the tree untouched. A write that removed the
    sentinel is recorded the same way, after the removal; its record's
    ``sha256`` reads ``absent``.

    Args:
        kanban_root:   The kanban root whose ``logs/`` holds the log
                       (:func:`kanban_root_for_scope` derives it from a
                       sentinel's scope directory).
        sentinel_path: The sentinel file that was written or removed.
        op:            The writer's name (``halt``, ``halt_global``,
                       ``halt_after``, ``defer``, ``drain_capture`` or
                       ``promote``).

    Returns:
        True when the record was appended; False when it could not be, in
        which case one warning line naming the log and the cause has been
        printed to stderr. Never raises.
    """
    target = log_path(kanban_root)
    sentinel = Path(sentinel_path)
    try:
        line = _record_line(sentinel, op).encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except Exception as exc:  # noqa: BLE001 -- provenance must never block the sentinel write
        try:
            print(
                f"{op}: WARNING: {sentinel} was written, but its provenance record "
                f"could not be appended to {target}: {exc}. The sentinel write stands; "
                f"check that {target.parent} is writable so the next write is recorded.",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001 -- a closed stderr must not fail the write either
            pass
        return False

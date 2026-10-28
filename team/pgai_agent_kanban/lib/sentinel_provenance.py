"""
sentinel_provenance.py — who wrote a pause or posture sentinel, and when.

The three sentinels (HALT, HALT-AFTER, DEFER-LOW-BUGS) carry only what their
readers parse -- an empty file, a drain token, a campaign target -- so
nothing inside an operator's sentinel says who armed it. (A HALT the chain
writes names its writer in its body instead; see
pgai_agent_kanban.overwatch.halt_provenance.) A sentinel that appears on the
live root while a gated suite runs therefore looks the same whether a
fixture leaked it or the operator armed it by hand. This module keeps the
answer OUTSIDE the sentinel, in one append-only log under the kanban root,
so no sentinel's bytes and no existing reader of them change.

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
             verbs); unhalt (the ops verb removing a project HALT);
             drain_capture (the HALT-AFTER drain step rewriting a
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

Cause
-----
:func:`sentinel_cause` answers "did an operator raise this sentinel, or the
chain?" from the log alone, reading and never writing it. The cause is
``operator`` (CAUSE_OPERATOR) when the latest record for that sentinel
path, at or before the time asked about, has an op in OPERATOR_OPS --
``halt``, ``halt_global``, ``halt_after``, ``drain_capture`` or
``promote``; otherwise it is ``chain`` (CAUSE_CHAIN), which is also the
answer when the log is absent or unreadable or has no record for the path.
The chain's own HALT writers do not record here, so "no operator record"
reads as the chain. An operator record must still describe the file: asked
about now (no time given), a record whose ``sha256`` names other bytes than
the sentinel holds, or names a file that has since gone, is a record of an
earlier sentinel -- the operator's HALT was cleared and the chain wrote
this one -- so the answer is ``chain``. ``unhalt`` records its removal
(``sha256=absent``), so a cleared operator HALT is never the latest record
of a later chain HALT either. A HALT directly under the kanban root -- the
fleet-scoped HALT, whose only writers are operators -- is always
``operator`` without consulting the log. Records are matched on the
normalised absolute path and ordered by ``epoch`` (``time`` when ``epoch``
is missing), the later line winning a tie; a line that does not parse is
skipped. The two values are the blocked ledger's ``cause`` vocabulary
(pgai_agent_kanban.metrics.blocked_ledger VALID_CAUSES).

The lookup underneath is public for other readers that weigh a record
differently: :func:`latest_sentinel_record` returns the latest record's
fields for a sentinel path (matched and ordered exactly as above), and
:func:`record_describes_sentinel` says whether that record's ``sha256``
still matches the file. ``pgai_agent_kanban.overwatch.halt_provenance``
uses them to tell a chain HALT from an operator HALT, where ``promote``
counts as the chain.

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
    CAUSE_OPERATOR, CAUSE_CHAIN, OPERATOR_OPS
    sentinel_cause(sentinel_path, at=None, kanban_root=None) -> str
    latest_sentinel_record(sentinel_path, at=None, kanban_root=None) -> dict | None
    record_describes_sentinel(record, sentinel_path) -> bool

Usage (CLI) -- the cause, for bash callers (through pp_run_ops):
    python3 -m pgai_agent_kanban.lib.sentinel_provenance cause SENTINEL_PATH \\
        [--at ISO] [--kanban-root DIR]

  Prints ``operator`` or ``chain`` and exits 0; reads only. Exit 2 is a
  usage error (including an --at that is not ISO-8601).
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
    "CAUSE_CHAIN",
    "CAUSE_OPERATOR",
    "FIELDS",
    "LOG_RELPATH",
    "OPERATOR_OPS",
    "VALUE_CAP",
    "kanban_root_for_scope",
    "latest_sentinel_record",
    "log_path",
    "record_describes_sentinel",
    "record_sentinel_write",
    "sentinel_cause",
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

#: :func:`sentinel_cause` results -- the blocked ledger's ``cause`` values.
CAUSE_OPERATOR = "operator"
CAUSE_CHAIN = "chain"

#: Ops whose record makes a sentinel operator-caused: the operator verbs and
#: the HALT-AFTER drain and promotion steps an operator's HALT-AFTER drives.
OPERATOR_OPS = frozenset({"halt", "halt_global", "halt_after", "drain_capture", "promote"})

#: The fleet-scoped HALT's file name, directly under the kanban root.
_HALT_NAME = "HALT"


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
                       ``halt_after``, ``defer``, ``unhalt``,
                       ``drain_capture`` or ``promote``).

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


def _epoch_of(value: Union[None, int, float, str, _dt.datetime]) -> Optional[float]:
    """*value* as Unix seconds: None stays None (no upper bound); a number
    is taken as epoch seconds; a datetime (naive read as local time, as
    the log's own ``time`` field is written) or an ISO-8601 string is
    converted. Raises ValueError for a string that is not ISO-8601."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise ValueError(
            f"at={value!r} is not an ISO-8601 time; pass e.g. "
            "'2026-10-09T01:30:12+00:00', a datetime, or epoch seconds"
        ) from None


def _record_fields(line: str) -> dict:
    """One log line's ``key=value`` pairs; pairs without ``=`` are skipped."""
    fields = {}
    for part in line.rstrip("\r\n").split("\t"):
        key, sep, value = part.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _record_epoch(fields: dict) -> Optional[float]:
    """A record's instant from ``epoch``, else ``time``; None when neither
    parses."""
    try:
        return float(fields["epoch"])
    except (KeyError, ValueError):
        pass
    try:
        return _epoch_of(fields.get("time") or "")
    except ValueError:
        return None


def sentinel_cause(
    sentinel_path: Union[str, "os.PathLike[str]"],
    at: Union[None, int, float, str, _dt.datetime] = None,
    *,
    kanban_root: Union[None, str, "os.PathLike[str]"] = None,
) -> str:
    """Return who caused the sentinel at *sentinel_path*: CAUSE_OPERATOR
    or CAUSE_CHAIN. See the module docstring's "Cause" section for the rule.

    Args:
        sentinel_path: The sentinel file, e.g. ``<kanban_root>/projects/
                       <name>/HALT``.
        at:            The time asked about -- epoch seconds, a datetime or
                       an ISO-8601 string; records after it are ignored.
                       None (the default) considers every record.
        kanban_root:   The kanban root whose ``logs/`` holds the log; when
                       None it is derived from the sentinel's directory with
                       :func:`kanban_root_for_scope`.

    Returns:
        ``operator`` or ``chain``. Reads only; never writes. An absent or
        unreadable log, or no record for the path, reads as ``chain``.

    Raises:
        ValueError: *at* is a string that is not ISO-8601.
    """
    sentinel = Path(os.path.normpath(os.path.abspath(sentinel_path)))
    root = _resolve_root(sentinel, kanban_root)
    if sentinel.name == _HALT_NAME and sentinel.parent == root:
        _epoch_of(at)  # an --at that is not ISO-8601 is still refused
        return CAUSE_OPERATOR

    record = latest_sentinel_record(sentinel, at, kanban_root=root)
    if record is None:
        return CAUSE_CHAIN
    if record.get("op", "").strip().lower() not in OPERATOR_OPS:
        return CAUSE_CHAIN
    if at is None and not _record_describes(record.get("sha256", "").strip().lower(), sentinel):
        return CAUSE_CHAIN
    return CAUSE_OPERATOR


def _resolve_root(sentinel: Path, kanban_root: Union[None, str, "os.PathLike[str]"]) -> Path:
    """The normalised kanban root: *kanban_root* when given, else derived
    from the sentinel's own directory (:func:`kanban_root_for_scope`)."""
    if kanban_root is not None:
        return Path(os.path.normpath(os.path.abspath(kanban_root)))
    return kanban_root_for_scope(sentinel.parent)


def latest_sentinel_record(
    sentinel_path: Union[str, "os.PathLike[str]"],
    at: Union[None, int, float, str, _dt.datetime] = None,
    *,
    kanban_root: Union[None, str, "os.PathLike[str]"] = None,
) -> Optional[dict]:
    """Return the latest sentinel-writes record for *sentinel_path*, or None.

    Records are matched on the normalised absolute path and ordered by
    ``epoch`` (``time`` when ``epoch`` is missing), the later line winning
    a tie; a line that does not parse, or has no usable time, is skipped.
    An absent or unreadable log reads as no record. Reads only; never
    writes.

    Args:
        sentinel_path: The sentinel file the records name.
        at:            Ignore records after this time (epoch seconds, a
                       datetime or an ISO-8601 string); None considers all.
        kanban_root:   The kanban root whose ``logs/`` holds the log; when
                       None it is derived with :func:`kanban_root_for_scope`.

    Returns:
        The record's ``key=value`` fields as a dict, or None when the log
        has no record for the path.

    Raises:
        ValueError: *at* is a string that is not ISO-8601.
    """
    limit = _epoch_of(at)
    sentinel = Path(os.path.normpath(os.path.abspath(sentinel_path)))
    root = _resolve_root(sentinel, kanban_root)
    wanted = str(sentinel)
    latest_key: Optional[tuple] = None
    latest: Optional[dict] = None
    try:
        with open(log_path(root), "r", encoding="utf-8", errors="replace") as handle:
            for lineno, line in enumerate(handle):
                fields = _record_fields(line)
                path = fields.get("path")
                if not path or os.path.normpath(path) != wanted:
                    continue
                epoch = _record_epoch(fields)
                if epoch is None or (limit is not None and epoch > limit):
                    continue
                key = (epoch, lineno)
                if latest_key is None or key > latest_key:
                    latest_key = key
                    latest = fields
    except OSError:
        return None
    return latest


def record_describes_sentinel(record: dict, sentinel_path: Union[str, "os.PathLike[str]"]) -> bool:
    """Whether *record*'s ``sha256`` still describes the file at *sentinel_path*.

    A record of a removal (``sha256=absent``) describes a sentinel that is
    gone; a digest that could not be taken (``-``, or blank) proves nothing
    either way and counts as describing it.
    """
    return _record_describes(str(record.get("sha256", "")).strip().lower(), Path(sentinel_path))


def _record_describes(recorded_digest: str, sentinel: Path) -> bool:
    """Whether a record's ``sha256`` still describes *sentinel* as it is now.

    A digest that could not be taken (``-``, or a blank field) proves
    nothing either way and counts as describing it.
    """
    if not recorded_digest or recorded_digest == _UNKNOWN:
        return True
    return _digest(sentinel) == recorded_digest


def _at_arg(value: str) -> str:
    """argparse type for ``--at``: an ISO-8601 time, refused as a usage error otherwise."""
    import argparse

    try:
        _epoch_of(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def main(argv: Optional[list] = None) -> int:
    """Run the sentinel-provenance CLI: ``cause SENTINEL_PATH`` prints
    ``operator`` or ``chain`` (see the module docstring's "Cause" section).
    Exit codes: 0 answered; 2 usage error."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.sentinel_provenance",
        description="Read the sentinel-writes log. 'cause' prints who raised a sentinel: operator or chain.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    cause_p = sub.add_parser(
        "cause",
        help="Print operator or chain for a sentinel, from the sentinel-writes log (reads only).",
    )
    cause_p.add_argument("sentinel_path", help="the sentinel file, e.g. <kanban_root>/projects/<name>/HALT")
    cause_p.add_argument(
        "--at",
        type=_at_arg,
        default=None,
        help="ignore records after this ISO-8601 time, e.g. 2026-10-09T01:30:12+00:00 (default: every record)",
    )
    cause_p.add_argument(
        "--kanban-root",
        default=None,
        help="the kanban root whose logs/ holds the log (default: derived from the sentinel's directory)",
    )
    args = parser.parse_args(argv)
    print(sentinel_cause(args.sentinel_path, args.at, kanban_root=args.kanban_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())

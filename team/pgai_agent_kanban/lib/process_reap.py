#!/usr/bin/env python3
"""
process_reap.py — find and stop the processes a finished run left behind.

A process an agent starts in the background (``cmd &``, ``nohup``, a tool
in a session of its own) outlives the agent and re-parents to PID 1, where
no process-group signal reaches it. This module finds such processes by
what they carry rather than by who their parent is, and stops them.

Two ways to name them:

  * ``--env NAME=VALUE`` -- every process whose environment holds exactly
    ``NAME=VALUE``. The wake exports a unique ``PGAI_AGENT_RUN_ID`` into the
    agent run it launches, every process the run starts inherits it, and no
    other process on the host has that value.
  * ``--cwd-under DIR`` -- every process whose working directory is DIR or
    inside it: what still runs in a scratch directory about to be removed.

Never signalled: this process and its ancestors, PID 1, and any process
whose ``/proc`` entry this account cannot read (another user's). With
``--skip-terminals`` a process attached to a controlling terminal -- an
operator's interactive shell -- is left running and reported ``kept``.

Stopping is SIGTERM to every match, a wait of up to ``--grace`` seconds,
SIGKILL to whatever is left, and a second wait of up to ``--grace``
seconds. Before every signal the process's start time is read again, so a
pid the kernel has recycled to an unrelated process is never signalled.

Usage:
    python3 -m pgai_agent_kanban.lib.process_reap (--env NAME=VALUE | --cwd-under DIR)
        [--grace SECONDS] [--skip-terminals] [--dry-run]

Output: one line per matched process, on stdout:

    pid=<pid> outcome=<outcome> cwd=<cwd> command=<command line>

where outcome is ``terminated`` (gone after SIGTERM), ``killed`` (gone
after SIGKILL), ``survived`` (still present after SIGKILL and the second
wait: in kernel teardown), ``kept`` (``--skip-terminals`` left it), or with
``--dry-run`` ``would-stop`` (nothing is signalled).

A ``/proc`` read that fails for any reason other than the process having
exited (a permission or mount problem, not ``ENOENT``/``ESRCH``) is counted,
not treated as "nothing there". When any such read failed, one summary line
goes to stderr after the process lines:

    process_reap: unreadable=<count> first_errno=<ERRNAME> first_path=<path> ...

Exit status: 0 when no matched process is left running (nothing matched
included); 3 when one is (``kept`` or ``survived``); 4 when ``/proc`` itself
could not be listed, so nothing was scanned; 2 for a usage error, an
``--env`` value that is not ``NAME=VALUE`` with both parts non-empty, or a
``--cwd-under`` that is not an existing directory other than ``/``.
``--help`` prints this text and exits 0.

Public API:
    ProcessRecord
    ReadFailures
    find_by_env(entry) -> list[ProcessRecord]
    find_by_cwd(directory) -> list[ProcessRecord]
    stop(records, grace_seconds) -> list[tuple[ProcessRecord, str]]
    read_failures() -> ReadFailures
    reset_read_failures() -> None
"""

from __future__ import annotations

import argparse
import dataclasses
import errno
import os
import pathlib
import shlex
import signal
import sys
import time
from collections.abc import Callable, Iterable

# Started by file path (python3 .../lib/process_reap.py) there is no parent
# package: put the package root this file sits in -- the directory holding
# pgai_agent_kanban/ -- at the front of sys.path so any package import
# resolves from the same tree, never another. Imported as a package member,
# sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402

__all__ = [
    "EXIT_LEFT_RUNNING",
    "EXIT_OK",
    "EXIT_PROC_UNLISTABLE",
    "EXIT_USAGE",
    "ProcessRecord",
    "ReadFailures",
    "find_by_cwd",
    "find_by_env",
    "read_failures",
    "reset_read_failures",
    "stop",
]

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_LEFT_RUNNING = 3
EXIT_PROC_UNLISTABLE = 4

_PROC = pathlib.Path("/proc")
# Seconds between liveness polls while waiting for a signalled process.
_POLL_SECONDS = 0.1
# Longest command line printed; the rest is cut and marked.
_COMMAND_LIMIT = 300

OUTCOME_TERMINATED = "terminated"
OUTCOME_KILLED = "killed"
OUTCOME_SURVIVED = "survived"
OUTCOME_KEPT = "kept"
OUTCOME_WOULD_STOP = "would-stop"


@dataclasses.dataclass(frozen=True)
class ProcessRecord:
    """One process found by a scan.

    Attributes:
        pid:          Process id.
        start_time:   Start time in clock ticks since boot (``/proc/<pid>/stat``
                      field 22); with *pid* it names the process uniquely.
        command:      Its command line, one line, cut at a fixed length.
        cwd:          Its working directory ("?" when unreadable).
        has_terminal: True when it has a controlling terminal.
    """

    pid: int
    start_time: str
    command: str
    cwd: str
    has_terminal: bool


@dataclasses.dataclass
class ReadFailures:
    """``/proc`` reads that failed for a reason other than the process exiting.

    Attributes:
        count:        How many reads failed.
        first_errno:  The errno of the first failure (None when count is 0).
        first_path:   The path the first failure was reading.
        listing:      True when ``/proc`` itself could not be listed, so the
                      scan saw no process at all.
    """

    count: int = 0
    first_errno: int | None = None
    first_path: str = ""
    listing: bool = False

    def record(self, path: str | os.PathLike[str], exc: OSError) -> None:
        """Count *exc*; the first one's errno and path are kept."""
        if self.count == 0:
            self.first_errno = exc.errno
            self.first_path = str(path)
        self.count += 1

    def summary(self) -> str:
        """One line naming the count, the first errno by name, and its path."""
        name = errno.errorcode.get(self.first_errno or 0, str(self.first_errno))
        what = (
            "/proc could not be listed, so no process was scanned"
            if self.listing
            else ("the scan may have missed processes or kept one it could not re-check")
        )
        return (
            f"process_reap: unreadable={self.count} first_errno={name} first_path={self.first_path}"
            f" -- {self.count} /proc read(s) failed ({os.strerror(self.first_errno or 0)}); {what}"
        )


# Errors that mean the process is gone, not that /proc could not be read.
_GONE_ERRNOS = frozenset({errno.ENOENT, errno.ESRCH})
_READ_FAILURES = ReadFailures()


def read_failures() -> ReadFailures:
    """The ``/proc`` read failures counted since the last :func:`reset_read_failures`."""
    return dataclasses.replace(_READ_FAILURES)


def reset_read_failures() -> None:
    """Start a fresh failure count (``main`` does this once per run)."""
    global _READ_FAILURES
    _READ_FAILURES = ReadFailures()


def _note_read_failure(path: str | os.PathLike[str], exc: OSError) -> None:
    if exc.errno not in _GONE_ERRNOS:
        _READ_FAILURES.record(path, exc)


def _stat_fields(pid: int) -> list[str] | None:
    """``/proc/<pid>/stat`` from field 3 (state) on, or None when unreadable.

    The command name (field 2) is in parentheses and may itself hold spaces
    or parentheses, so the split starts after the last ``)``. A read that
    fails because the process has exited is the ordinary "gone" answer; any
    other failure is also counted in :func:`read_failures`.
    """
    path = _PROC / str(pid) / "stat"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _note_read_failure(path, exc)
        return None
    _head, _sep, tail = text.rpartition(")")
    fields = tail.split()
    return fields if len(fields) >= 20 else None


def _is_running(pid: int, start_time: str) -> bool:
    """True while *pid* is the same process (same start time) and not a zombie.

    An unreadable ``stat`` answers False; when the cause is not the process
    exiting, :func:`_stat_fields` has counted it in :func:`read_failures`.
    """
    fields = _stat_fields(pid)
    if fields is None:
        return False
    state, start = fields[0], fields[19]
    return start == start_time and state not in ("Z", "X", "x")


def _parent(pid: int) -> int:
    fields = _stat_fields(pid)
    return int(fields[1]) if fields else 0


def _protected_pids() -> set[int]:
    """This process, every ancestor of it, and PID 1: never signalled."""
    protected = {1}
    pid = os.getpid()
    while pid > 1 and pid not in protected:
        protected.add(pid)
        pid = _parent(pid)
    return protected


def _command(pid: int) -> str:
    try:
        raw = (_PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        raw = b""
    text = " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)
    if not text:
        try:
            text = "[" + (_PROC / str(pid) / "comm").read_text(encoding="utf-8", errors="replace").strip() + "]"
        except OSError:
            text = "[?]"
    text = " ".join(text.split())
    if len(text) > _COMMAND_LIMIT:
        text = text[: _COMMAND_LIMIT - 3] + "..."
    return text


def _cwd(pid: int) -> str | None:
    try:
        return os.readlink(_PROC / str(pid) / "cwd")
    except OSError:
        return None


def _record(pid: int, cwd: str | None = None) -> ProcessRecord | None:
    fields = _stat_fields(pid)
    if fields is None or fields[0] in ("Z", "X", "x"):
        return None
    if cwd is None:
        cwd = _cwd(pid) or "?"
    return ProcessRecord(
        pid=pid,
        start_time=fields[19],
        command=_command(pid),
        cwd=cwd,
        has_terminal=fields[4] != "0",
    )


def _candidate_pids() -> Iterable[int]:
    protected = _protected_pids()
    try:
        names = os.listdir(_PROC)
    except OSError as exc:
        _READ_FAILURES.record(_PROC, exc)
        _READ_FAILURES.listing = True
        return []
    return sorted(int(name) for name in names if name.isdigit() and int(name) not in protected)


def find_by_env(entry: str) -> list[ProcessRecord]:
    """Every readable process whose environment holds exactly *entry* (``NAME=VALUE``).

    The environment read is the one the process was started with
    (``/proc/<pid>/environ``), which is what it inherited from its parent.
    """
    wanted = entry.encode("utf-8")
    found = []
    for pid in _candidate_pids():
        try:
            environ = (_PROC / str(pid) / "environ").read_bytes()
        except OSError:
            continue
        if wanted not in environ.split(b"\0"):
            continue
        record = _record(pid)
        if record is not None:
            found.append(record)
    return found


def _inside(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def find_by_cwd(directory: str | os.PathLike[str]) -> list[ProcessRecord]:
    """Every readable process whose working directory is *directory* or inside it.

    *directory* is resolved (symlinks followed) first, as the kernel reports
    a working directory by its real path.
    """
    target = os.path.realpath(directory)
    found = []
    for pid in _candidate_pids():
        cwd = _cwd(pid)
        if cwd is None or not _inside(cwd, target):
            continue
        record = _record(pid, cwd)
        if record is not None:
            found.append(record)
    return found


def _signal(record: ProcessRecord, signum: int) -> None:
    if not _is_running(record.pid, record.start_time):
        return
    try:
        os.kill(record.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _wait_gone(
    records: list[ProcessRecord],
    grace_seconds: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> list[ProcessRecord]:
    """Poll until every record is gone or *grace_seconds* pass; return those still running."""
    deadline = clock() + grace_seconds
    alive = [r for r in records if _is_running(r.pid, r.start_time)]
    while alive and clock() < deadline:
        sleep(_POLL_SECONDS)
        alive = [r for r in alive if _is_running(r.pid, r.start_time)]
    return alive


def stop(
    records: Iterable[ProcessRecord],
    grace_seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[tuple[ProcessRecord, str]]:
    """SIGTERM every record, SIGKILL what outlives *grace_seconds*; report each outcome.

    Returns ``(record, outcome)`` per record, outcome ``terminated``,
    ``killed`` or ``survived``. A record that is already gone counts as
    ``terminated``.
    """
    records = list(records)
    for record in records:
        _signal(record, signal.SIGTERM)
    after_term = _wait_gone(records, grace_seconds, sleep, clock)
    for record in after_term:
        _signal(record, signal.SIGKILL)
    after_kill = _wait_gone(after_term, grace_seconds, sleep, clock)
    killed = {r.pid for r in after_term}
    survived = {r.pid for r in after_kill}
    outcomes = []
    for record in records:
        if record.pid in survived:
            outcomes.append((record, OUTCOME_SURVIVED))
        elif record.pid in killed:
            outcomes.append((record, OUTCOME_KILLED))
        else:
            outcomes.append((record, OUTCOME_TERMINATED))
    return outcomes


def _line(record: ProcessRecord, outcome: str) -> str:
    return f"pid={record.pid} outcome={outcome} cwd={record.cwd} command={record.command}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.process_reap",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--env", metavar="NAME=VALUE", help="stop every process whose environment holds NAME=VALUE")
    which.add_argument(
        "--cwd-under", metavar="DIR", help="stop every process whose working directory is DIR or inside it"
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="seconds to wait after SIGTERM, and again after SIGKILL (default 30)",
    )
    parser.add_argument(
        "--skip-terminals",
        action="store_true",
        help="leave a process attached to a controlling terminal running (reported 'kept')",
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would be stopped; signal nothing")
    return parser


def _cwd_scope_example(*options: str) -> str:
    """A ``--cwd-under`` dry run a refusal's remedy names, runnable as printed.

    The scope is the framework temp root as :func:`resolve_temp_root`
    resolves it, so a configured ``PGAI_AGENT_KANBAN_TEMP_DIR`` is honoured.
    The resolver never creates that directory and ``--cwd-under`` refuses one
    that does not exist, so when it is absent the command creates it first.
    *options* go between the scope and ``--dry-run``; the path is
    shell-quoted.
    """
    temp_root = resolve_temp_root()
    scope = shlex.quote(temp_root)
    command = " ".join(["python3 -m pgai_agent_kanban.lib.process_reap --cwd-under", scope, *options, "--dry-run"])
    if os.path.isdir(temp_root):
        return command
    return f"mkdir -p {scope} && {command}"


def _refuse_scope(error: str, message: str, remedy: str) -> int:
    """Refuse a scan scope before anything is signalled; returns the usage exit."""
    # BLOCK-WORTHY: an invariant by design -- a scope that could name far
    # more than one run's processes (an empty value, "/") or names nothing
    # real is refused before any process is scanned or signalled.
    emit_error_contract(error, message, remedy)
    return EXIT_USAGE


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; see the module docstring."""
    reset_read_failures()
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    if args.grace < 0:
        return _refuse_scope(
            "process_reap.grace_negative",
            f"process_reap: --grace must be zero or more seconds, got {args.grace!r}; nothing was signalled.",
            _cwd_scope_example("--grace", "30"),
        )
    if args.env is not None:
        name, sep, value = args.env.partition("=")
        if not (name and sep and value):
            return _refuse_scope(
                "process_reap.env_entry_incomplete",
                f"process_reap: --env needs NAME=VALUE with both parts non-empty, got {args.env!r}; nothing was signalled.",
                "python3 -m pgai_agent_kanban.lib.process_reap --env PGAI_AGENT_RUN_ID=CODER-20260101-001-example.4242.1 --dry-run",
            )
        records = find_by_env(args.env)
    else:
        target = os.path.realpath(args.cwd_under)
        if not os.path.isdir(target) or target == "/":
            return _refuse_scope(
                "process_reap.cwd_scope_invalid",
                f"process_reap: --cwd-under needs an existing directory other than /, got {args.cwd_under!r}; nothing was signalled.",
                _cwd_scope_example(),
            )
        records = find_by_cwd(target)

    kept = [r for r in records if args.skip_terminals and r.has_terminal]
    to_stop = [r for r in records if r not in kept]
    if args.dry_run:
        outcomes = [(r, OUTCOME_WOULD_STOP) for r in to_stop]
    else:
        outcomes = stop(to_stop, args.grace)
    outcomes += [(r, OUTCOME_KEPT) for r in kept]
    for record, outcome in sorted(outcomes, key=lambda item: item[0].pid):
        print(_line(record, outcome))
    failures = read_failures()
    if failures.count:
        print(failures.summary(), file=sys.stderr)
    if failures.listing:
        return EXIT_PROC_UNLISTABLE
    left = any(outcome in (OUTCOME_KEPT, OUTCOME_SURVIVED) for _r, outcome in outcomes)
    return EXIT_LEFT_RUNNING if left else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

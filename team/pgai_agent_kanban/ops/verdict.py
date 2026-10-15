"""
verdict.py — the lane verdict: what is each project's lane doing right now, and why.

One implementation behind ``team/scripts/why.sh`` and
``team/scripts/verdict-report.sh`` (both reach :func:`resolve_verdicts`
through ``pp_run_ops``) and ``GET /verdicts`` (which calls it in process), so
the CLI sentence, the machine-readable blocks and the JSON can never disagree
about a lane's verdict, a lock's provenance, a queue's tail or the posture.

Vocabulary
----------
Exactly five verdict words exist; a project gets exactly one:

  RUNNING     A task is in WORKING state.
  BLOCKED     No task is WORKING, and a task is BLOCKED.
  HALTED      A project-scoped HALT file is present: nothing can be dequeued
              whatever the task states, so it outranks RUNNING and BLOCKED.
  STUCK-LOCK  No task is WORKING or BLOCKED, work is queued (BACKLOG or
              WAITING), and one of the project's own locks is stale or carries
              no provenance: wake keeps deferring against a lock that will
              never resolve itself.
  IDLE        Nothing is running or blocked and no lock is stuck: the project
              is disabled in projects.cfg (``enabled=false``), or has queued
              work not yet picked up, or has no non-terminal task at all. The
              rendered line names which.

Precedence (highest wins)
-------------------------
1. ``enabled=false`` in projects.cfg -> IDLE.
2. ``<project>/HALT`` present -> HALTED. While OVERWATCH's resolution stage is
   running (``<project>/overwatch/resolving.md`` carries lane, attempt and
   version) the cause names that attempt. Otherwise, when release-state.md's
   last ``## OVERWATCH Decision`` event names the release still in
   ``## Active RC`` (or the Active RC cannot be read), the cause is that
   decision, its outcome cut at 200 characters. Otherwise the cause is the
   HALT file's first non-blank line, ``#`` dropped.
3. A WORKING task -> RUNNING (the most recently modified one).
4. A BLOCKED task -> BLOCKED (the most recently modified one).
5. Queued work and a stuck lock -> STUCK-LOCK.
6. Otherwise -> IDLE.

Lock correlation
----------------
A project's locks are the files under ``<root>/locks`` whose name ends in
``-<project>.lock`` (:func:`pgai_agent_kanban.ops.locks.project_lock_files`).
The first one that carries no provenance (``no-provenance``) or whose file
has not been refreshed for the project's stale-lock threshold
(``aged-out``; :func:`~pgai_agent_kanban.ops.locks.project_stale_age_threshold`)
is the stuck lock. The age is the lock file's mtime age -- a heartbeat a
live holder keeps refreshing -- never the held time, so a long healthy chain
is not called stuck; the cause names both. A dead-pid holder of a young lock
is not stuck when nothing holds its flock any more: the next acquisition
wins the flock and replaces the record. The exception is an orphan holder --
a process the record does not name (a child that inherited the dead
holder's lock descriptor, say) still holding the flock. Acquisitions defer
to it, narrating ``lock.provenance.orphan_holder``, so the lock stays held
until the orphan exits and is reported stuck once it ages out; a release
without ``--force`` refuses ``release_locks.orphan_holder`` (exit 4) naming
the pids. The remedy is a ``release-locks.sh --release`` command, with
``--force`` when the recorded holder is alive or, for a dead recorded
holder, when its flock is still held
(:func:`pgai_agent_kanban.ops.locks.flock_held`), so the printed command is
one the release accepts.

Stall detection
---------------
A RUNNING line always names the task, its state and the whole minutes it
has held that state. From :data:`STALL_THRESHOLD_MINUTES` on, the word stays
RUNNING (a WORKING task is running) but the line names the stall and the exit
code is :data:`EXIT_RUNNING_STALLED` rather than :data:`EXIT_RUNNING`.

Time in a state is the age of the task's status.md mtime: status.md carries
no "entered this state at" field, and any edit resets the clock, so the
figure under-counts, never over-counts. ``VERDICT_NOW_EPOCH``, set to digits
in the environment, is used as "now" for the verdict (time in state, lock
age, held time in the STUCK-LOCK cause); the ``--details`` lock readings
always use the wall clock.

Exit codes
----------
Ordered by severity so the worst of several is their maximum:
0 RUNNING, 2 IDLE, 3 RUNNING past the stall threshold, 4 BLOCKED, 5 HALTED,
6 STUCK-LOCK. 1 is never a verdict: it means no verdict was reached.

Details
-------
With ``details`` (one project, whose directory exists) the report also
carries what ``why.sh --details`` shows and ``GET /verdicts?details=true``
returns: every lock of the project (refresh age, held time from the record's
``acquired_at``, provenance, the stale-lock threshold and its derivation, and
whether a missing provenance record is a violation), the last five entries of
every ``tasks/queues/*_backlog.md`` with their raw markers beside the task's
real ``## State`` (an entry whose item is not live but is in the project's
archive reads its state from the archive, marked ``(archived)``), the posture (HALT / HALT-AFTER state, the deferral posture
from :mod:`pgai_agent_kanban.lib.defer_posture`, the ``## Active RC`` from
:mod:`pgai_agent_kanban.cm.read_state_field`) and every pending requirements
bundle. A deferral posture that refuses to resolve (a malformed campaign file
or an invalid ``[release]`` value) is carried as ``posture_error`` and never
printed over.

Unresolved projects
-------------------
A named project with no directory has no verdict: the report entry is an
:class:`UnresolvedProject` -- never an IDLE read off an empty scan. ``why.sh``
refuses such a project itself before asking; its registry walk resolves every
name (``unresolved_missing=False``).

Surfaces
--------
:func:`render_why_lines`, :func:`render_why_details` and
:func:`render_report_blocks` produce the text the scripts print;
:func:`verdicts_json` the JSON body ``GET /verdicts`` returns. Every reader
here is read-only: nothing is created, edited or removed.

CLI (``python3 -m pgai_agent_kanban.ops resolve_verdicts ...``)::

    resolve_verdicts --kanban-root=ROOT --render=why|report [--details] [PROJECT...]

``--render=why`` prints one verdict line per PROJECT and exits with the worst
code; with ``--details`` (one PROJECT) it prints that project's line and the
locks, queue tails, show-queues --details and posture sections, exiting with
the verdict's code. ``--render=report`` prints the key=value blocks
``verdict-report.sh`` documents and exits 0. Any refusal exits 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import re
import sys
import time
from typing import Optional, Sequence, Union

# Run by file path (``python3 .../verdict.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.read_state_field import read_release_state_field  # noqa: E402
from pgai_agent_kanban.lib import defer_posture, ini, md_fields  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.ops import locks as ops_locks  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402

__all__ = [
    "CLI_VERBS",
    "DecisionEvent",
    "EXIT_BLOCKED",
    "EXIT_HALTED",
    "EXIT_IDLE",
    "EXIT_REFUSED",
    "EXIT_RUNNING",
    "EXIT_RUNNING_STALLED",
    "EXIT_STUCK_LOCK",
    "PendingBundle",
    "PostureDetail",
    "ProjectDetails",
    "ProjectVerdict",
    "QueueEntry",
    "QueueTail",
    "ReleaseLockDetail",
    "STALL_THRESHOLD_MINUTES",
    "UnresolvedProject",
    "VerdictReport",
    "render_report_blocks",
    "render_why_details",
    "render_why_lines",
    "resolve_project_verdict",
    "resolve_verdicts",
    "task_state",
    "verdicts_json",
]

#: The ops CLI verbs this module serves (``ops/__main__.py`` routes them here).
CLI_VERBS: tuple[str, ...] = ("resolve_verdicts",)

VERDICT_RUNNING = "RUNNING"
VERDICT_BLOCKED = "BLOCKED"
VERDICT_HALTED = "HALTED"
VERDICT_STUCK_LOCK = "STUCK-LOCK"
VERDICT_IDLE = "IDLE"

EXIT_RUNNING = 0
EXIT_REFUSED = 1
EXIT_IDLE = 2
EXIT_RUNNING_STALLED = 3
EXIT_BLOCKED = 4
EXIT_HALTED = 5
EXIT_STUCK_LOCK = 6

#: Whole minutes in WORKING from which a RUNNING lane is reported stalled:
#: about thirty missed two-minute wake windows without a state transition.
STALL_THRESHOLD_MINUTES = 60
#: Environment name of the fixed "now" (epoch seconds) the verdict uses.
NOW_EPOCH_ENV = "VERDICT_NOW_EPOCH"
#: Characters of an OVERWATCH decision's outcome shown before it is cut.
DECISION_OUTCOME_LIMIT = 200
#: Entries shown from the end of each queue file.
QUEUE_TAIL_LENGTH = 5
#: The error field of a project with no directory.
UNRESOLVED_ERROR = "project directory not found"

_NO_PROVENANCE = ops_locks.NO_PROVENANCE
_HOUSEKEEPING_DIRS = frozenset({"archive", "queues", "plans"})
_NONTERMINAL_QUEUED = frozenset({"BACKLOG", "WAITING"})
_BACKLOG_SUFFIX = "_backlog.md"
_BUNDLE_RE = re.compile(r".*-(bugfix|priority)-bundle-.*\.md", re.DOTALL)
_VERSION_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_DIGITS_RE = re.compile(r"[0-9]+")
# POSIX [[:space:]] and the blanks xargs splits on.
_SPACE = " \t\n\r\v\f"
_XARGS_BLANKS = " \t"
_QUEUE_LINE_RE = re.compile(r"[ \t\n\r\v\f]*-[ \t\n\r\v\f]*\[(.)\][ \t\n\r\v\f]+([^ \t\n\r\v\f]+)", re.DOTALL)
_MARKER_LINE_RE = re.compile(r"([A-Za-z_]+):[ \t\n\r\v\f]*(.*)", re.DOTALL)

PathLike = Union[str, "os.PathLike[str]"]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ProjectVerdict:
    """One project's verdict.

    Attributes:
        project:          The project name.
        verdict:          RUNNING, BLOCKED, HALTED, STUCK-LOCK or IDLE.
        exit_code:        The code ``why.sh`` exits with for it.
        cause:            Why the verdict is what it is.
        remedy:           The runnable fix, or a "none needed ..." fragment.
        line:             ``<project>: <VERDICT> — ...``: verdict, cause and
                          remedy in the one sentence ``why.sh`` prints.
        task_id:          The WORKING or BLOCKED task (RUNNING and BLOCKED only).
        task_state:       ``WORKING`` or ``BLOCKED`` (RUNNING and BLOCKED only).
        minutes_in_state: Whole minutes in WORKING (RUNNING only).
    """

    project: str
    verdict: str
    exit_code: int
    cause: str
    remedy: str
    line: str
    task_id: str = ""
    task_state: str = ""
    minutes_in_state: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class UnresolvedProject:
    """A named project with no directory: no verdict was reached."""

    project: str
    project_root: str
    error: str = UNRESOLVED_ERROR

    @property
    def cause(self) -> str:
        """Return the cause text: the project is registered but its directory is missing."""
        return f"{self.project} is registered but has no directory at {self.project_root}"

    @property
    def remedy(self) -> str:
        """Return the remedy text: check the name against projects.cfg or restore the directory."""
        return (
            "check the project name against projects.cfg, or restore the missing project "
            "directory if it was deleted in error"
        )


@dataclasses.dataclass(frozen=True)
class ReleaseLockDetail:
    """One of the project's locks, as the details report it.

    Attributes:
        path:         The lock file.
        record:       Its raw provenance record.
        age_seconds:  Seconds since the file was last refreshed (now - mtime).
        held_seconds: Seconds since the record's ``acquired_at``; None when
                      the record carries none (a pre-upgrade lock).
        provenance:   ``holder=... pid=... op=... task=...`` or ``(no provenance recorded)``.
        violation:    True when the lock carries no provenance record.
    """

    path: str
    record: str
    age_seconds: int
    held_seconds: Optional[int]
    provenance: str
    violation: bool


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """One queue-file line: its raw marker, task id and the task's real state.

    ``status_state`` is the live task's ``## State``, ``(blank)`` or
    ``(no status.md)``; for an item that is not live but is archived it is
    the archived item's state followed by `` (archived)`` (see
    :func:`_archived_entry_state`).
    """

    marker: str
    task_id: str
    status_state: str


@dataclasses.dataclass(frozen=True)
class QueueTail:
    """The last entries of one ``<agent>_backlog.md``."""

    agent: str
    entries: tuple[QueueEntry, ...]


@dataclasses.dataclass(frozen=True)
class PendingBundle:
    """A requirements bundle whose ``## Status`` is not ``done``."""

    name: str
    status: str


@dataclasses.dataclass(frozen=True)
class PostureDetail:
    """The posture in force.

    Attributes:
        halt_state: ``normal``, ``halted`` or ``draining``.
        halt_event: The HALT event or HALT-AFTER token (``""`` when none).
        defer:      The resolved deferral posture.
        active_rc:  ``none`` or the ``vX.Y.Z`` in ``## Active RC``.
    """

    halt_state: str
    halt_event: str
    defer: defer_posture.DeferPosture
    active_rc: str


@dataclasses.dataclass(frozen=True)
class ProjectDetails:
    """What ``--details`` adds for one project.

    Attributes:
        locks:           The project's locks.
        stale_threshold: Their stale-lock threshold.
        queue_tails:     One per queue file; None when the project has no
                         queues directory or no queue file in it.
        posture:         None when the deferral posture refused.
        posture_error:   The refusal, when it refused.
        pending_bundles: Pending requirements bundles.
        now:             The epoch second the lock ages were read at.
    """

    locks: tuple[ReleaseLockDetail, ...]
    stale_threshold: ops_locks.StaleThreshold
    queue_tails: Optional[tuple[QueueTail, ...]]
    posture: Optional[PostureDetail]
    posture_error: Optional[defer_posture.DeferPostureError]
    pending_bundles: tuple[PendingBundle, ...]
    now: int


@dataclasses.dataclass(frozen=True)
class VerdictReport:
    """Every verdict asked for, in order, and the details when asked for."""

    entries: tuple[Union[ProjectVerdict, UnresolvedProject], ...]
    details: Optional[ProjectDetails] = None

    @property
    def worst_exit_code(self) -> int:
        """The highest verdict exit code (0 when there is no verdict)."""
        return max((e.exit_code for e in self.entries if isinstance(e, ProjectVerdict)), default=0)


@dataclasses.dataclass(frozen=True)
class DecisionEvent:
    """The last ``## OVERWATCH Decision`` event of release-state.md."""

    lane: str
    version: str
    outcome: str


# ---------------------------------------------------------------------------
# Reading files as the shell tools read them
# ---------------------------------------------------------------------------


def _read_text(path: PathLike) -> Optional[str]:
    """The file's text, undecodable bytes kept as surrogates; None when unreadable."""
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError:
        return None
    return data.decode("utf-8", errors="surrogateescape")


def _is_text(data: str) -> bool:
    """False for what grep treats as binary: a NUL byte or an encoding error."""
    return "\x00" not in data and not any("\udc80" <= ch <= "\udcff" for ch in data)


def _read_lines(text: str) -> list[str]:
    """Lines a ``while IFS= read -r`` loop sees: a final line without a newline is not read."""
    return text.split("\n")[:-1]


def _lstrip_space(value: str) -> str:
    return value.lstrip(_SPACE)


def _xargs_tokens(line: str) -> Optional[list[str]]:
    """Split *line* the way xargs splits its input; None on an unmatched quote."""
    tokens: list[str] = []
    current: list[str] = []
    in_token = False
    quote = ""
    chars = iter(line)
    for ch in chars:
        if quote:
            if ch == quote:
                quote = ""
            else:
                current.append(ch)
            continue
        if ch in _XARGS_BLANKS:
            if in_token:
                tokens.append("".join(current))
                current, in_token = [], False
            continue
        in_token = True
        if ch in ("'", '"'):
            quote = ch
        elif ch == "\\":
            current.append(next(chars, "\n"))
        else:
            current.append(ch)
    if quote:
        return None
    if in_token:
        tokens.append("".join(current))
    return tokens


_ECHO_ESCAPES = {"\\": "\\", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _echo_escapes(text: str) -> tuple[str, bool]:
    """Interpret echo -e escapes; True in the second slot when ``\\c`` stopped the output."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "\\" and nxt == "c":
            return "".join(out), True
        if ch == "\\" and nxt in _ECHO_ESCAPES:
            out.append(_ECHO_ESCAPES[nxt])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), False


def _xargs_echo(line: str) -> str:
    """What ``printf '%s\\n' LINE | xargs`` prints, trailing newlines removed.

    xargs splits on blanks with quote and backslash rules and runs echo on
    the words: leading ``-n``/``-e``/``-E`` words are echo's options. An
    unmatched quote prints nothing.
    """
    tokens = _xargs_tokens(line)
    if tokens is None:
        return ""
    newline, escapes = True, False
    while tokens and re.fullmatch(r"-[neE]+", tokens[0]):
        for flag in tokens.pop(0)[1:]:
            if flag == "n":
                newline = False
            else:
                escapes = flag == "e"
    text = " ".join(tokens)
    if escapes:
        text, _stopped = _echo_escapes(text)
    return text.rstrip("\n") if newline or text.endswith("\n") else text


def task_state(status_file: PathLike) -> str:
    """The first non-blank line under ``## State`` in *status_file*, blank-normalised.

    The heading is any line starting ``## State``; the next ``## `` heading
    ends the section. The value is read the way the shell reader did --
    words joined by single spaces, quotes removed, an unmatched quote reading
    as no value. ``""`` when the file is unreadable or carries no value.
    """
    text = _read_text(status_file)
    if text is None:
        return ""
    found = False
    for line in ini.split_lines(text):
        if line.startswith("## State"):
            found = True
            continue
        if found and line.startswith("## "):
            break
        if found and line.strip(" \t"):
            return _xargs_echo(line)
    return ""


def _mtime(path: str, fallback: int) -> int:
    """The path's own mtime in whole seconds (a link is not followed), or *fallback*."""
    try:
        return os.lstat(path).st_mtime_ns // 1_000_000_000
    except OSError:
        return fallback


def _read_tab_fields(line: str, count: int) -> list[str]:
    """Split *line* on tabs the way ``IFS=$'\\t' read -r`` fills *count* names.

    Runs of tabs separate fields, and leading and trailing tabs are dropped;
    the last name takes the rest of the line.
    """
    rest = line.strip("\t")
    fields: list[str] = []
    for _ in range(count - 1):
        head, sep, tail = rest.partition("\t")
        fields.append(head)
        rest = tail.lstrip("\t") if sep else ""
    fields.append(rest)
    return fields


# ---------------------------------------------------------------------------
# Verdict inputs
# ---------------------------------------------------------------------------


def _project_disabled(kanban_root: str, project: str) -> bool:
    """True when projects.cfg registers *project* with ``enabled=false``.

    The same answer the wake, discovery and OVERWATCH loops skip on
    (:func:`pgai_agent_kanban.lib.ini.project_enabled`), so ``why`` never
    calls a project idle that the wake is still dispatching for.
    """
    try:
        return not ini.project_enabled(kanban_root, project)
    except (ini.IniError, UnicodeDecodeError):
        return False


@dataclasses.dataclass
class _TaskScan:
    working_id: str = ""
    working_mtime: int = 0
    blocked_id: str = ""
    blocked_mtime: int = 0
    nonterminal: int = 0


def _status_files(tasks_dir: str) -> list[str]:
    """Every ``status.md`` at most two levels under *tasks_dir*, by path.

    Directories are descended without following links; a linked
    *tasks_dir* itself is not descended.
    """
    if os.path.islink(tasks_dir):
        return []
    found: list[str] = []
    try:
        top = list(os.scandir(tasks_dir))
    except OSError:
        return []
    for entry in top:
        path = f"{tasks_dir}/{entry.name}"
        if entry.name == "status.md":
            found.append(path)
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False
        if not is_dir:
            continue
        try:
            if any(child.name == "status.md" for child in os.scandir(path)):
                found.append(f"{path}/status.md")
        except OSError:
            continue
    return sorted(found)


def _scan_tasks(project_root: str) -> _TaskScan:
    """The most recently modified WORKING and BLOCKED tasks and the non-terminal count.

    Housekeeping directories (archive, queues, plans) are skipped; a task
    with no state reads as BACKLOG; DONE, WONT-DO and anything unrecognised
    are terminal. Equal mtimes keep the first task in path order.
    """
    scan = _TaskScan()
    tasks_dir = f"{project_root}/tasks"
    if not os.path.isdir(tasks_dir):
        return scan
    for status_file in _status_files(tasks_dir):
        task_dir = status_file[: -len("/status.md")]
        task_id = task_dir.rsplit("/", 1)[-1]
        if task_id in _HOUSEKEEPING_DIRS:
            continue
        state = task_state(status_file) or "BACKLOG"
        if state == "WORKING":
            scan.nonterminal += 1
            mtime = _mtime(status_file, 0)
            if not scan.working_id or mtime > scan.working_mtime:
                scan.working_id, scan.working_mtime = task_id, mtime
        elif state == "BLOCKED":
            scan.nonterminal += 1
            mtime = _mtime(status_file, 0)
            if not scan.blocked_id or mtime > scan.blocked_mtime:
                scan.blocked_id, scan.blocked_mtime = task_id, mtime
        elif state in _NONTERMINAL_QUEUED:
            scan.nonterminal += 1
    return scan


def _halt_reason(halt_file: str) -> str:
    """The HALT file's first non-blank line, leading blanks and one ``#`` dropped."""
    text = _read_text(halt_file)
    line = ""
    if text is not None and _is_text(text):
        for candidate in ini.split_lines(text):
            if candidate.strip(_SPACE):
                line = candidate
                break
    line = _lstrip_space(line)
    if line.startswith("#"):
        line = line[1:]
    line = _lstrip_space(line)
    return line or "no reason recorded in the HALT file (likely an operator-initiated manual pause)"


def _resolving_marker(project_root: str) -> Optional[tuple[str, str, str]]:
    """``(lane, attempt, version)`` from ``overwatch/resolving.md``; None when absent or partial."""
    marker = f"{project_root}/overwatch/resolving.md"
    if not os.path.isfile(marker):
        return None
    text = _read_text(marker)
    if text is None:
        return None
    fields = {"lane": "", "attempt": "", "version": ""}
    for line in _read_lines(text):
        match = _MARKER_LINE_RE.fullmatch(line)
        if match and match.group(1) in fields:
            fields[match.group(1)] = match.group(2)
    if not all(fields.values()):
        return None
    lane, attempt, version = _read_tab_fields("\t".join(fields.values()), 3)
    return lane, attempt, version


def _last_decision(release_state_path: str) -> Optional[DecisionEvent]:
    """The last ``## OVERWATCH Decision`` block of release-state.md (it is append-only).

    A block ends at the next ``## `` heading; a new decision heading starts a
    fresh block. Within a block the last ``Lane:``, ``Version:`` and
    ``Outcome:`` lines win.
    """
    if not os.path.isfile(release_state_path):
        return None
    text = _read_text(release_state_path)
    if text is None:
        return None
    in_block = have = False
    cur = {"Lane": "", "Version": "", "Outcome": ""}
    last = dict(cur)
    for line in ini.split_lines(text):
        if line.startswith("## OVERWATCH Decision"):
            in_block, cur = True, {"Lane": "", "Version": "", "Outcome": ""}
            continue
        if line.startswith("## "):
            if in_block:
                last, have = dict(cur), True
            in_block = False
        if in_block:
            for key in cur:
                if line.startswith(f"{key}:"):
                    cur[key] = line[len(key) + 1:].lstrip(" \t")
    if in_block:
        last, have = dict(cur), True
    if not have:
        return None
    lane, version, outcome = _read_tab_fields(f"{last['Lane']}\t{last['Version']}\t{last['Outcome']}", 3)
    return DecisionEvent(lane, version, outcome)


def _guard_active_rc(release_state_path: str) -> str:
    """``## Active RC`` for the decision staleness guard; ``""`` when it does not read."""
    try:
        return read_release_state_field(release_state_path, "Active RC")
    except (OSError, ValueError):
        return ""


def _posture_active_rc(release_state_path: str) -> str:
    """``## Active RC`` when it is a ``vX.Y.Z``, else ``none``."""
    if not os.path.isfile(release_state_path):
        return "none"
    try:
        value = read_release_state_field(release_state_path, "Active RC")
    except (OSError, ValueError):
        return "none"
    return value if _VERSION_RE.fullmatch(value) else "none"


def _now(now: Optional[int]) -> int:
    if now is not None:
        return now
    pinned = os.environ.get(NOW_EPOCH_ENV, "")
    return int(pinned) if _DIGITS_RE.fullmatch(pinned) else int(time.time())


def _lock_ctx(kanban_root: str) -> OpsContext:
    return OpsContext(kanban_root=pathlib.Path(kanban_root))


@dataclasses.dataclass(frozen=True)
class _StuckLock:
    path: str
    reason: str
    name: str
    age: str
    liveness: str
    held: str
    orphan_held: bool = False


def _find_stuck_lock(kanban_root: str, project: str, now: int) -> Optional[_StuckLock]:
    """The project's first lock with no provenance or aged past the threshold."""
    threshold = ops_locks.project_stale_age_threshold(_lock_ctx(kanban_root), project).seconds
    for path in ops_locks.project_lock_files(f"{kanban_root}/locks", project):
        record = ops_locks.read_provenance(path)
        liveness = ops_locks.pid_liveness_verdict(record)
        age = max(now - _mtime(path, now), 0)
        if liveness == "unparseable":
            reason = "no-provenance"
        elif age >= threshold:
            reason = "aged-out"
        else:
            continue
        held = ops_locks.held_seconds(record, now)
        held_text = ops_locks.NO_ACQUIRED_AT if held is None else f"held {ops_locks.format_held_seconds(held)}"
        name = path.rsplit("/", 1)[-1][: -len(".lock")]
        # A dead recorded holder whose flock is still held: an orphan the
        # record does not name, which release-locks.sh lets go only with --force.
        orphan_held = liveness == "stale" and ops_locks.flock_held(path)
        return _StuckLock(path, reason, name, ops_locks.format_age_seconds(age), liveness, held_text, orphan_held)
    return None


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def _halted(project: str, project_root: str) -> ProjectVerdict:
    resolving = _resolving_marker(project_root)
    if resolving is not None:
        lane, attempt, version = resolving
        cause = f"OVERWATCH resolving (lane {lane}, attempt {attempt}) for {version}"
        remedy = (
            "none needed -- OVERWATCH is attempting an autonomous recovery; re-run why.sh once it "
            "completes, or see the OVERWATCH Decision event in release-state.md."
        )
        return ProjectVerdict(project, VERDICT_HALTED, EXIT_HALTED, cause, remedy, f"{project}: HALTED — {cause}; remedy: {remedy}")

    release_state = f"{project_root}/release-state.md"
    decision = _last_decision(release_state)
    if decision is not None:
        active_rc = _guard_active_rc(release_state)
        if not active_rc or active_rc == decision.version:
            outcome = decision.outcome
            if len(outcome) > DECISION_OUTCOME_LIMIT:
                outcome = outcome[:DECISION_OUTCOME_LIMIT] + "…"
            cause = f"OVERWATCH Decision recorded (lane {decision.lane}, {decision.version}): {outcome}"
            remedy = (
                "review the full ## OVERWATCH Decision event in release-state.md; if it does not "
                "resolve the block, a human resolves it as usual (team/SOP-core.md, 'HALT Flags') — "
                f"once the underlying issue is resolved, run: team/scripts/unhalt.sh --project {project}"
            )
            return ProjectVerdict(project, VERDICT_HALTED, EXIT_HALTED, cause, remedy, f"{project}: HALTED — {cause}; remedy: {remedy}")

    halt_file = f"{project_root}/HALT"
    cause = f"{halt_file} is present ({_halt_reason(halt_file)})"
    remedy = (
        "only the operator may remove a HALT file (team/SOP-core.md, 'HALT Flags') — once the "
        f"underlying issue is resolved, run: team/scripts/unhalt.sh --project {project}"
    )
    return ProjectVerdict(project, VERDICT_HALTED, EXIT_HALTED, cause, remedy, f"{project}: HALTED — {cause}; remedy: {remedy}")


def resolve_project_verdict(kanban_root: PathLike, project: str, *, now: Optional[int] = None) -> ProjectVerdict:
    """Resolve *project*'s verdict by the precedence in the module docstring. Read-only.

    Args:
        kanban_root: The kanban root; every path in the result is built on it as given.
        project:     The project name.
        now:         Epoch second to measure ages to (default: ``VERDICT_NOW_EPOCH``,
                     else the wall clock).

    Returns:
        The :class:`ProjectVerdict`. A project with no directory reads as an
        empty one; callers that must tell the two apart check the directory
        first (:func:`resolve_verdicts` does).
    """
    root = os.fspath(kanban_root)
    project_root = f"{root}/projects/{project}"

    if _project_disabled(root, project):
        cause = "project is disabled in projects.cfg (enabled=false), so it produces no work"
        remedy = "set enabled=true in projects.cfg to re-enable it."
        return ProjectVerdict(project, VERDICT_IDLE, EXIT_IDLE, cause, remedy, f"{project}: IDLE — cause: {cause}; remedy: {remedy}")

    if os.path.isfile(f"{project_root}/HALT"):
        return _halted(project, project_root)

    current = _now(now)
    scan = _scan_tasks(project_root)

    if scan.working_id:
        task_id = scan.working_id
        minutes = max(current - scan.working_mtime, 0) // 60
        if minutes >= STALL_THRESHOLD_MINUTES:
            cause = "no state transition in that window suggests churn or a hang"
            remedy = (
                f"inspect {task_id}/status.md and its agent log, and if it is genuinely stuck, set it to "
                "BLOCKED with the obstacle named (team/SOP-core.md, 'Standard Task States') so a human "
                "can reset it, or file a bug for the hang."
            )
            line = (
                f"{project}: RUNNING — task {task_id} has been WORKING for {minutes}m, past the "
                f"{STALL_THRESHOLD_MINUTES}m stall threshold; cause: {cause}; remedy: {remedy}"
            )
            code = EXIT_RUNNING_STALLED
        else:
            cause = "an agent is actively processing it"
            remedy = f"none needed while it stays within the {STALL_THRESHOLD_MINUTES}m stall threshold."
            line = f"{project}: RUNNING — task {task_id} is WORKING, {minutes}m in that state; cause: {cause}; remedy: {remedy}"
            code = EXIT_RUNNING
        return ProjectVerdict(project, VERDICT_RUNNING, code, cause, remedy, line, task_id, "WORKING", minutes)

    if scan.blocked_id:
        task_id = scan.blocked_id
        cause = f"see {task_id}/status.md's ## Blockers field for the named obstacle"
        remedy = (
            "a human resolves that obstacle, then flips its State from BLOCKED back to BACKLOG "
            "(team/SOP-core.md, 'Standard Task States' — BLOCKED is returned to BACKLOG only by HUMAN)."
        )
        line = f"{project}: BLOCKED — task {task_id} is in BLOCKED state and no task is WORKING; cause: {cause}; remedy: {remedy}"
        return ProjectVerdict(project, VERDICT_BLOCKED, EXIT_BLOCKED, cause, remedy, line, task_id, "BLOCKED")

    queued = scan.nonterminal
    if queued > 0:
        stuck = _find_stuck_lock(root, project, current)
        if stuck is not None:
            release = f"team/scripts/release-locks.sh --release {stuck.name}"
            if stuck.liveness == "held" or stuck.orphan_held:
                release += " --force"
            if stuck.reason == "no-provenance":
                cause = (
                    f"{queued} task(s) are queued but ⚠️ VIOLATION: lock {stuck.path} (last refreshed "
                    f"{stuck.age} ago) carries no provenance record at all -- a lock this framework holds "
                    "must never exist without one (team/scripts/lib/lock_provenance.sh, 'Atomic release'), "
                    "so wake cannot be trusted to have deferred against it safely"
                )
            else:
                cause = (
                    f"{queued} task(s) are queued but lock {stuck.path} has not been refreshed for "
                    f"{stuck.age} ({stuck.held}), past this project's stale-lock threshold -- a holder "
                    "that has stopped refreshing its lock this long is stuck, not working"
                )
                if stuck.orphan_held:
                    cause += (
                        "; its recorded holder is not running, yet a process the record does not name "
                        "still holds its flock"
                    )
            remedy = (
                f"release it: {release} (team/scripts/why.sh --project {project} --details lists every "
                "lock's full provenance); if it recurs, inspect the holder's exit behavior."
            )
            line = f"{project}: STUCK-LOCK — cause: {cause}; remedy: {remedy}"
            return ProjectVerdict(project, VERDICT_STUCK_LOCK, EXIT_STUCK_LOCK, cause, remedy, line)
        cause = f"{queued} task(s) are queued (BACKLOG/WAITING) but not yet picked up"
        remedy = "none needed — the next scheduled wake firing for this project's agents will pick up queued work."
        line = f"{project}: IDLE — no task is WORKING or BLOCKED; cause: {cause}; remedy: {remedy}"
        return ProjectVerdict(project, VERDICT_IDLE, EXIT_IDLE, cause, remedy, line)

    cause = "every task is DONE or WONT-DO, or none exist yet"
    remedy = (
        "none needed — queue new work (a requirements document, a priority file, or a bug report) "
        "when there is something for this lane to do."
    )
    line = f"{project}: IDLE — no non-terminal tasks remain for this project; cause: {cause}; remedy: {remedy}"
    return ProjectVerdict(project, VERDICT_IDLE, EXIT_IDLE, cause, remedy, line)


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------


def _release_locks(kanban_root: str, project: str, now: int) -> tuple[ReleaseLockDetail, ...]:
    details = []
    for path in ops_locks.project_lock_files(f"{kanban_root}/locks", project):
        record = ops_locks.read_provenance(path)
        provenance = ops_locks.describe_provenance(record)
        details.append(
            ReleaseLockDetail(
                path=path,
                record=record,
                age_seconds=now - _mtime(path, now),
                held_seconds=ops_locks.held_seconds(record, now),
                provenance=provenance,
                violation=provenance == _NO_PROVENANCE,
            )
        )
    return tuple(details)


def _queue_entries(queue_file: str) -> list[str]:
    """Queue-entry lines (``- [m] TASK ...``) of *queue_file*; none for a binary or unreadable file."""
    text = _read_text(queue_file)
    if text is None or not _is_text(text):
        return []
    return [line for line in ini.split_lines(text) if _QUEUE_LINE_RE.match(line)]


def _queue_tails(project_root: str) -> Optional[tuple[QueueTail, ...]]:
    queues_dir = f"{project_root}/tasks/queues"
    if not os.path.isdir(queues_dir) or os.path.islink(queues_dir):
        return None
    try:
        names = sorted(entry.name for entry in os.scandir(queues_dir) if entry.name.endswith(_BACKLOG_SUFFIX))
    except OSError:
        return None
    if not names:
        return None
    tails = []
    for name in names:
        agent = name[: -len(_BACKLOG_SUFFIX)] if name != _BACKLOG_SUFFIX else name
        entries = []
        for line in _queue_entries(f"{queues_dir}/{name}")[-QUEUE_TAIL_LENGTH:]:
            match = _QUEUE_LINE_RE.match(line)
            marker, task_id = match.group(1), match.group(2)
            status_file = f"{project_root}/tasks/{task_id}/status.md"
            if os.path.isfile(status_file):
                state = task_state(status_file) or "(blank)"
            else:
                state = _archived_entry_state(project_root, task_id) or "(no status.md)"
            entries.append(QueueEntry(marker, task_id, state))
        tails.append(QueueTail(agent, tuple(entries)))
    return tuple(tails)


#: Appended to a queue entry's state when the item was read from the archive.
ARCHIVED_SUFFIX = " (archived)"


def _archived_entry_state(project_root: str, item_id: str) -> Optional[str]:
    """The state of a queue entry's item read from the project's archive, or ``None``.

    The lookup by ID behind a queue-tail entry whose ``tasks/<ID>/status.md``
    is not live. *item_id* is the entry's ID as the queue line carries it --
    a task ID, or a ``BUG-NNNN-slug`` / ``PRIORITY-NNNN-slug`` file stem
    (``archive.lookup.classify_item_id`` reads the shapes). Returns
    ``"<state> (archived)"`` when the item is in
    ``<kanban_root>/archive/<project>/`` -- a task's ``## State`` (or
    ``(blank)`` / ``(no status.md)``), an intake item's ``## Status`` (or
    ``## State``, or ``(no Status field)``).

    ``None`` -- the caller keeps its live answer -- when the ID has no item
    shape, the item is live (a live ``tasks/<ID>`` entry of any kind, or a
    live bug/priority/requirement file: the live project always wins), the
    project has no archive, or the archive does not hold it. Read-only.
    """
    # Imported here: the archive package imports ops.resolve, and ops/__init__
    # imports this module before ops.resolve, so a module-level import would
    # be circular.
    from pgai_agent_kanban.archive.layout import CATEGORY_TASKS, ProjectLayout, archive_layout_for_tasks_dir
    from pgai_agent_kanban.archive.lookup import classify_item_id, find_item

    parsed = classify_item_id(item_id)
    if parsed is None:
        return None
    tasks_dir = f"{project_root}/tasks"
    if parsed.category == CATEGORY_TASKS and os.path.lexists(f"{tasks_dir}/{parsed.canonical}"):
        return None
    if find_item(ProjectLayout(pathlib.Path(project_root)), item_id) is not None:
        return None
    archive = archive_layout_for_tasks_dir(tasks_dir)
    if archive is None:
        return None
    path = find_item(archive, item_id)
    if path is None:
        return None
    if parsed.category == CATEGORY_TASKS:
        status_file = path / "status.md"
        state = (task_state(status_file) or "(blank)") if status_file.is_file() else "(no status.md)"
    else:
        text = _read_text(path)
        fields = _universal_newlines(_json_text(text)) if text is not None else ""
        state = (md_fields.field_value(fields, "Status") or md_fields.field_value(fields, "State")
                 or "(no Status field)")
    return f"{state}{ARCHIVED_SUFFIX}"


def _universal_newlines(text: str) -> str:
    """*text* with CRLF and lone CR line endings read as LF, as a text-mode read does."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _halt_state(project_root: str) -> tuple[str, str]:
    """``(state, event)`` from the shared HALT / HALT-AFTER reader; normal when it fails."""
    try:
        from pgai_agent_kanban.dashboard.halt_state import compute_halt_state

        state, event = compute_halt_state(pathlib.Path(project_root))
    except Exception:  # noqa: BLE001 -- a display never refuses over the optional reader
        return "normal", ""
    return state or "normal", event or ""


def _pending_bundles(project_root: str) -> tuple[PendingBundle, ...]:
    requirements = f"{project_root}/requirements"
    if not os.path.isdir(requirements) or os.path.islink(requirements):
        return ()
    try:
        names = sorted(entry.name for entry in os.scandir(requirements) if entry.name.endswith(".md"))
    except OSError:
        return ()
    pending = []
    for name in names:
        if not _BUNDLE_RE.fullmatch(name):
            continue
        path = f"{requirements}/{name}"
        status = ""
        if os.path.isfile(path):
            text = _read_text(path)
            if text is not None:
                status = md_fields.field_value(_universal_newlines(_json_text(text)), "Status")
        status = status or "(no Status field)"
        if status.lower() != "done":
            pending.append(PendingBundle(name, status))
    return tuple(pending)


def _project_details(kanban_root: str, project: str, now: int) -> ProjectDetails:
    project_root = f"{kanban_root}/projects/{project}"
    halt_state, halt_event = _halt_state(project_root)
    posture: Optional[PostureDetail] = None
    error: Optional[defer_posture.DeferPostureError] = None
    try:
        defer = defer_posture.resolve_defer_posture(kanban_root, project)
    except defer_posture.DeferPostureError as exc:
        error = exc
    else:
        posture = PostureDetail(halt_state, halt_event, defer, _posture_active_rc(f"{project_root}/release-state.md"))
    return ProjectDetails(
        locks=_release_locks(kanban_root, project, now),
        stale_threshold=ops_locks.project_stale_age_threshold(_lock_ctx(kanban_root), project),
        queue_tails=_queue_tails(project_root),
        posture=posture,
        posture_error=error,
        pending_bundles=_pending_bundles(project_root),
        now=now,
    )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def resolve_verdicts(
    kanban_root: PathLike,
    projects: Optional[Sequence[str]] = None,
    *,
    details: bool = False,
    unresolved_missing: bool = True,
    now: Optional[int] = None,
    details_now: Optional[int] = None,
) -> VerdictReport:
    """Resolve the verdict of every project in *projects*, in order. Read-only.

    The one function behind ``why.sh``, ``verdict-report.sh`` and
    ``GET /verdicts``.

    Args:
        kanban_root:        The kanban root; paths in the result are built on it as given.
        projects:           Project names; None walks projects.cfg in priority order.
        details:            Add :class:`ProjectDetails` for the one project named,
                            when its directory exists.
        unresolved_missing: Report a project with no directory as an
                            :class:`UnresolvedProject` (the default) rather than
                            resolving it like an empty one.
        now:                The verdict's "now" (see :func:`resolve_project_verdict`).
        details_now:        The details' "now" (default: the wall clock).

    Returns:
        The :class:`VerdictReport`. A deferral posture that refuses is carried
        in ``details.posture_error``, not raised.

    Raises:
        ValueError: *details* with other than exactly one project.
        ini.IniError: *projects* is None and projects.cfg is malformed.
    """
    root = os.fspath(kanban_root)
    names = list(projects) if projects is not None else [e.name for e in ini.projects_registry(root)]
    if details and len(names) != 1:
        raise ValueError(f"details are per-project: name exactly one project (got {len(names)})")
    entries: list[Union[ProjectVerdict, UnresolvedProject]] = []
    for name in names:
        project_root = f"{root}/projects/{name}"
        if unresolved_missing and not os.path.isdir(project_root):
            entries.append(UnresolvedProject(name, project_root))
        else:
            entries.append(resolve_project_verdict(root, name, now=now))
    report_details = None
    if details and isinstance(entries[0], ProjectVerdict):
        current = int(time.time()) if details_now is None else details_now
        report_details = _project_details(root, names[0], current)
    return VerdictReport(tuple(entries), report_details)


# ---------------------------------------------------------------------------
# Text renderers
# ---------------------------------------------------------------------------


def render_why_lines(report: VerdictReport) -> str:
    """One verdict line per resolved project, newline-terminated."""
    return "".join(f"{e.line}\n" for e in report.entries if isinstance(e, ProjectVerdict))


def _render_locks_text(details: ProjectDetails) -> str:
    if not details.locks:
        return "no locks held for this project"
    threshold = details.stale_threshold
    lines = []
    for lock in details.locks:
        lines.append(f"lock: {lock.path}")
        lines.append(f"  {ops_locks.describe_held(lock.record, details.now)}")
        verdict = ops_locks.held_verdict(lock.record, threshold.seconds, details.now)
        if verdict is not None:
            lines.append(f"  held verdict: {verdict}")
        lines.append(f"  provenance: {lock.provenance}")
        lines.append(
            f"  stale-age threshold: {ops_locks.format_age_seconds(threshold.seconds)} ({threshold.derivation})"
        )
        if lock.violation:
            lines.append(
                '  ⚠️ VIOLATION: a lock this framework holds must never exist without a provenance record '
                '(lock_provenance.sh, "Truncate-free release") -- run team/scripts/release-locks.sh to '
                "inspect or clear it."
            )
    return "\n".join(lines)


def _render_queue_tails_text(details: ProjectDetails) -> str:
    if details.queue_tails is None:
        return "no queues found for this project"
    lines = []
    for tail in details.queue_tails:
        lines.append(f"queue: {tail.agent}")
        if not tail.entries:
            lines.append("  (no entries)")
        for entry in tail.entries:
            lines.append(f"  [{entry.marker}] {entry.task_id}  status.md: {entry.status_state}")
    return "\n".join(lines)


def _describe_token(token: str) -> str:
    try:
        from pgai_agent_kanban.halt_after.token import describe_token

        return describe_token(token)
    except Exception:  # noqa: BLE001 -- the raw token is the fallback wording
        return token


def _render_posture_text(details: ProjectDetails) -> str:
    posture = details.posture
    assert posture is not None
    if posture.halt_state == "halted":
        event = posture.halt_event
        lines = [f"HALT: present (event: {event})" if event else "HALT: present (no event recorded)"]
    elif posture.halt_state == "draining":
        token = posture.halt_event
        described = _describe_token(token)
        lines = [
            f"HALT-AFTER: draining (token={token}, {described})" if described != token
            else f"HALT-AFTER: draining (token={token})"
        ]
    else:
        lines = ["HALT/HALT-AFTER: none in force"]
    defer = posture.defer
    lines.append(f"deferred_bugs={defer.deferred_bugs.value} ({defer.deferred_bugs.tier})")
    lines.append(f"deferred_bug_level={defer.deferred_bug_level.value} ({defer.deferred_bug_level.tier})")
    lines.append(f"defer_piggyback={defer.defer_piggyback.value} ({defer.defer_piggyback.tier})")
    until = defer.until.value or "(none)"
    if defer.until.tier == defer_posture.TIER_CAMPAIGN_FILE:
        lines.append(f"until={until} (campaign file: {defer.campaign_file.value})")
    else:
        lines.append(f"until={until} (default)")
    lines.append(f"Active RC: {posture.active_rc}")
    if details.pending_bundles:
        lines.extend(f"pending bundle: {b.name}: {b.status}" for b in details.pending_bundles)
    else:
        lines.append("no pending bundles")
    return "\n".join(lines)


def render_why_details(report: VerdictReport, show_queues_pane: str) -> str:
    """The ``why.sh --details`` pane up to and including the posture section.

    Args:
        report:           A report with details for one resolved project whose
                          posture resolved.
        show_queues_pane: The ``show-queues.sh --project NAME --details`` pane,
                          embedded as it renders.

    Returns:
        The verdict line, then the locks, queue tails, show-queues and
        posture sections, newline-terminated.
    """
    entry, details = report.entries[0], report.details
    assert isinstance(entry, ProjectVerdict) and details is not None and details.posture is not None
    sections = (
        ("locks", _render_locks_text(details)),
        ("queue tails (last 5, raw markers)", _render_queue_tails_text(details)),
        ("show-queues --details", show_queues_pane.rstrip("\n")),
        ("posture", _render_posture_text(details)),
    )
    return f"{entry.line}\n" + "".join(f"\n== {title} ==\n{body}\n" for title, body in sections)


def _block(fields: Sequence[tuple[str, object]]) -> str:
    return "".join(f"{key}={value}\n" for key, value in fields)


def _verdict_block(entry: Union[ProjectVerdict, UnresolvedProject]) -> str:
    if isinstance(entry, UnresolvedProject):
        return _block(
            (("project", entry.project), ("error", entry.error), ("cause", entry.cause), ("remedy", entry.remedy))
        )
    fields: list[tuple[str, object]] = [
        ("project", entry.project), ("verdict", entry.verdict), ("exit_code", entry.exit_code),
        ("cause", entry.cause), ("remedy", entry.remedy), ("line", entry.line),
    ]
    if entry.task_id:
        fields.append(("task_id", entry.task_id))
    if entry.task_state:
        fields.append(("task_state", entry.task_state))
    if entry.minutes_in_state is not None:
        fields.append(("minutes_in_state", entry.minutes_in_state))
    return _block(fields)


def _detail_blocks_before_posture(project: str, details: ProjectDetails) -> list[str]:
    """The release_lock and queue blocks, in the order the report prints them."""
    groups: list[list[str]] = []
    threshold = details.stale_threshold
    if details.locks:
        groups.append([
            _block((
                ("kind", "release_lock"), ("project", project), ("path", lock.path),
                ("age_seconds", lock.age_seconds),
                ("held_seconds", "none" if lock.held_seconds is None else lock.held_seconds),
                ("provenance", lock.provenance), ("stale_threshold_seconds", threshold.seconds),
                ("stale_threshold_derivation", threshold.derivation),
                ("violation", "true" if lock.violation else "false"),
            ))
            for lock in details.locks
        ])
    else:
        groups.append([_block((("kind", "release_lock"), ("project", project), ("none", "true")))])
    if details.queue_tails is None:
        groups.append([_block((("kind", "queue_tail"), ("project", project), ("none", "true")))])
    else:
        queue_blocks = []
        for tail in details.queue_tails:
            if not tail.entries:
                queue_blocks.append(
                    _block((("kind", "queue_tail"), ("project", project), ("agent", tail.agent), ("none", "true")))
                )
            for entry in tail.entries:
                queue_blocks.append(_block((
                    ("kind", "queue_entry"), ("project", project), ("agent", tail.agent),
                    ("marker", entry.marker), ("task_id", entry.task_id), ("status_state", entry.status_state),
                )))
        groups.append(queue_blocks)
    return ["\n" + "\n".join(group) for group in groups]


def _posture_block(project: str, details: ProjectDetails) -> str:
    posture = details.posture
    assert posture is not None
    defer = posture.defer
    fields: list[tuple[str, object]] = [("kind", "posture"), ("project", project), ("halt_state", posture.halt_state)]
    if posture.halt_state != "normal" and posture.halt_event:
        fields.append(("halt_event", posture.halt_event))
    fields += [
        ("deferred_bugs", defer.deferred_bugs.value), ("deferred_bugs_tier", defer.deferred_bugs.tier),
        ("deferred_bug_level", defer.deferred_bug_level.value),
        ("deferred_bug_level_tier", defer.deferred_bug_level.tier),
        ("defer_piggyback", defer.defer_piggyback.value), ("defer_piggyback_tier", defer.defer_piggyback.tier),
        ("until", defer.until.value or "(none)"), ("until_tier", defer.until.tier),
    ]
    if defer.until.tier == defer_posture.TIER_CAMPAIGN_FILE:
        fields.append(("campaign_file", defer.campaign_file.value))
    fields.append(("active_rc", posture.active_rc))
    return _block(fields)


def _pending_blocks(project: str, details: ProjectDetails) -> str:
    if not details.pending_bundles:
        return _block((("kind", "pending_bundle"), ("project", project), ("none", "true")))
    return "\n".join(
        _block((("kind", "pending_bundle"), ("project", project), ("name", b.name), ("status", b.status)))
        for b in details.pending_bundles
    )


def render_report_blocks(report: VerdictReport) -> str:
    """The key=value blocks ``verdict-report.sh`` prints for *report*.

    One block per project, blank-line separated; with details, the
    release_lock, queue_entry / queue_tail, posture and pending_bundle
    blocks follow. When the deferral posture refused, the text stops where
    the posture block would start -- the caller prints the refusal.
    """
    text = "\n".join(_verdict_block(e) for e in report.entries)
    details = report.details
    if details is None:
        return text
    project = report.entries[0].project
    text += "".join(_detail_blocks_before_posture(project, details)) + "\n"
    if details.posture is None:
        return text
    return text + _posture_block(project, details) + "\n" + _pending_blocks(project, details)


# ---------------------------------------------------------------------------
# JSON (GET /verdicts)
# ---------------------------------------------------------------------------


def _json_text(value: str) -> str:
    """*value* with any undecodable byte read as U+FFFD, so it serialises as JSON."""
    return value.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _verdict_json(entry: Union[ProjectVerdict, UnresolvedProject]) -> dict:
    if isinstance(entry, UnresolvedProject):
        return {
            "project": _json_text(entry.project), "error": entry.error,
            "cause": _json_text(entry.cause), "remedy": entry.remedy,
        }
    body: dict = {
        "project": _json_text(entry.project), "verdict": entry.verdict, "exit_code": entry.exit_code,
        "cause": _json_text(entry.cause), "remedy": _json_text(entry.remedy), "line": _json_text(entry.line),
    }
    if entry.task_id:
        body["task_id"] = _json_text(entry.task_id)
    if entry.task_state:
        body["task_state"] = entry.task_state
    if entry.minutes_in_state is not None:
        body["minutes_in_state"] = entry.minutes_in_state
    return body


def _posture_json(details: ProjectDetails) -> dict:
    posture = details.posture
    assert posture is not None
    defer = posture.defer

    def field(value: defer_posture.PostureValue) -> dict:
        return {"value": _json_text(value.value), "tier": value.tier}

    until = {"value": _json_text(defer.until.value) or "(none)", "tier": defer.until.tier}
    body: dict = {
        "halt_state": posture.halt_state,
        "deferred_bugs": field(defer.deferred_bugs),
        "deferred_bug_level": field(defer.deferred_bug_level),
        "defer_piggyback": field(defer.defer_piggyback),
        "until": until,
        "active_rc": posture.active_rc,
    }
    if posture.halt_state != "normal" and posture.halt_event:
        body["halt_event"] = _json_text(posture.halt_event)
    if defer.until.tier == defer_posture.TIER_CAMPAIGN_FILE:
        until["campaign_file"] = _json_text(defer.campaign_file.value)
    body["pending_bundles"] = [
        {"name": _json_text(b.name), "status": _json_text(b.status)} for b in details.pending_bundles
    ]
    return body


def _locks_json(details: ProjectDetails) -> list[dict]:
    threshold = details.stale_threshold
    return [
        {
            "path": _json_text(lock.path),
            "provenance": _json_text(lock.provenance),
            "stale_threshold_derivation": threshold.derivation,
            "violation": lock.violation,
            "age_seconds": lock.age_seconds,
            "held_seconds": lock.held_seconds,
            "stale_threshold_seconds": threshold.seconds,
        }
        for lock in details.locks
    ]


def _queue_tails_json(details: ProjectDetails) -> list[dict]:
    return [
        {
            "agent": _json_text(tail.agent),
            "entries": [
                {"marker": _json_text(e.marker), "task_id": _json_text(e.task_id), "status_state": _json_text(e.status_state)}
                for e in tail.entries
            ],
        }
        for tail in details.queue_tails or ()
    ]


def verdicts_json(report: VerdictReport) -> dict:
    """The ``GET /verdicts`` body for *report* (without ``warnings``).

    ``{"verdicts": [...]}``, plus ``posture``, ``release_locks`` and
    ``queue_tails`` when the report carries details whose posture resolved.
    A field with no content is omitted, never null -- except
    ``held_seconds``, which is null for a lock with no recorded
    ``acquired_at``.
    """
    body: dict = {"verdicts": [_verdict_json(e) for e in report.entries]}
    details = report.details
    if details is not None and details.posture is not None:
        body["posture"] = _posture_json(details)
        body["release_locks"] = _locks_json(details)
        body["queue_tails"] = _queue_tails_json(details)
    return body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_stdout(text: str) -> None:
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8", "surrogateescape"))
    sys.stdout.buffer.flush()


def _emit_posture_error(exc: defer_posture.DeferPostureError) -> None:
    sys.stdout.flush()
    emit_error_contract(exc.error, exc.message, exc.remedy)


def _cli_why_details(root: str, report: VerdictReport) -> int:
    """Print the ``why.sh --details`` pane through the posture section."""
    from pgai_agent_kanban.ops.read import render_queues

    project = report.entries[0].project
    try:
        pane = render_queues(OpsContext(kanban_root=pathlib.Path(root)), project, project, True)
    except OpsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REFUSED
    sys.stderr.write(pane.stderr)
    sys.stderr.flush()
    details = report.details
    if details is None or details.posture_error is not None:
        if details is not None and details.posture_error is not None:
            _emit_posture_error(details.posture_error)
        return EXIT_REFUSED
    _write_stdout(render_why_details(report, pane.stdout))
    return report.entries[0].exit_code


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m pgai_agent_kanban.ops resolve_verdicts", add_help=False)
    parser.add_argument(
        "--kanban-root",
        required=True,
        help="kanban root holding projects.cfg and projects/ (required)",
    )
    parser.add_argument(
        "--render",
        required=True,
        choices=("why", "report"),
        help=(
            "why: one verdict line per project, exit with the worst code; report: key=value "
            "blocks, exit 0"
        ),
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help=(
            "with --render why and exactly one project, also print the locks, queue and posture "
            "sections"
        ),
    )
    parser.add_argument(
        "projects",
        nargs="*",
        help="the project names to resolve, in the order given",
    )
    return parser


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run ``resolve_verdicts`` from the command line (see the module docstring)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        print(f"verdict: VERB must be one of: {', '.join(CLI_VERBS)}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        opts = _cli_parser().parse_args(args[1:])
    except SystemExit:
        return EXIT_REFUSED
    if opts.details and len(opts.projects) != 1:
        print("verdict: --details takes exactly one project", file=sys.stderr)
        return EXIT_REFUSED
    why = opts.render == "why"
    report = resolve_verdicts(
        opts.kanban_root, opts.projects, details=opts.details, unresolved_missing=not why,
    )
    if why and opts.details:
        return _cli_why_details(opts.kanban_root, report)
    if why:
        _write_stdout(render_why_lines(report))
        return report.worst_exit_code
    _write_stdout(render_report_blocks(report))
    if report.details is not None and report.details.posture_error is not None:
        _emit_posture_error(report.details.posture_error)
        return EXIT_REFUSED
    return 0

"""
locks.py — the advisory-lock operator verb: list every lock under the kanban
root's ``locks/`` directory with its provenance, and release one named lock.

One implementation behind ``team/scripts/release-locks.sh`` (which reaches
:func:`list_locks` and :func:`release_lock` through ``pp_run_ops``),
``GET /release-locks`` (which calls :func:`list_locks` in process) and
``POST /operations/release-locks`` (which calls :func:`release_lock` in
process). The script keeps flag parsing and ``--help``; everything it prints
and every change it makes happen here, so the three surfaces list alike,
refuse alike and leave the same bytes on disk.

The provenance record
---------------------
Every lock file ``team/scripts/lib/lock_provenance.sh`` writes carries plain
``key=value`` lines: ``holder``, ``pid``, ``op``, ``task`` and
``acquired_at`` (ISO-8601 UTC). The readers below are the line-for-line
Python form of that file's ``pgai_lock_field``,
``pgai_lock_describe_provenance``, ``pgai_lock_pid_liveness_verdict``,
``pgai_lock_describe_held``, ``pgai_lock_held_verdict`` and
``pgai_lock_stale_age_threshold_seconds``, so a lock reads the same here as
on every bash surface (``why.sh --details``, the wake's contention message).

Liveness is evidence, never inference: a record whose pid answers a
signal-0 probe is ``held``, one whose pid does not is ``stale``, and one
with no pid at all (an empty, garbled or pre-provenance file) is
``unparseable``. A record that also carries ``boot_id`` or ``pid_start``
(the task-liveness records the wake writes) is ``stale`` when either
differs from what this host reports now -- the pid answering is then a
later process that reused the number -- and a blank field, or a host that
cannot answer, is not compared. A pid owned by another account reads as ``stale``, exactly
as ``kill -0`` reads it. A pid that is not a positive integer is never
probed (zero and negatives name process groups, not a process) and reads as
``stale``.

Taking a lock from Python
-------------------------
:func:`acquire_lock` is the Python form of ``pgai_lock_acquire``: same
staged record, same flock, same link-or-rename onto the lock path, so a
Python holder and a bash holder of one file exclude each other. It never
waits: a lock another process holds raises :class:`LockContended` carrying
that holder's record, and a blank provenance field is refused
(:class:`LockProvenanceIncomplete`). The returned :class:`HeldLock`
releases with the same ownership check ``pgai_lock_release`` makes.
:func:`repo_lock_path` names the per-project repo lock every wake chain
holds, the file ``pp_repo_lock_path`` prints. The bash helper's
contention-path reaping is not repeated here: a dead holder's flock is
already gone, so only a live or provenance-less holder is reported as
contended. :func:`pid_in_own_ancestry` tells a contended caller whether
the recorded holder pid is its own process or one of its ancestors -- the
wake chain it runs inside -- rather than an unrelated process.

What release does
-----------------
The kernel releases a dead holder's ``flock`` the moment it exits, so a dead
holder's record blocks nothing; release only clears it. A live holder's
lock is never removed or replaced: removing its file does not stop it (its
flock lives on the old inode) and only lets the next acquirer flock a fresh
inode beside it -- two chains each believing they hold "the" lock.

- Dead holder (``stale``): the lock file's flock is probed first
  (``LOCK_EX|LOCK_NB`` on a descriptor of this call's own). Granted, the
  file is atomically replaced with an empty one (a temp file in ``locks/``
  renamed over it) while the probe still holds that flock, so the next
  acquirer's recovery sweep completes the removal. Refused, the lock is
  held by a process the record does not name -- a child that inherited the
  dead holder's lock descriptor, a holder in another pid namespace, an
  acquirer between its flock and its rename -- and the release is refused
  (``release_locks.orphan_holder``, exit 4) naming the pids that hold the
  file open when ``/proc`` shows them; the file is left in place. With
  ``force`` those pids are sent SIGTERM and the call waits up to twice
  ``kill_grace_seconds`` plus :data:`FORCE_WAIT_MARGIN_SECONDS` for the
  flock to come free before replacing the file; pids that cannot be found or signalled, or a flock still held
  after the wait, refuse ``release_locks.holder_not_released`` (exit 5).
- No provenance (``unparseable``): reported as already released; nothing
  changes. Re-running a release is therefore idempotent.
- Live holder (``held``), no ``force``: refused dual-channel
  (``release_locks.holder_alive``, exit 4) naming the holder's
  ``holder``, ``pid``, ``op`` and ``task``; the file is left in place.
- Live holder with ``force``: the record is first re-read through a
  descriptor opened on the lock file; if the file's inode or recorded pid
  changed since the listing read it, the holder it named has already let go
  (the next chain may hold the lock now) and nobody is signalled. Otherwise
  the holder pid is sent SIGTERM, whose own exit path releases the lock, and
  the call waits up to twice ``kill_grace_seconds`` plus
  :data:`FORCE_WAIT_MARGIN_SECONDS` for it to let go -- a stopping wake
  first stops its provider's process group (SIGTERM, then SIGKILL after
  ``kill_grace_seconds``, then a wait for the group to empty) and only then
  releases its lock. Letting go is judged on the lock file's ORIGINAL inode,
  through a descriptor opened before the signal: while any process still
  holds a flock there -- a child that inherited the holder's lock
  descriptor survives the holder's exit and keeps it -- the holder has not
  let go, whether or not the path was unlinked. A holder that cannot be
  signalled, or whose lock is still held after the wait, is refused
  (``release_locks.holder_not_released``, exit 5, naming the remaining
  pids when ``/proc`` shows them) and its file is left in place.

Name resolution matches the lock's name (its file basename without the
trailing ``.lock``) exactly, then by an unambiguous hyphen-boundary prefix
(``repo`` matches ``repo-wake-alpha``, ``repo-wake-alpha`` never matches
``repo-wake-alphabet``); a typed trailing ``.lock`` is ignored. No match
refuses with exit 3, several with exit 2, each naming the candidates.

Output
------
:func:`list_locks` returns a :class:`LockListing`; :func:`render_lock_listing`
turns it into the text ``release-locks.sh`` prints. :func:`release_lock`
hands each narration line to its ``say`` callback (default: print to
stdout) as it happens, so the CLI shows the "waiting" line before it waits
and a route collects the lines without capturing the process's streams. A
refusal is raised as :class:`LockRefusal`; :func:`locks_exit_code` gives the
exit code the script exits with for any ops failure.

CLI (``python3 -m pgai_agent_kanban.ops <verb> ...``; the verbs in
:data:`CLI_VERBS` are routed here by ``ops/__main__.py``)::

    list_locks   --kanban-root=ROOT
    release_lock --kanban-root=ROOT --name=NAME [--force]
"""

from __future__ import annotations

import argparse
import calendar
import dataclasses
import datetime
import fcntl
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional, Union

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.lib.project_cfg import project_cfg_file, read_cfg_key
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import IoError, OpsError, Refused

__all__ = [
    "ACQUIRE_MAX_ATTEMPTS",
    "CLI_VERBS",
    "HeldLock",
    "LockContended",
    "LockEntry",
    "LockListing",
    "LockProvenanceIncomplete",
    "LockRefusal",
    "LockRelease",
    "acquire_lock",
    "acquired_epoch",
    "chain_budget_floor_seconds",
    "describe_held",
    "StaleThreshold",
    "describe_provenance",
    "flock_held",
    "format_age_seconds",
    "force_wait_seconds",
    "format_held_seconds",
    "held_seconds",
    "held_verdict",
    "list_locks",
    "lock_field",
    "locks_exit_code",
    "pid_in_own_ancestry",
    "pid_liveness_verdict",
    "boot_id",
    "proc_start_time",
    "project_lock_files",
    "project_stale_age_threshold",
    "read_provenance",
    "release_lock",
    "render_lock_listing",
    "repo_lock_path",
    "resolve_lock_name",
    "stale_age_threshold_seconds",
    "stale_lock_reap_periods",
    "wake_cfg_seconds",
]

#: The ops CLI verbs this module serves (``ops/__main__.py`` routes them here).
CLI_VERBS: tuple[str, ...] = ("list_locks", "release_lock")

#: What a record with no recognisable field describes as.
NO_PROVENANCE = "(no provenance recorded)"
#: The held-line wording for a record without a parseable acquired_at.
NO_ACQUIRED_AT = "acquired_at not recorded (pre-upgrade lock)"

#: One wake period in seconds (the two-minute per-agent cron cadence).
WAKE_PERIOD_SECONDS = 120
#: Slack added on top of the wake budget when deriving the stale floor.
CHAIN_BUDGET_MARGIN_SECONDS = 120
#: Wake periods used for the stale threshold: locks/ is root-wide, so no
#: project's [locks] stale_lock_reap_after_wake_periods applies.
DEFAULT_STALE_WAKE_PERIODS = 3
DEFAULT_MAX_RUNTIME_SECONDS = 14400
DEFAULT_MAX_TASK_SECONDS = 5400
DEFAULT_KILL_GRACE_SECONDS = 30

EXIT_USAGE = 1
EXIT_AMBIGUOUS = 2
EXIT_NOT_FOUND = 3
EXIT_HOLDER_ALIVE = 4
EXIT_HOLDER_NOT_RELEASED = 5

#: Seconds a --force wait adds to twice kill_grace_seconds: slack for the
#: stopping wake's own cleanup after its provider's process group is gone.
FORCE_WAIT_MARGIN_SECONDS = 10

_SCRIPT = "team/scripts/release-locks.sh"
_LOCK_SUFFIX = ".lock"

_DIGITS_RE = re.compile(r"[0-9]+")
_PID_RE = re.compile(r"-?[0-9]+")
# [[:space:]] -- each such character in acquired_at reads as a plain space.
_SPACE_RE = re.compile(r"[ \t\n\v\f\r]")
_ACQUIRED_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?"
    r"(Z|([+-])([0-9]{2}):?([0-9]{2}))"
)
# The largest UTC offset date(1) accepts, in minutes (+24:00, or +23:60).
_MAX_OFFSET_MINUTES = 24 * 60

# Indirections the force path signals and waits through (tests replace them).
_kill = os.kill
_sleep = time.sleep
# Where the process-identity readers look (tests point them at a fixture).
_PROC_ROOT = pathlib.Path("/proc")


# ---------------------------------------------------------------------------
# Results, refusals, output
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class LockEntry:
    """One lock file under ``locks/`` as the listing reports it.

    Attributes:
        name:         The lock's name: its file basename without ``.lock``.
        path:         The lock file.
        record:       The raw provenance record text (``""`` when the file is
                      empty, unreadable or not a file).
        holder, pid, op, task, acquired_at:
                      The record's fields (``""`` when absent).
        provenance:   The one-line ``holder=... pid=... op=... task=...``
                      description, or ``(no provenance recorded)``.
        verdict:      ``held``, ``stale`` or ``unparseable``.
        held_line:    ``held: <duration> (since <acquired_at>) by <holder>
                      pid <pid> — <task>`` (or the not-recorded wording).
        held_verdict: ``live``, ``long (Nm past floor)`` or ``dead (pid N not
                      running)``; None when acquired_at is not recorded.
    """

    name: str
    path: pathlib.Path
    record: str
    holder: str
    pid: str
    op: str
    task: str
    acquired_at: str
    provenance: str
    verdict: str
    held_line: str
    held_verdict: Optional[str]


@dataclasses.dataclass(frozen=True)
class LockListing:
    """Every lock under ``locks/``, in name order.

    Attributes:
        locks_dir:               ``<kanban root>/locks``.
        locks:                   One :class:`LockEntry` per lock file.
        stale_threshold_seconds: The age the held verdicts were judged against.
        now:                     The epoch second every held duration used.
    """

    locks_dir: pathlib.Path
    locks: tuple[LockEntry, ...]
    stale_threshold_seconds: int
    now: int


@dataclasses.dataclass(frozen=True)
class LockRelease:
    """What one :func:`release_lock` call did.

    Attributes:
        name:    The resolved lock name.
        path:    Its lock file.
        outcome: ``already_released`` (no provenance; nothing changed),
                 ``released_stale`` (a dead holder's file was swapped for an
                 empty one), ``holder_let_go`` (a signalled live holder
                 released it), ``holder_exited`` (a signalled live holder
                 exited, leaving a record the next acquisition reaps) or
                 ``orphan_let_go`` (the processes holding a dead holder's
                 flock were signalled and let go; the file was swapped for
                 an empty one).
        pid:     The recorded holder pid (``""`` when none was recorded).
        waited_seconds: Seconds waited for a signalled holder.
    """

    name: str
    path: pathlib.Path
    outcome: str
    pid: str = ""
    waited_seconds: int = 0


class LockRefusal(Refused):
    """A release was refused; nothing on disk was changed.

    Attributes:
        error:     The dual-channel machine key (``release_locks.<reason>``).
        exit_code: The code ``release-locks.sh`` exits with for it.

    The message is the dual-channel error/message/remedy block the script
    prints to stderr.
    """

    def __init__(self, error: str, message: str, remedy: str, exit_code: int) -> None:
        super().__init__(format_error_contract(error, message, remedy))
        self.error = error
        self.exit_code = exit_code


def locks_exit_code(exc: OpsError) -> int:
    """Return the exit code ``release-locks.sh`` exits with for *exc*.

    A :class:`LockRefusal` carries its own code (2 ambiguous, 3 not found,
    4 live holder, 5 live holder not released); any other failure (a
    filesystem error swapping the file) exits 1. Shared by the ops CLI and
    the REST route.
    """
    return getattr(exc, "exit_code", EXIT_USAGE)


def _print_line(line: str) -> None:
    """Print one narration line to stdout and flush it (the script's printf)."""
    print(line)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# The provenance record
# ---------------------------------------------------------------------------


def read_provenance(path: os.PathLike | str) -> str:
    """Return the raw record text of the lock file at *path*.

    ``""`` when the file is empty, absent, unreadable or not a regular file.
    Trailing newlines are dropped and NUL bytes ignored, as the shell's
    command substitution reads the same file.
    """
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").replace("\x00", "").rstrip("\n")


def lock_field(record: str, key: str) -> str:
    """Return the value of the first ``<key>=`` line of *record*, or ``""``."""
    prefix = key + "="
    for line in record.split("\n"):
        if line.startswith(prefix):
            return line[len(prefix):]
    return ""


def _fields(record: str) -> tuple[str, str, str, str]:
    return tuple(lock_field(record, key) for key in ("holder", "pid", "op", "task"))  # type: ignore[return-value]


def describe_provenance(record: str) -> str:
    """Return ``holder=H pid=P op=O task=T`` for *record*, or ``(no provenance recorded)``.

    A missing field renders as ``<unknown>``; a record carrying none of the
    four is ``(no provenance recorded)``.
    """
    holder, pid, op, task = _fields(record)
    if not (holder or pid or op or task):
        return NO_PROVENANCE
    return (
        f"holder={holder or '<unknown>'} pid={pid or '<unknown>'} "
        f"op={op or '<unknown>'} task={task or '<unknown>'}"
    )


def _pid_answers(pid: str) -> bool:
    """True when *pid* is a positive integer that answers a signal-0 probe."""
    if not _PID_RE.fullmatch(pid):
        return False
    number = int(pid)
    if number <= 0:
        return False
    try:
        _kill(number, 0)
    except (OSError, OverflowError):
        return False
    return True


def proc_start_time(pid: str) -> str:
    """Return *pid*'s start time in clock ticks since boot, or ``""``.

    Field 22 of ``/proc/<pid>/stat``, counted from after the command name's
    last ``)`` (the name may hold spaces and parentheses) -- the Python form
    of ``pgai_proc_start_time``. ``""`` when *pid* is not a number or the
    file cannot be read.
    """
    if not _DIGITS_RE.fullmatch(pid or ""):
        return ""
    try:
        stat = (_PROC_ROOT / pid / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    fields = stat.rpartition(")")[2].split()
    # fields[0] is field 3 (state), so field 22 (starttime) is fields[19].
    value = fields[19] if len(fields) > 19 else ""
    return value if _DIGITS_RE.fullmatch(value) else ""


def boot_id() -> str:
    """Return this boot's id, or ``""`` where the kernel exposes none (``pgai_boot_id``)."""
    try:
        return (_PROC_ROOT / "sys" / "kernel" / "random" / "boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def pid_liveness_verdict(record: str) -> str:
    """Return ``held``, ``stale`` or ``unparseable`` for *record* (see the module docstring)."""
    if describe_provenance(record) == NO_PROVENANCE:
        return "unparseable"
    pid = lock_field(record, "pid")
    if not pid:
        return "unparseable"
    if not _pid_answers(pid):
        return "stale"
    recorded = lock_field(record, "boot_id")
    if recorded:
        current = boot_id()
        if current and current != recorded:
            return "stale"
    recorded = lock_field(record, "pid_start")
    if recorded:
        current = proc_start_time(pid)
        if current and current != recorded:
            return "stale"
    return "held"


def acquired_epoch(record: str) -> Optional[int]:
    """Return the epoch second of *record*'s ``acquired_at``, or None when absent or torn.

    Liberal in what it reads: a ``T`` or a space between date and time, an
    optional fractional second (dropped), and ``Z`` or a ``+hh:mm`` /
    ``-hh:mm`` / ``+hhmm`` offset of at most 24 hours. A calendar-invalid
    date or time, or an instant before 1970, is torn.
    """
    value = _SPACE_RE.sub(" ", lock_field(record, "acquired_at"))
    if value.startswith(" "):
        value = value[1:]
    if value.endswith(" "):
        value = value[:-1]
    match = _ACQUIRED_RE.fullmatch(value)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    try:
        datetime.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    offset = 0
    if match.group(7) != "Z":
        offset_minutes = int(match.group(9)) * 60 + int(match.group(10))
        if offset_minutes > _MAX_OFFSET_MINUTES:
            return None
        offset = offset_minutes * 60 * (1 if match.group(8) == "+" else -1)
    epoch = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)) - offset
    return epoch if epoch >= 0 else None


def held_seconds(record: str, now: Optional[int] = None) -> Optional[int]:
    """Seconds the lock has been held (now minus acquired_at, never the mtime), or None.

    None when acquired_at is not recorded; a clock earlier than acquired_at
    reads 0.
    """
    acquired = acquired_epoch(record)
    if acquired is None:
        return None
    current = int(time.time()) if now is None else now
    return max(current - acquired, 0)


def format_held_seconds(seconds: int) -> str:
    """``Ns`` under a minute, ``Nm`` under an hour, ``Nh Mm`` beyond."""
    secs = max(int(seconds), 0)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def describe_held(record: str, now: Optional[int] = None) -> str:
    """The held line every lock listing shows.

    ``held: <duration> (since <acquired_at>) by <holder> pid <pid> — <task>``;
    ``<task>`` reads ``chain, no task yet`` for ``none`` or a missing task,
    and a record without a parseable acquired_at reads ``held: acquired_at
    not recorded (pre-upgrade lock) by ...`` -- no duration is invented.
    """
    holder, pid, _op, task = _fields(record)
    if not task or task == "none":
        task = "chain, no task yet"
    who = f"by {holder or '<unknown>'} pid {pid or '<unknown>'} — {task}"
    secs = held_seconds(record, now)
    if secs is None:
        return f"held: {NO_ACQUIRED_AT} {who}"
    return f"held: {format_held_seconds(secs)} (since {lock_field(record, 'acquired_at')}) {who}"


def held_verdict(record: str, threshold_seconds: int, now: Optional[int] = None) -> Optional[str]:
    """``live``, ``long (Nm past floor)`` or ``dead (pid N not running)``; None without acquired_at.

    ``long`` when held longer than *threshold_seconds*, N the overage in
    whole minutes rounded up; ``dead`` when the recorded pid does not answer.
    """
    secs = held_seconds(record, now)
    if secs is None:
        return None
    if pid_liveness_verdict(record) == "stale":
        return f"dead (pid {lock_field(record, 'pid')} not running)"
    if secs > threshold_seconds:
        return f"long ({(secs - threshold_seconds + 59) // 60}m past floor)"
    return "live"


# ---------------------------------------------------------------------------
# The stale-age threshold
# ---------------------------------------------------------------------------


def wake_cfg_seconds(ctx: OpsContext, env_name: str, ini_key: str, default: int) -> int:
    """Resolve one ``[wake]`` tunable as whole seconds.

    An exported environment variable *env_name* holding digits wins, then
    ``kanban.cfg``'s ``[wake] <ini_key>`` (read by the bash ``read_ini``
    rules), then *default* -- the precedence the wake loop itself applies.
    """
    env_value = os.environ.get(env_name, "")
    if _DIGITS_RE.fullmatch(env_value):
        return int(env_value)
    cfg = ctx.kanban_root / "kanban.cfg"
    if cfg.is_file():
        try:
            value = ini.read_line_value(cfg, "wake", ini_key)
        except (OSError, UnicodeDecodeError):
            value = None
        if value is not None and _DIGITS_RE.fullmatch(value):
            return int(value)
    return default


def chain_budget_floor_seconds(ctx: OpsContext) -> int:
    """The longest a legitimate wake chain holds a lock, in seconds.

    ``max_runtime_seconds + max_task_seconds + kill_grace_seconds`` plus a
    fixed margin of one wake period.
    """
    return (
        wake_cfg_seconds(ctx, "MAX_RUNTIME_SECONDS", "max_runtime_seconds", DEFAULT_MAX_RUNTIME_SECONDS)
        + wake_cfg_seconds(ctx, "MAX_TASK_SECONDS", "max_task_seconds", DEFAULT_MAX_TASK_SECONDS)
        + wake_cfg_seconds(ctx, "KILL_GRACE_SECONDS", "kill_grace_seconds", DEFAULT_KILL_GRACE_SECONDS)
        + CHAIN_BUDGET_MARGIN_SECONDS
    )


def stale_age_threshold_seconds(ctx: OpsContext) -> int:
    """The held age past which a live holder reads ``long``: the larger of the
    root-wide wake-period figure and the chain-budget floor."""
    period_based = DEFAULT_STALE_WAKE_PERIODS * WAKE_PERIOD_SECONDS
    return max(period_based, chain_budget_floor_seconds(ctx))


def stale_lock_reap_periods(ctx: OpsContext, project: str) -> int:
    """``[locks] stale_lock_reap_after_wake_periods`` for *project*, in wake periods.

    project.cfg (or a legacy PROJECT.cfg) wins over kanban.cfg; absent from
    both, the default of :data:`DEFAULT_STALE_WAKE_PERIODS`. A value at the
    tier that supplies it which is not a positive integer reads as the
    default too -- a display of the threshold never refuses over it.
    """
    key = "stale_lock_reap_after_wake_periods"
    raw = ""
    cfg = project_cfg_file(ctx.project_root(project))
    if cfg is not None:
        raw = read_cfg_key(cfg, "locks", key)
    if not raw:
        raw = ini.read_line_value(ctx.kanban_root / "kanban.cfg", "locks", key) or ""
    if not raw:
        return DEFAULT_STALE_WAKE_PERIODS
    if not _DIGITS_RE.fullmatch(raw) or int(raw) == 0:
        return DEFAULT_STALE_WAKE_PERIODS
    return int(raw)


@dataclasses.dataclass(frozen=True)
class StaleThreshold:
    """A project's stale-lock age threshold and the account of how it was reached.

    Attributes:
        seconds:    max(configured wake periods x period length, chain-budget floor).
        derivation: Which inputs produced it and which of the two won, in the
                    words every lock pane prints after the threshold.
    """

    seconds: int
    derivation: str


def project_stale_age_threshold(ctx: OpsContext, project: str) -> StaleThreshold:
    """The age past which *project*'s lock is overdue: its configured wake
    periods times the period length, never below the chain-budget floor."""
    periods = stale_lock_reap_periods(ctx, project)
    period_based = periods * WAKE_PERIOD_SECONDS
    max_runtime = wake_cfg_seconds(ctx, "MAX_RUNTIME_SECONDS", "max_runtime_seconds", DEFAULT_MAX_RUNTIME_SECONDS)
    max_task = wake_cfg_seconds(ctx, "MAX_TASK_SECONDS", "max_task_seconds", DEFAULT_MAX_TASK_SECONDS)
    kill_grace = wake_cfg_seconds(ctx, "KILL_GRACE_SECONDS", "kill_grace_seconds", DEFAULT_KILL_GRACE_SECONDS)
    floor = max_runtime + max_task + kill_grace + CHAIN_BUDGET_MARGIN_SECONDS
    addends = (
        f"max_runtime_seconds {max_runtime}s + max_task_seconds {max_task}s + "
        f"kill_grace_seconds {kill_grace}s + {CHAIN_BUDGET_MARGIN_SECONDS}s margin"
    )
    periods_text = f"{periods} wake period(s) x {WAKE_PERIOD_SECONDS}s/period = {period_based}s"
    if period_based >= floor:
        return StaleThreshold(
            period_based,
            f"configured {periods_text}, at or above the max-chain-budget floor of {floor}s ({addends})",
        )
    return StaleThreshold(
        floor, f"max-chain-budget floor enforced at {floor}s ({addends}), above the configured {periods_text}"
    )


def format_age_seconds(seconds: int) -> str:
    """``Ns`` under a minute, ``Nm Ss`` under an hour, ``Nh Mm`` beyond (negative reads 0)."""
    secs = max(int(seconds), 0)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def project_lock_files(locks_dir: str, project: str) -> list[str]:
    """Every lock file directly in *locks_dir* that belongs to *project*, in name order.

    A lock belongs to a project when its name ends ``-<project>.lock`` or
    ``-<repo id>.lock`` (the project name with ``/`` read as ``_`` and every
    character outside ``[A-Za-z0-9_-]`` dropped) -- the repo-wake and
    overwatch lock names alike. Hidden files are not locks; an entry that
    does not resolve to an existing file or directory (a dangling link) is
    skipped. Returns ``<locks_dir>/<name>`` strings; empty when *locks_dir*
    is absent.
    """
    repo_id = _repo_id(project)
    suffixes = (f"-{project}{_LOCK_SUFFIX}", f"-{repo_id}{_LOCK_SUFFIX}")
    try:
        names = sorted(entry.name for entry in os.scandir(locks_dir))
    except OSError:
        return []
    found = []
    for name in names:
        if name.startswith(".") or not name.endswith(_LOCK_SUFFIX):
            continue
        path = f"{locks_dir}/{name}"
        if os.path.exists(path) and name.endswith(suffixes):
            found.append(path)
    return found


# ---------------------------------------------------------------------------
# Acquisition: the Python form of pgai_lock_acquire / pgai_lock_release
# ---------------------------------------------------------------------------

#: Bounded retries when the lock path is replaced between open and flock,
#: as ``_PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS`` in lock_provenance.sh.
ACQUIRE_MAX_ATTEMPTS = 4
_REPO_LOCK_PREFIX = "repo-wake-"
_PROVENANCE_FIELDS = ("holder", "pid", "op", "task")


def _repo_id(project: str) -> str:
    """*project* with ``/`` read as ``_`` and every character outside ``[A-Za-z0-9_-]`` dropped.

    The sanitisation ``pp_repo_lock_path`` (project_paths.sh) applies.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "", project.replace("/", "_"))


def repo_lock_path(kanban_root: os.PathLike | str, project: str) -> pathlib.Path:
    """Return ``<kanban_root>/locks/repo-wake-<repo id>.lock``, the per-project repo lock.

    The same file ``pp_repo_lock_path`` names and every wake chain holds
    while it works the project's queue, so a holder here and a wake chain
    exclude each other.
    """
    return pathlib.Path(kanban_root) / "locks" / f"{_REPO_LOCK_PREFIX}{_repo_id(project)}{_LOCK_SUFFIX}"


class LockProvenanceIncomplete(OpsError):
    """An acquisition named a blank provenance field; no lock was taken.

    Attributes:
        error:   ``lock.provenance.incomplete``.
        message: The lock file and the blank fields.
        remedy:  A command that prints the lock-provenance rule.
        missing: The blank field names.
    """

    def __init__(self, error: str, message: str, remedy: str, missing: tuple[str, ...]) -> None:
        super().__init__(format_error_contract(error, message, remedy))
        self.error = error
        self.message = message
        self.remedy = remedy
        self.missing = missing


class LockContended(OpsError):
    """Another process holds the lock; nothing was taken or changed.

    Attributes:
        path:   The lock file.
        record: The holder's raw provenance record (``""`` when none could
                be read).
        holder, pid, op, task: The holder's four fields (``""`` when absent).
    """

    def __init__(self, path: pathlib.Path, record: str) -> None:
        self.path = path
        self.record = record
        self.holder, self.pid, self.op, self.task = _fields(record)
        super().__init__(f"{path} is held by {describe_provenance(record)}")

    def describe(self) -> str:
        """``holder=H pid=P op=O task=T``, or ``(no provenance recorded)``."""
        return describe_provenance(self.record)


@dataclasses.dataclass
class HeldLock:
    """A lock this process holds, taken by :func:`acquire_lock`.

    The flock lives on :attr:`fd`, which is not inherited by child
    processes. Call :meth:`release` exactly once; it is also a context
    manager that releases on exit.

    Attributes:
        path: The lock file.
        fd:   The open descriptor holding the flock (``-1`` once released).
        pid:  The pid recorded in the provenance.
    """

    path: pathlib.Path
    fd: int
    pid: str

    def owns_path(self) -> bool:
        """True when the file at :attr:`path` is the inode :attr:`fd` holds."""
        return self.fd >= 0 and _fd_names_path(self.fd, self.path)

    def release(self) -> bool:
        """Drop the lock; return True when the lock file was removed.

        Ownership-checked as ``pgai_lock_release`` is: the path is unlinked
        only while it still names the inode this lock's flock is on, then
        the descriptor is closed. When another process has since replaced
        the file, it is left to that holder and only the descriptor closes
        (False is returned).
        """
        if self.fd < 0:
            return False
        removed = False
        try:
            if _fd_names_path(self.fd, self.path):
                try:
                    os.unlink(self.path)
                    removed = True
                except FileNotFoundError:
                    pass
        finally:
            os.close(self.fd)
            self.fd = -1
        return removed

    def __enter__(self) -> "HeldLock":
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


def _fd_names_path(fd: int, path: pathlib.Path) -> bool:
    """True when *path* currently names the same inode as open descriptor *fd*."""
    try:
        held = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)


def _remove_staging_litter(path: pathlib.Path) -> None:
    """Remove ``<lock>.tmp.<pid>`` files whose pid no longer answers."""
    prefix = f"{path.name}.tmp."
    try:
        names = os.listdir(path.parent)
    except OSError:
        return
    for name in names:
        pid = name[len(prefix):] if name.startswith(prefix) else ""
        if not _DIGITS_RE.fullmatch(pid) or int(pid) == os.getpid() or _pid_answers(pid):
            continue
        try:
            os.unlink(path.parent / name)
        except OSError:
            pass


def acquire_lock(
    lock_file: os.PathLike | str,
    *,
    holder: str,
    pid: object,
    op: str,
    task: str,
) -> HeldLock:
    """Take the advisory lock at *lock_file* without waiting, recording who took it.

    The on-disk protocol is ``pgai_lock_acquire``'s, so this lock and every
    bash holder of the same file exclude each other: the record
    (``holder``, ``pid``, ``op``, ``task``, ``acquired_at``) is staged in
    ``<lock_file>.tmp.<pid>``, flocked there, and then linked onto the lock
    path (nothing there) or renamed over it (after winning the flock on
    the file that is there). A reader of the lock path never sees an empty
    or partial record. A previous holder that died released its flock with
    its last descriptor, so the provenance it left never blocks this
    acquisition.

    Args:
        lock_file: The lock file; its directory is created when missing.
        holder:    Who takes the lock (a program or role name).
        pid:       The process the lock belongs to (normally ``os.getpid()``).
        op:        What the holder is doing.
        task:      The task the work is for; the literal ``none`` when it
                   is not scoped to one task.

    Returns:
        The :class:`HeldLock`.

    Raises:
        LockProvenanceIncomplete: A provenance field is blank.
        LockContended: Another process holds the lock (its record is
            attached).
        IoError: The lock directory or staging file cannot be created,
            written or flocked.
    """
    values = dict(zip(_PROVENANCE_FIELDS, (holder, pid, op, task)))
    missing = tuple(name for name, value in values.items() if not str(value if value is not None else "").strip())
    path = pathlib.Path(lock_file)
    if missing:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a lock held with a
        # blank provenance field could not be diagnosed when it wedges --
        # the invariant every lock acquisition keeps. Only this
        # acquisition is refused; nothing on disk changes.
        # CENSUS-ANCHOR: locks-acquire-provenance-01
        raise LockProvenanceIncomplete(
            "lock.provenance.incomplete",
            f"acquire_lock refused: acquiring {path} is missing required provenance field(s): "
            f"{', '.join(missing)}. holder, pid, op and task must all be non-blank; "
            "a caller not scoped to one task passes the literal 'none'.",
            "grep -n -A4 '^## Lock Provenance' \"$PGAI_AGENT_KANBAN_ROOT_PATH/SOP-core.md\"",
            missing,
        )
    record = "".join(f"{name}={str(values[name]).strip()}\n" for name in _PROVENANCE_FIELDS)
    record += f"acquired_at={datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IoError(f"acquire_lock: cannot create lock directory {path.parent}: {exc}") from exc
    _remove_staging_litter(path)

    staging = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    except OSError as exc:
        raise IoError(f"acquire_lock: cannot create provenance staging file {staging}: {exc}") from exc

    def _give_up() -> None:
        os.close(fd)
        try:
            os.unlink(staging)
        except OSError:
            pass

    try:
        os.write(fd, record.encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        _give_up()
        raise IoError(
            f"acquire_lock: cannot write and flock the staging file {staging} -- "
            f"does the filesystem under {path.parent} support flock? ({exc})"
        ) from exc

    for _attempt in range(ACQUIRE_MAX_ATTEMPTS):
        try:
            os.link(staging, path)
        except FileExistsError:
            pass
        except OSError as exc:
            _give_up()
            raise IoError(f"acquire_lock: cannot link {staging} onto {path}: {exc}") from exc
        else:
            os.unlink(staging)
            return HeldLock(path, fd, str(values["pid"]).strip())

        try:
            existing = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            continue
        except OSError as exc:
            _give_up()
            raise IoError(f"acquire_lock: cannot open lock file {path}: {exc}") from exc
        try:
            try:
                fcntl.flock(existing, fcntl.LOCK_EX | fcntl.LOCK_NB)
                won = True
            except BlockingIOError:
                won = False
            if not _fd_names_path(existing, path):
                continue
            if not won:
                _give_up()
                raise LockContended(path, read_provenance(path))
            try:
                os.replace(staging, path)
            except OSError as exc:
                _give_up()
                raise IoError(f"acquire_lock: cannot move staged provenance {staging} onto {path}: {exc}") from exc
            return HeldLock(path, fd, str(values["pid"]).strip())
        finally:
            os.close(existing)

    _give_up()
    raise LockContended(path, read_provenance(path))


# ---------------------------------------------------------------------------
# Process ancestry: is a recorded holder this process's own chain?
# ---------------------------------------------------------------------------

#: How many parent links :func:`pid_in_own_ancestry` follows before giving up.
_MAX_ANCESTRY_DEPTH = 1024


def _parent_pid(pid: int) -> Optional[int]:
    """Return the parent pid of the live process *pid*, or None when it cannot be read.

    Reads ``/proc/<pid>/stat`` (the fields after the parenthesised command
    name: state, then ppid). Where ``/proc`` is not mounted, asks
    ``ps -o ppid= -p <pid>``. A process that has exited, or one neither
    source can describe, reads as None.
    """
    if pid == os.getpid():
        return os.getppid()
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        if pathlib.Path("/proc/self/stat").exists():
            return None
        return _parent_pid_from_ps(pid)
    except OSError:
        return None
    fields = stat.rpartition(")")[2].split()
    if len(fields) < 2 or not _DIGITS_RE.fullmatch(fields[1]):
        return None
    return int(fields[1])


def _parent_pid_from_ps(pid: int) -> Optional[int]:
    """``ps -o ppid= -p <pid>``, for a host without ``/proc``; None when unreadable."""
    try:
        found = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = found.stdout.strip()
    return int(value) if found.returncode == 0 and _DIGITS_RE.fullmatch(value) else None


def pid_in_own_ancestry(pid: object) -> bool:
    """True when *pid* is this process or one of its ancestors.

    This is how a caller tells "the lock is held by the wake chain I run
    inside" from "the lock is held by someone else": a wake chain records
    its own pid when it takes a lock and holds it until every process it
    started has exited, so a recorded pid that is this process's parent,
    grandparent, ... is that chain. Every ancestor is alive by definition,
    so a dead holder's leftover record never matches.

    Liberal in what it reads: *pid* may be an int or its text, with
    surrounding whitespace. Anything that is not a whole number above 1 is
    never an ancestor -- pid 1 (init) and below name no chain. The walk
    follows parent links from this process upward, at most
    ``_MAX_ANCESTRY_DEPTH`` of them, and stops at the first link it cannot
    read, so an unreadable process table answers False rather than
    guessing.
    """
    text = str(pid if pid is not None else "").strip()
    if not _DIGITS_RE.fullmatch(text):
        return False
    wanted = int(text)
    if wanted <= 1:
        return False
    current: Optional[int] = os.getpid()
    for _depth in range(_MAX_ANCESTRY_DEPTH):
        if current is None or current <= 1:
            return False
        if current == wanted:
            return True
        current = _parent_pid(current)
    return False


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _locks_dir(ctx: OpsContext) -> pathlib.Path:
    return ctx.kanban_root / "locks"


def _lock_files(locks_dir: pathlib.Path) -> list[pathlib.Path]:
    """Every ``*.lock`` entry directly in *locks_dir*, in name order."""
    try:
        names = [entry.name for entry in os.scandir(locks_dir) if entry.name.endswith(_LOCK_SUFFIX)]
    except OSError:
        return []
    return [locks_dir / name for name in sorted(names)]


def _lock_name(path: pathlib.Path) -> str:
    return path.name[: -len(_LOCK_SUFFIX)]


def list_locks(ctx: OpsContext, *, now: Optional[int] = None) -> LockListing:
    """List every lock under ``<kanban root>/locks`` with its provenance. Read-only.

    Args:
        ctx: Kanban root.
        now: Epoch second every held duration is measured to (default: now).

    Returns:
        The :class:`LockListing`; empty when ``locks/`` is absent or holds no
        ``*.lock`` file. An unreadable or empty record is listed, never
        dropped, with verdict ``unparseable``.
    """
    locks_dir = _locks_dir(ctx)
    current = int(time.time()) if now is None else now
    files = _lock_files(locks_dir)
    threshold = stale_age_threshold_seconds(ctx)
    entries = []
    for path in files:
        record = read_provenance(path)
        holder, pid, op, task = _fields(record)
        entries.append(
            LockEntry(
                name=_lock_name(path),
                path=path,
                record=record,
                holder=holder,
                pid=pid,
                op=op,
                task=task,
                acquired_at=lock_field(record, "acquired_at"),
                provenance=describe_provenance(record),
                verdict=pid_liveness_verdict(record),
                held_line=describe_held(record, current),
                held_verdict=held_verdict(record, threshold, current),
            )
        )
    return LockListing(locks_dir, tuple(entries), threshold, current)


def render_lock_listing(listing: LockListing) -> str:
    """The text ``release-locks.sh`` prints for *listing*, newline-terminated."""
    if not listing.locks:
        return f"no lock files found under {listing.locks_dir}\n"
    lines: list[str] = []
    for lock in listing.locks:
        lines.append(f"lock: {lock.path}")
        lines.append(f"  {lock.held_line}")
        if lock.held_verdict is not None:
            lines.append(f"  held verdict: {lock.held_verdict}")
        lines.append(f"  provenance: {lock.provenance}")
        lines.append(f"  verdict: {lock.verdict}")
        if lock.verdict == "stale":
            lines.append(f"  note: recorded pid {lock.pid} does not correspond to a live process")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def resolve_lock_name(names: list[str], typed: str) -> list[str]:
    """Return the lock names *typed* selects from *names*: exact first, else hyphen-boundary prefix.

    A trailing ``.lock`` on *typed* is ignored. An exact match returns only
    itself; otherwise every name starting with ``<typed>-`` is returned, in
    the order of *names*.
    """
    target = typed[: -len(_LOCK_SUFFIX)] if typed.endswith(_LOCK_SUFFIX) else typed
    if target in names:
        return [target]
    return [name for name in names if name.startswith(target + "-")]


def _inode(path: pathlib.Path) -> Optional[int]:
    try:
        return os.stat(path, follow_symlinks=False).st_ino
    except OSError:
        return None


def _inode_flock_held(fd: int) -> bool:
    """True while any open file description holds a flock on the inode *fd* is open on.

    Probes with a non-blocking exclusive flock of its own on *fd* and drops it
    at once when granted. *fd* must be this process's own descriptor, opened
    separately from every holder's (a probe on a holder's inherited descriptor
    would share its lock). A probe that fails for any reason other than
    contention reads as held: the answer gates a "the holder let go" report,
    so an unknown never reads as free.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def flock_held(path: Union[str, pathlib.Path]) -> bool:
    """True when some process holds a flock on the lock file at *path*.

    Probes on a descriptor of this call's own (see :func:`_inode_flock_held`)
    and closes it before returning, so a granted probe lock never outlives
    the call. A missing or unopenable file reads as not held: there is no
    inode to probe, and :func:`release_lock` skips the same probe for it.
    Used to tell an orphan holder -- a process the lock's record does not
    name, still holding the flock of a dead recorded holder -- from a lock
    nothing holds any more.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        return _inode_flock_held(fd)
    finally:
        os.close(fd)


def _take_flock(fd: int) -> bool:
    """Take a non-blocking exclusive flock on *fd* and keep it; True when granted.

    Any failure -- contention or otherwise -- reads as not granted: the
    answer gates replacing a lock file, so an unknown never reads as free.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _inode_holder_pids(fd: int) -> list[str]:
    """Pids of other processes with a descriptor open on the inode *fd* is open on.

    Read from ``/proc/<pid>/fd``, best-effort: processes this account cannot
    inspect are skipped, and a host without ``/proc`` yields an empty list.
    Used only to name, in a refusal, who still holds a lock's original inode.
    """
    try:
        want = os.fstat(fd)
    except OSError:
        return []
    own = os.getpid()
    found: list[str] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit() or int(entry) == own:
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            names = os.listdir(fd_dir)
        except OSError:
            continue
        for name in names:
            try:
                st = os.stat(f"{fd_dir}/{name}")
            except OSError:
                continue
            if st.st_ino == want.st_ino and st.st_dev == want.st_dev:
                found.append(entry)
                break
    return sorted(found, key=int)


def _holder_let_go(
    path: pathlib.Path, pid: str, inode: Optional[int], probe_fd: Optional[int] = None
) -> bool:
    """True once the signalled holder no longer holds *path*.

    *probe_fd*, when given, is a descriptor this process opened on the lock
    file before signalling the holder, so it still names the ORIGINAL inode
    after the path is unlinked or replaced. While any process holds a flock
    on that inode the holder has not let go, whatever the path shows: a
    child that inherited the holder's lock descriptor keeps the flock after
    the holder unlinks the file and exits, and the next acquirer would flock
    a fresh inode beside it.

    Otherwise (or with no *probe_fd*): the file is gone, a different inode
    sits at the path or its record names another pid (the holder released
    and a new acquirer took it), or the pid no longer answers (it exited;
    the kernel dropped its flock).
    """
    if probe_fd is not None and _inode_flock_held(probe_fd):
        return False
    if not os.path.exists(path):
        return True
    if inode is None or _inode(path) != inode:
        return True
    if lock_field(read_provenance(path), "pid") != pid:
        return True
    return not _pid_answers(pid)


def force_wait_seconds(ctx: OpsContext, grace_seconds: Optional[int] = None) -> int:
    """How long a ``--force`` release waits for a signalled holder to let go.

    *grace_seconds* when given; otherwise twice ``kill_grace_seconds`` (the
    environment's KILL_GRACE_SECONDS, else kanban.cfg ``[wake]``, else 30)
    plus :data:`FORCE_WAIT_MARGIN_SECONDS`. A stopping wake stops its
    provider's process group -- SIGTERM, SIGKILL after kill_grace_seconds,
    then a wait of up to kill_grace_seconds for the group to empty -- before
    it releases its lock, so one grace period is not enough to see it go.
    """
    if grace_seconds is not None:
        return grace_seconds
    kill_grace = wake_cfg_seconds(ctx, "KILL_GRACE_SECONDS", "kill_grace_seconds", DEFAULT_KILL_GRACE_SECONDS)
    return 2 * kill_grace + FORCE_WAIT_MARGIN_SECONDS


def _record_through(fd: int) -> str:
    """The provenance record as read through *fd* (from offset 0), or ``""``."""
    try:
        return os.pread(fd, 65536, 0).decode("utf-8", "replace")
    except OSError:
        return ""


def _swap_for_empty(locks_dir: pathlib.Path, name: str, target: pathlib.Path) -> None:
    """Atomically replace *target* with a fresh, empty file (a temp file renamed over it)."""
    try:
        fd, temp = tempfile.mkstemp(prefix=f"{name}.release.", dir=locks_dir)
    except OSError as exc:
        raise IoError(f"release-locks.sh: cannot create a replacement for {target} in {locks_dir}: {exc}") from exc
    os.close(fd)
    try:
        os.replace(temp, target)
    except OSError as exc:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise IoError(f"release-locks.sh: cannot replace {target}: {exc}") from exc


def release_lock(
    ctx: OpsContext,
    name: Optional[str],
    *,
    force: bool = False,
    say: Optional[Callable[[str], None]] = None,
    grace_seconds: Optional[int] = None,
) -> LockRelease:
    """Release the lock *name* selects (see "What release does" in the module docstring).

    Args:
        ctx:           Kanban root.
        name:          The lock's name or an unambiguous hyphen-boundary prefix
                       of one; a trailing ``.lock`` is ignored.
        force:         Signal a live holder (SIGTERM) and wait for it to let
                       go, instead of refusing.
        say:           Receives each narration line as it happens (default:
                       print to stdout).
        grace_seconds: How long *force* waits (default: ``kill_grace_seconds``
                       from the environment or kanban.cfg ``[wake]``, else 30).

    Returns:
        The :class:`LockRelease`.

    Raises:
        LockRefusal: no name (exit 1), no match (3), several matches (2), a
            live holder without *force* (4), a dead holder whose flock is
            still held, without *force* (4), a live holder -- or the
            processes holding a dead holder's flock -- that could not be
            signalled or did not let go (5). Nothing is changed.
        IoError:     the dead holder's file could not be replaced.
    """
    emit = say or _print_line
    typed = name or ""
    locks_dir = _locks_dir(ctx)
    if not typed:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a release that names no
        # lock has no target to act on (the CLI lists instead; the route
        # refuses before calling).
        # CENSUS-ANCHOR: locks-release-required-01
        raise LockRefusal(
            "release_locks.release_required",
            "release-locks.sh: no lock was named to release.",
            f"Run {_SCRIPT} with no flags to list every lock by name, then re-run: "
            f"{_SCRIPT} --release repo-wake-my-app",
            EXIT_USAGE,
        )
    names = [_lock_name(path) for path in _lock_files(locks_dir)]
    matches = resolve_lock_name(names, typed)

    if not matches:
        candidates = (
            f"known locks: {', '.join(names)}" if names else f"no lock files exist under {locks_dir}"
        )
        # BLOCK-WORTHY (docs/blocking-site-census.md): a name that resolves to
        # nothing has no target to act on -- an invariant-by-design stop.
        # CENSUS-ANCHOR: locks-release-not-found-01
        raise LockRefusal(
            "release_locks.not_found",
            f"release-locks.sh: no lock matches '{typed}' under {locks_dir} ({candidates}).",
            f"Run {_SCRIPT} with no flags to list every lock by name, then re-run with the full "
            "name or an unambiguous prefix.",
            EXIT_NOT_FOUND,
        )
    if len(matches) > 1:
        # BLOCK-WORTHY (docs/blocking-site-census.md): several locks share
        # this prefix; picking one silently risks releasing the wrong lock.
        # CENSUS-ANCHOR: locks-release-ambiguous-01
        raise LockRefusal(
            "release_locks.ambiguous",
            f"release-locks.sh: '{typed}' matches multiple locks: {', '.join(matches)}.",
            f"Re-run with a longer, unambiguous prefix or the full name, e.g. --release {matches[0]}.",
            EXIT_AMBIGUOUS,
        )

    target_name = matches[0]
    target = locks_dir / f"{target_name}{_LOCK_SUFFIX}"
    read_inode = _inode(target)
    record = read_provenance(target)
    verdict = pid_liveness_verdict(record)

    if verdict == "unparseable":
        emit(f"release-locks: lock {target_name} is already gone (no provenance recorded); nothing to release.")
        return LockRelease(target_name, target, "already_released")

    pid = lock_field(record, "pid")
    holder = describe_provenance(record)
    if verdict == "held" and not force:
        # BLOCK-WORTHY (docs/blocking-site-census.md): releasing a lock whose
        # holder is verifiably still running could let two processes believe
        # they hold the same lock at once; the file is left in place.
        # CENSUS-ANCHOR: locks-release-holder-alive-01
        raise LockRefusal(
            "release_locks.holder_alive",
            f"release-locks.sh: lock '{target_name}' is held by {holder}, which is still running; "
            "refusing to release it without --force. Its lock file was left in place.",
            "If the holder is wedged rather than doing real work, re-run with --force: "
            f"{_SCRIPT} --release {target_name} --force.",
            EXIT_HOLDER_ALIVE,
        )

    if verdict == "held":
        released = _signal_live_holder(
            ctx, target_name, target, record, pid, holder, emit, grace_seconds, read_inode=read_inode
        )
        if released is not None:
            return released

    return _release_dead_holder(ctx, locks_dir, target_name, target, pid, holder, force, emit, grace_seconds)


def _release_dead_holder(
    ctx: OpsContext,
    locks_dir: pathlib.Path,
    name: str,
    target: pathlib.Path,
    pid: str,
    holder: str,
    force: bool,
    emit: Callable[[str], None],
    grace_seconds: Optional[int],
) -> LockRelease:
    """Clear the record of a holder whose recorded pid is dead.

    The flock is probed on a descriptor of this call's own before anything
    is replaced, and held through the swap so no acquirer can take the old
    inode in between. A held flock means a process the record does not name
    still holds the lock: refused without *force*, its holders signalled
    with it (see "What release does" in the module docstring).
    """
    try:
        probe_fd: Optional[int] = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        emit(f"release-locks: lock {name} is already gone; nothing to release.")
        return LockRelease(name, target, "already_released", pid=pid)
    except OSError:
        # Unreadable here: nothing to probe through, and the swap below
        # reports its own failure if the directory refuses it too.
        probe_fd = None
    try:
        outcome = "released_stale"
        waited = 0
        if probe_fd is not None and not _take_flock(probe_fd):
            holders = _inode_holder_pids(probe_fd)
            if not force:
                named = f"pids {' '.join(holders)}" if holders else "pids not visible to this account"
                # BLOCK-WORTHY (docs/blocking-site-census.md): the recorded
                # holder is dead but the lock's flock is still held by a
                # process the record does not name; replacing the file
                # would let the next acquirer take a fresh inode beside it.
                # CENSUS-ANCHOR: locks-release-orphan-holder-01
                raise LockRefusal(
                    "release_locks.orphan_holder",
                    f"release-locks.sh: lock '{name}' records {holder}, whose pid is not running, yet its "
                    f"flock is still held -- by {named} (a process that inherited the holder's lock "
                    "descriptor, a holder in another pid namespace, or an acquirer mid-rename). Refusing "
                    "to release it without --force; its lock file was left in place.",
                    "If those processes are leftovers of a stopped chain rather than live work, re-run "
                    f"with --force to signal them: {_SCRIPT} --release {name} --force.",
                    EXIT_HOLDER_ALIVE,
                )
            waited = _signal_orphan_holders(ctx, name, target, pid, holder, holders, emit, grace_seconds, probe_fd)
            outcome = "orphan_let_go"
            if not os.path.exists(target) or _inode(target) != os.fstat(probe_fd).st_ino:
                emit(f"release-locks: released lock {name} ({target}): its holders let go {waited}s after SIGTERM")
                return LockRelease(name, target, outcome, pid=pid, waited_seconds=waited)
        elif probe_fd is not None and _inode(target) != os.fstat(probe_fd).st_ino:
            emit(
                f"release-locks: lock {name} was replaced by a new acquisition while it was being released; "
                "the dead holder's record is already gone and the new one was left in place."
            )
            return LockRelease(name, target, "already_released", pid=pid)
        _swap_for_empty(locks_dir, name, target)
    finally:
        if probe_fd is not None:
            os.close(probe_fd)
    if outcome == "orphan_let_go":
        emit(f"release-locks: released lock {name} ({target}): its holders let go {waited}s after SIGTERM")
    else:
        emit(f"release-locks: released stale lock {name} ({target})")
    return LockRelease(name, target, outcome, pid=pid, waited_seconds=waited)


def _signal_orphan_holders(
    ctx: OpsContext,
    name: str,
    target: pathlib.Path,
    pid: str,
    holder: str,
    holders: list[str],
    emit: Callable[[str], None],
    grace_seconds: Optional[int],
    probe_fd: int,
) -> int:
    """SIGTERM the processes holding a dead holder's flock and wait for it to come free.

    Returns the seconds waited once *probe_fd* holds the flock. Raises
    :class:`LockRefusal` (exit 5) when no holder is visible, none could be
    signalled, or the flock is still held after the grace; nothing on disk
    is touched in either case.
    """
    grace = force_wait_seconds(ctx, grace_seconds)
    signalled: list[str] = []
    for other in holders:
        try:
            _kill(int(other), signal.SIGTERM)
        except ProcessLookupError:
            signalled.append(other)  # already gone: its descriptor is closed
        except (OSError, OverflowError):
            continue
        else:
            signalled.append(other)
    if not signalled:
        if holders:
            message = (
                f"release-locks.sh: lock '{name}' records {holder}, whose pid is not running, and its flock "
                f"is still held by pids {' '.join(holders)}, which could not be sent SIGTERM from this "
                "account; its lock file was left untouched."
            )
            remedy = (
                f"Signal them from the account that owns them: kill -TERM {' '.join(holders)} -- then re-run "
                f"{_SCRIPT} --release {name}."
            )
        else:
            message = (
                f"release-locks.sh: lock '{name}' records {holder}, whose pid is not running, and its flock "
                "is still held by processes not visible to this account, so --force has nobody to signal; "
                "its lock file was left untouched."
            )
            remedy = (
                f"List the processes holding it: find -L /proc/[0-9]*/fd -maxdepth 1 -samefile {target} "
                f"2>/dev/null -- stop them, then re-run {_SCRIPT} --release {name}."
            )
        # BLOCK-WORTHY (docs/blocking-site-census.md): the override was asked
        # for but cannot be delivered to any process holding the flock, and
        # replacing the file would seat the next acquirer beside them.
        # CENSUS-ANCHOR: locks-release-orphan-not-released-01
        raise LockRefusal("release_locks.holder_not_released", message, remedy, EXIT_HOLDER_NOT_RELEASED)

    emit(
        f"release-locks: sent SIGTERM to pids {' '.join(signalled)} holding lock {name} for its dead recorded "
        f"holder pid {pid} (--force); waiting up to {grace}s for them to let go"
    )
    waited = 0
    while not _take_flock(probe_fd):
        if waited >= grace:
            message, remedy = _not_released_wording(name, target, pid, holder, waited, probe_fd)
            # BLOCK-WORTHY (docs/blocking-site-census.md): the override was
            # delivered but the flock is still held after the grace; reporting
            # the lock released would seat a second holder beside them.
            # CENSUS-ANCHOR: locks-release-orphan-not-released-02
            raise LockRefusal("release_locks.holder_not_released", message, remedy, EXIT_HOLDER_NOT_RELEASED)
        _sleep(1)
        waited += 1
    return waited


def _signal_live_holder(
    ctx: OpsContext,
    name: str,
    target: pathlib.Path,
    record: str,
    pid: str,
    holder: str,
    emit: Callable[[str], None],
    grace_seconds: Optional[int],
    *,
    read_inode: Optional[int] = None,
) -> Optional[LockRelease]:
    """SIGTERM a live holder and wait for it to let go of *target*.

    Returns the :class:`LockRelease` once the holder let go, or None when it
    exited before it could be signalled (the caller then clears its record as
    a dead holder's). Raises :class:`LockRefusal` (exit 5) when it cannot be
    signalled or still holds after the wait; the file is never touched here.

    Before any signal the record is re-read through the probe descriptor: a
    lock file whose inode differs from *read_inode* (the one the record was
    read from), or whose recorded pid is no longer *pid*, means that holder
    already let go and the lock may now belong to the next chain -- the call
    reports it let go and signals nobody (outcome ``holder_let_go``, no pid).
    """
    grace = force_wait_seconds(ctx, grace_seconds)
    # A descriptor of our own on the lock's current inode, opened before the
    # signal: the holder's exit path unlinks the file, and the only way left
    # to ask whether anything still holds the old inode's flock is through a
    # descriptor that already names it (see _holder_let_go).
    try:
        probe_fd: Optional[int] = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        probe_fd = None
    try:
        if probe_fd is not None and _holder_changed(probe_fd, pid, read_inode):
            emit(
                f"release-locks: the holder recorded for lock {name} let go of it before it could be "
                "signalled; the lock may already belong to a new holder, so nobody was signalled. "
                f"To act on the lock as it is now, re-run: {_SCRIPT} --release {name}"
            )
            return LockRelease(name, target, "holder_let_go")
        return _signal_and_wait(name, target, pid, holder, emit, grace, probe_fd)
    finally:
        if probe_fd is not None:
            os.close(probe_fd)


def _holder_changed(probe_fd: int, pid: str, read_inode: Optional[int]) -> bool:
    """True when the lock *probe_fd* names is no longer the one read for *pid*.

    The file's inode differs from *read_inode* (the holder released and a new
    acquirer renamed its own file into place), or the record read through
    *probe_fd* names another pid (or none).
    """
    try:
        if read_inode is not None and os.fstat(probe_fd).st_ino != read_inode:
            return True
    except OSError:
        return False
    return lock_field(_record_through(probe_fd), "pid") != pid


def _signal_and_wait(
    name: str,
    target: pathlib.Path,
    pid: str,
    holder: str,
    emit: Callable[[str], None],
    grace: int,
    probe_fd: Optional[int],
) -> Optional[LockRelease]:
    """The body of :func:`_signal_live_holder`, run while *probe_fd* is open."""
    inode = os.fstat(probe_fd).st_ino if probe_fd is not None else _inode(target)
    try:
        _kill(int(pid), signal.SIGTERM)
    except (OSError, OverflowError):
        if _pid_answers(pid):
            # BLOCK-WORTHY (docs/blocking-site-census.md): the override was
            # asked for but cannot be delivered -- a live holder this account
            # cannot signal keeps its flock, and swapping its file out from
            # under it would seat a second chain beside it.
            # CENSUS-ANCHOR: locks-release-holder-not-released-01
            raise LockRefusal(
                "release_locks.holder_not_released",
                f"release-locks.sh: lock '{name}' is held by {holder}, which is alive but could not "
                "be sent SIGTERM from this account (a pid owned by another user cannot be signalled "
                "here); its lock file was left untouched.",
                f"Signal the holder from the account that owns it: kill -TERM {pid} -- then re-run "
                f"{_SCRIPT} --release {name} once it has exited.",
                EXIT_HOLDER_NOT_RELEASED,
            )
        emit(
            f"release-locks: holder pid {pid} of lock {name} exited before it could be signalled; "
            "clearing its stale record"
        )
        return None

    emit(
        f"release-locks: sent SIGTERM to live holder pid {pid} of lock {name} (--force); "
        f"waiting up to {grace}s for it to release the lock itself"
    )
    waited = 0
    let_go = False
    while True:
        if _holder_let_go(target, pid, inode, probe_fd):
            let_go = True
            break
        if waited >= grace:
            break
        _sleep(1)
        waited += 1
    if let_go:
        if os.path.exists(target) and not _pid_answers(pid):
            emit(
                f"release-locks: holder pid {pid} of lock {name} exited {waited}s after SIGTERM; its "
                "flock is gone and the record it left is reaped on the next acquisition (or re-run "
                f"--release {name} to clear it now)"
            )
            return LockRelease(name, target, "holder_exited", pid=pid, waited_seconds=waited)
        emit(f"release-locks: released lock {name} ({target}): holder pid {pid} let go of it {waited}s after SIGTERM")
        return LockRelease(name, target, "holder_let_go", pid=pid, waited_seconds=waited)
    message, remedy = _not_released_wording(name, target, pid, holder, waited, probe_fd)
    # BLOCK-WORTHY (docs/blocking-site-census.md): the override was delivered
    # but not honoured within the grace -- the holder, or a process that
    # inherited its lock descriptor, still holds the flock, and reporting the
    # lock released would seat a second chain beside it. Escalation is the
    # operator's call.
    # CENSUS-ANCHOR: locks-release-holder-not-released-02
    raise LockRefusal("release_locks.holder_not_released", message, remedy, EXIT_HOLDER_NOT_RELEASED)


def _not_released_wording(
    name: str, target: pathlib.Path, pid: str, holder: str, waited: int, probe_fd: Optional[int]
) -> tuple[str, str]:
    """The message and remedy of the exit-5 refusal after the grace ran out.

    Two shapes: the recorded holder pid is itself still alive, or it has
    exited while other processes still hold the flock on the lock's original
    inode (named when ``/proc`` shows them).
    """
    if _pid_answers(pid) or probe_fd is None:
        return (
            f"release-locks.sh: lock '{name}' is still held by {holder} {waited}s after SIGTERM; the "
            "holder is alive and has not released it -- a wake may still be stopping its provider's "
            "process group, which it does before it releases its lock -- and its lock file was left "
            "untouched (removing a live holder's file would only seat a second chain beside it).",
            f"Give a stopping wake time to finish, then re-run (it waits again): {_SCRIPT} --release "
            f"{name} --force. Only if the holder is wedged and must go: kill -KILL {pid} -- then re-run "
            f"{_SCRIPT} --release {name} (a dead holder's record is cleared without --force).",
        )
    others = _inode_holder_pids(probe_fd)
    named = f"pids {' '.join(others)}" if others else "pids not visible to this account"
    message = (
        f"release-locks.sh: lock '{name}' is not released {waited}s after SIGTERM: its holder ({holder}) "
        f"has exited, but other processes still hold the flock on the lock file's original inode "
        f"({named}) -- typically children that inherited the holder's lock descriptor and are still "
        "running. Reporting the lock released would let the next acquirer take a fresh file beside them."
    )
    if others:
        remedy = (
            f"Stop the processes still holding it: kill -TERM {' '.join(others)} -- then re-run "
            f"{_SCRIPT} --release {name}."
        )
    else:
        remedy = (
            f"List the processes still holding it: find /proc/[0-9]*/fd -lname '{target}*' 2>/dev/null "
            f"-- stop them, then re-run {_SCRIPT} --release {name}."
        )
    return message, remedy


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_parser(verb: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python3 -m pgai_agent_kanban.ops {verb}", add_help=False)
    parser.add_argument(
        "--kanban-root",
        required=True,
        help="kanban root whose locks/ directory is read (required)",
    )
    if verb == "release_lock":
        parser.add_argument(
            "--name",
            default="",
            help="the lock to release, as list_locks names it",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="signal a live holder (SIGTERM) and wait for it to let go before releasing",
        )
    return parser


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run one lock verb from the command line (see the module docstring).

    ``list_locks`` prints the listing; ``release_lock`` prints its narration.
    A refusal goes to stderr through the shared dual-channel emitter and the
    exit code is :func:`locks_exit_code`'s.
    """
    from pgai_agent_kanban.ops.write import _print_refusal

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        print(f"locks: VERB must be one of: {', '.join(CLI_VERBS)}", file=sys.stderr)
        return EXIT_USAGE
    verb = args[0]
    opts = _cli_parser(verb).parse_args(args[1:])
    ctx = OpsContext(kanban_root=pathlib.Path(opts.kanban_root))
    if verb == "list_locks":
        sys.stdout.write(render_lock_listing(list_locks(ctx)))
        return 0
    try:
        release_lock(ctx, opts.name, force=opts.force)
    except OpsError as exc:
        sys.stdout.flush()
        _print_refusal(exc)
        return locks_exit_code(exc)
    return 0

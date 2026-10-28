"""
halt_first.py — the HALT-first protocol for OVERWATCH's Python writers.

The Python counterpart of ``overwatch_halt_first_fix`` and
``overwatch_backup_file`` in ``team/scripts/lib/overwatch_protocol.sh``, for
a fix carried out in Python (the decision executor,
:mod:`pgai_agent_kanban.overwatch.decision_executor`). Both sides follow one
protocol over the same files, so a bash fix and a Python fix exclude each
other, read each other's repair HALT and restore each other's backups.

The protocol (:func:`run_halt_first`), in order
-----------------------------------------------
1. ``HALT_OVERWATCH`` set in the environment, or a ``HALT_OVERWATCH`` file
   at the kanban root: decline; nothing is run or changed.
2. The fleet-wide ``<kanban root>/HALT``: defer; nothing is run or changed.
3. Take the project's repo lock (:func:`pgai_agent_kanban.ops.locks.
   repo_lock_path`, the file every wake chain holds while it works the
   project), with holder/pid/op/task provenance. Held by someone else past
   ``lock_wait_seconds`` (default ``OVERWATCH_LOCK_WAIT_SECONDS``, else 0:
   one attempt): decline; nothing is run or changed.
4. Under the lock: a project HALT a stopped OVERWATCH repair left behind
   (its creator marker names a process that no longer runs) is removed. A
   project HALT still present defers the fix, unless the caller passes
   ``past_project_halt`` — the fix whose subject is that HALT (clearing a
   chain HALT, the resolution stage) — in which case the fix runs with the
   HALT left exactly as it is. With no project HALT, an OVERWATCH repair
   HALT is created so the project's chain starts no task while the fix
   runs.
5. Run the fix.
6. Remove the repair HALT this call created (only while it still carries
   this process's marker) and release the lock, whatever the fix did.

A deferral speaks the dual-channel contract on stderr and writes one
``fix-deferred`` row to the project's OVERWATCH action log, as the bash
wrapper does.

The repair HALT
---------------
Byte-compatible with ``overwatch_write_repair_halt``::

    # HALT created by OVERWATCH while it repairs this project (<op>); removed when the repair ends.
    # overwatch-repair-halt: pid=<pid> start=<start> fn=<op> at=<UTC time>
    # halt-writer: overwatch-repair
    # halt-reason: OVERWATCH repair in progress (<op>)

``<start>`` is field 22 of ``/proc/<pid>/stat``, so a recycled pid never
reads as the creator (:func:`repair_halt_state`).

Backups
-------
:func:`backup_file` copies a file to
``<project>/overwatch/backups/<timestamp>/project-root/<path relative to the
project root>`` (``absolute/<path>`` for a file outside it), keeping the
first copy taken in a firing — ``overwatch_backup_file``'s layout, which
``team/scripts/overwatch-revert.sh`` reads back from the action log.

Usage (import)::

    from pgai_agent_kanban.overwatch.halt_first import backup_file, run_halt_first
    envelope = run_halt_first(kanban_root, project, fix, op="reset-to-backlog", task=task_id)
    if envelope.ran:
        envelope.value          # what fix() returned
    else:
        envelope.detail         # why nothing ran
"""

from __future__ import annotations

import dataclasses
import datetime
import os
import pathlib
import re
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

# Run by file path the module has no parent package; put this tree's own
# package root first so the package imports resolve from the same tree.
# Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402
from pgai_agent_kanban.ops.locks import LockContended, acquire_lock, repo_lock_path  # noqa: E402
from pgai_agent_kanban.overwatch.halt_provenance import WRITER_OVERWATCH_REPAIR, provenance_lines  # noqa: E402
from pgai_agent_kanban.overwatch.resolution_stage import log_action  # noqa: E402

__all__ = [
    "REPAIR_HALT_MARKER",
    "STATUS_DECLINED_HALT_OVERWATCH",
    "STATUS_DECLINED_LOCK_BUSY",
    "STATUS_DEFERRED_HALT",
    "STATUS_FAILED",
    "STATUS_RAN",
    "Envelope",
    "backup_file",
    "clear_dead_repair_halt",
    "firing_timestamp",
    "repair_halt_state",
    "run_halt_first",
    "write_repair_halt",
]

#: The fix ran (``Envelope.value`` holds what it returned).
STATUS_RAN = "ran"
#: ``HALT_OVERWATCH`` is set: nothing ran (the bash wrapper's sentinel 3).
STATUS_DECLINED_HALT_OVERWATCH = "declined-halt-overwatch"
#: The project's repo lock is held: nothing ran (sentinel 4).
STATUS_DECLINED_LOCK_BUSY = "declined-lock-busy"
#: A HALT is present: nothing ran (sentinel 5).
STATUS_DEFERRED_HALT = "deferred-halt-present"
#: The envelope itself could not be set up (the repair HALT not writable).
STATUS_FAILED = "failed"

#: The creator marker's prefix, ``OVERWATCH_REPAIR_HALT_MARKER`` in bash.
REPAIR_HALT_MARKER = "# overwatch-repair-halt:"

#: The exact marker line; anything else is never read as a repair's
#: (fail closed, as overwatch_repair_halt_state does).
_MARKER_LINE_RE = re.compile(
    r"^# overwatch-repair-halt: pid=([0-9]+) start=([0-9]+) fn=\S+ at=[0-9TZ:-]+$", re.MULTILINE
)

#: Who takes the repo lock.
LOCK_HOLDER = "overwatch"

#: How often a contended lock is retried while waiting.
_LOCK_POLL_SECONDS = 0.5

#: The action-log name every row this module writes carries.
_LOG_NAME = "overwatch-halt-first-fix"


@dataclasses.dataclass(frozen=True)
class Envelope:
    """What :func:`run_halt_first` did.

    Attributes:
        status:    One of the ``STATUS_*`` values.
        detail:    One line saying what happened.
        halt_path: The HALT that deferred the fix, when one did.
        value:     What the fix returned, when it ran.
    """

    status: str
    detail: str
    halt_path: Optional[str] = None
    value: Any = None

    @property
    def ran(self) -> bool:
        """True when the fix ran."""
        return self.status == STATUS_RAN


def firing_timestamp(moment: Optional[datetime.datetime] = None) -> str:
    """The backup-directory timestamp: ``OVERWATCH_FIRING_TIMESTAMP`` when set,
    else *moment* (default now) as ``YYYY-MM-DDThh:mm:ssZ`` in UTC."""
    fixed = os.environ.get("OVERWATCH_FIRING_TIMESTAMP", "").strip()
    if fixed:
        return fixed
    when = moment or datetime.datetime.now(datetime.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backup_file(kanban_root: "Path | str", project: str, source: "Path | str", timestamp: str) -> str:
    """Copy *source* under the project's OVERWATCH backups and return the copy's path.

    The layout is ``overwatch_backup_file``'s (see the module docstring); a
    copy already taken for the same timestamp is kept, so the first copy in
    a firing is the pre-firing file.

    Raises:
        OSError: *source* is missing or the copy cannot be written.
    """
    project_root = Path(os.path.realpath(Path(kanban_root) / "projects" / project))
    src = Path(os.path.realpath(source))
    try:
        key = Path("project-root") / src.relative_to(project_root)
    except ValueError:
        key = Path("absolute") / str(src).lstrip("/")
    destination = project_root / "overwatch" / "backups" / timestamp / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        shutil.copy2(src, destination)
    return str(destination)


def _proc_start_time(pid: str) -> Optional[str]:
    """Field 22 of ``/proc/<pid>/stat`` (read past the command name), or None."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = stat.rsplit(") ", 1)[-1].split()
    value = fields[19] if len(fields) > 19 else ""
    return value if value.isdigit() else None


def repair_halt_state(halt_path: "Path | str") -> str:
    """``live`` for a repair HALT whose creator still runs, ``dead`` for one
    whose creator is gone, ``other`` for anything else (no file, an operator
    or CM HALT, a malformed marker)."""
    try:
        text = Path(halt_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "other"
    match = _MARKER_LINE_RE.search(text)
    if match is None:
        return "other"
    pid, start = match.group(1), match.group(2)
    return "live" if _proc_start_time(pid) == start else "dead"


def clear_dead_repair_halt(kanban_root: "Path | str", project: str, halt_path: "Path | str") -> bool:
    """Remove *halt_path* when it is a repair HALT whose creator is gone, logging
    the removal; True when it was removed. Any other HALT is never touched."""
    if repair_halt_state(halt_path) != "dead":
        return False
    try:
        os.unlink(halt_path)
    except OSError:
        return False
    try:
        log_action(
            str(kanban_root), project, _LOG_NAME, str(halt_path), "stale-repair-halt-removed",
            "removed the project HALT a stopped OVERWATCH repair left behind; an operator or CM HALT is never removed",
        )
    except OSError:
        pass
    return True


def _op_token(op: str) -> str:
    """*op* as one marker-safe token (``fn=`` takes no whitespace)."""
    return re.sub(r"\s+", "-", op.strip()) or "fix"


def write_repair_halt(halt_path: "Path | str", op: str) -> bool:
    """Create *halt_path* as this process's repair HALT; False when a HALT is already there."""
    token = _op_token(op)
    start = _proc_start_time(str(os.getpid())) or "0"
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# HALT created by OVERWATCH while it repairs this project ({token}); removed when the repair ends.",
        f"{REPAIR_HALT_MARKER} pid={os.getpid()} start={start} fn={token} at={at}",
        *provenance_lines(WRITER_OVERWATCH_REPAIR, f"OVERWATCH repair in progress ({token})"),
    ]
    try:
        fd = os.open(str(halt_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return True


def _remove_own_repair_halt(halt_path: Path) -> None:
    """Remove *halt_path* only while it still carries this process's marker."""
    try:
        text = halt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    match = _MARKER_LINE_RE.search(text)
    if match is not None and match.group(1) == str(os.getpid()):
        try:
            os.unlink(halt_path)
        except OSError:
            pass


def _narrate_deferral(kanban_root: Path, project: str, op: str, halt_path: Path) -> Envelope:
    """The one deferral narration: dual channel on stderr and an action-log row."""
    message = (
        f"fix deferred: HALT present at {halt_path}; OVERWATCH left {op} unrun and changed nothing. "
        "Fixes resume once the HALT is gone; only the operator removes an operator HALT."
    )
    # BLOCK-WORTHY: an invariant by design. A HALT is an operator's (or the
    # chain's) stop; OVERWATCH leaves the fix unrun rather than working
    # through it, changes nothing, and the caller moves on.
    # CENSUS-ANCHOR: halt-first-deferred-01
    emit_error_contract(
        "overwatch.fix_deferred_halt_present", message, f"cat {shlex.quote(str(halt_path))}", project=project
    )
    try:
        log_action(str(kanban_root), project, _LOG_NAME, op, "fix-deferred", message)
    except OSError:
        pass
    return Envelope(STATUS_DEFERRED_HALT, message, halt_path=str(halt_path))


def _decline_halt_overwatch(kanban_root: Path, project: str, op: str, flag: Path, from_env: bool) -> Envelope:
    """Decline every fix while OVERWATCH itself is halted."""
    where = "the HALT_OVERWATCH environment variable is set" if from_env else f"{flag} is present"
    message = f"OVERWATCH is halted ({where}); {op} was not run and nothing was changed."
    remedy = "printenv HALT_OVERWATCH" if from_env else f"cat {shlex.quote(str(flag))}"
    # BLOCK-WORTHY: an operator stop by design -- HALT_OVERWATCH stops every
    # OVERWATCH fix until the operator lifts it; nothing is changed.
    # CENSUS-ANCHOR: halt-first-halt-overwatch-01
    emit_error_contract("overwatch.halt_overwatch_set", message, remedy, project=project)
    return Envelope(STATUS_DECLINED_HALT_OVERWATCH, message, halt_path=None if from_env else str(flag))


def _decline_lock_busy(project: str, op: str, lock_file: Path, holder: str, waited: float) -> Envelope:
    """Decline a fix while a wake chain (or another fix) holds the repo lock."""
    message = (
        f"the repo lock of project {project} is held by {holder}"
        + (f" and stayed held for {waited:g}s" if waited > 0 else "")
        + f"; {op} was not run and nothing was changed. The next firing tries again."
    )
    # CONVERT: a busy lock means a wake chain is working the project; the fix
    # is left for the next firing and nothing stops.
    # CENSUS-ANCHOR: halt-first-lock-busy-01
    emit_error_contract("overwatch.repo_lock_busy", message, f"cat {shlex.quote(str(lock_file))}", project=project)
    return Envelope(STATUS_DECLINED_LOCK_BUSY, message)


def _lock_wait(value: Optional[float], env: Mapping[str, str]) -> float:
    if value is not None:
        return max(float(value), 0.0)
    raw = str(env.get("OVERWATCH_LOCK_WAIT_SECONDS", "")).strip()
    return float(raw) if raw.isdigit() else 0.0


def run_halt_first(
    kanban_root: "Path | str",
    project: str,
    fix: Callable[[], Any],
    *,
    op: str,
    task: str = "none",
    past_project_halt: bool = False,
    lock_wait_seconds: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Envelope:
    """Run *fix* under the HALT-first protocol (see the module docstring).

    Args:
        kanban_root:       The kanban root.
        project:           The one project the fix changes.
        fix:               What to run; its return value is ``Envelope.value``.
        op:                What the fix is (the lock's op, the repair HALT's
                           ``fn``, the action log's target).
        task:              The task the fix is for, or ``none``.
        past_project_halt: Run with a pre-existing project HALT left exactly
                           as it is; only for a fix whose subject is that
                           HALT. The fleet-wide HALT still defers.
        lock_wait_seconds: How long a contended repo lock is retried
                           (default ``OVERWATCH_LOCK_WAIT_SECONDS``, else 0).
        env:               The environment read for ``HALT_OVERWATCH``
                           (default ``os.environ``).

    Exceptions raised by *fix* propagate after the repair HALT is removed and
    the lock released.
    """
    root = Path(kanban_root)
    env = os.environ if env is None else env
    halt_overwatch = root / "HALT_OVERWATCH"
    if str(env.get("HALT_OVERWATCH", "")).strip():
        return _decline_halt_overwatch(root, project, op, halt_overwatch, True)
    if halt_overwatch.exists():
        return _decline_halt_overwatch(root, project, op, halt_overwatch, False)
    global_halt = root / "HALT"
    if global_halt.exists():
        return _narrate_deferral(root, project, op, global_halt)

    lock_file = repo_lock_path(root, project)
    wait = _lock_wait(lock_wait_seconds, env)
    deadline = time.monotonic() + wait
    while True:
        try:
            held = acquire_lock(
                lock_file, holder=LOCK_HOLDER, pid=os.getpid(), op=f"overwatch-halt-first-fix:{_op_token(op)}",
                task=(task or "none").strip() or "none",
            )
            break
        except LockContended as busy:
            if time.monotonic() >= deadline:
                return _decline_lock_busy(project, op, lock_file, busy.describe(), wait)
            time.sleep(_LOCK_POLL_SECONDS)
        except OpsError as exc:
            return _decline_lock_busy(project, op, lock_file, f"nobody this process can name ({exc})", 0)

    halt = root / "projects" / project / "HALT"
    created = False
    try:
        clear_dead_repair_halt(root, project, halt)
        if halt.exists():
            if not past_project_halt:
                return _narrate_deferral(root, project, op, halt)
        else:
            try:
                created = write_repair_halt(halt, op)
            except OSError as exc:
                return Envelope(
                    STATUS_FAILED, f"the repair HALT {halt} could not be written ({exc}); {op} was not run"
                )
            if not created:
                # A HALT appeared between the check and the create (a writer
                # not holding the repo lock): it defers the fix like any other.
                return _narrate_deferral(root, project, op, halt)
        value = fix()
        return Envelope(STATUS_RAN, f"{op} ran under the repo lock of project {project}", value=value)
    finally:
        if created:
            _remove_own_repair_halt(halt)
        held.release()

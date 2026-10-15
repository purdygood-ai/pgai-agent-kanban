#!/usr/bin/env python3
"""
lifecycle.py — shared logic behind the container lifecycle scripts
(``container-up.sh``, ``container-down.sh``, ``container-status.sh``).

The kanban runs either on the host (cron fires the wake scripts) or in one
named container (pseudocron fires them inside it), never both. This module
answers the questions those scripts ask, from text the scripts hand it:

* **Container state** — absent / stopped / running, the image it runs and
  its uptime, parsed from ``<engine> container inspect NAME`` output.
* **Scheduler** — whether the in-container pseudocron is running, parsed
  from the container's process table (``<engine> exec NAME ps -eo
  pid=,args=``).
* **Heartbeat** — the age of the last ``fired (minute=NN)`` line in
  ``<kanban root>/logs/pseudocron.log``.
* **Host-crontab wake lines** — the non-comment crontab lines that invoke
  a kanban wake script (``crontab -l`` output).
* **Agent sessions** — the agent dispatches still running in the
  container, with role and task ID.
* **Wake drivers** — the wake scripts (``wake-batch.sh``, ``wake.sh``,
  ``wake-now.sh``) still running in the container. A driver outlives its
  agent CLI: it finalizes the task after the CLI exits and may start the
  next one, so the container is busy while either kind runs.
* **OVERWATCH sweeps** — ``overwatch-sweep.sh`` still running in the
  container. A wake starts one detached after blocking a task; stopped
  mid-repair it would leave the project HALT its repair created, so the
  container is busy while one runs too.

This module never runs a container engine. The bash scripts make every
engine call through ``pgai_docker`` (``scripts/lib/docker_use.sh``), so the
docker-use log records it, and pass the output here. The module reads only
files on the kanban root, which is bind-mounted at the same path on the
host and in the container.

Agent sessions
--------------
Each agent dispatch writes a liveness record,
``<kanban root>/locks/task-liveness/<TASK-ID>.record``, holding
``holder=<provider>``, ``pid=<pid>``, ``op=dispatch`` and ``task=<TASK-ID>``.
Records outlive their process, and a pid written inside the container
belongs to the container's PID namespace, so the host cannot judge it with
``kill -0``. A record is therefore a *running session* only when its pid
appears in the container's own process table AND that process's command
line names the record's task ID or its provider — the second check keeps a
recycled pid, or a record written on the host, from reading as a session.
The role is the task ID's leading role word (``CODER`` for
``CODER-YYYYMMDD-NNN-slug``).

Wake drivers
------------
A wake driver is a process in the container's table whose program is one
of the wake scripts, or a shell (``bash``, ``sh``, ``dash``) whose first
non-option argument is one -- the shape a script started through its
``#!/usr/bin/env bash`` line, or through ``bash -c``, has in ``ps``. A
process that merely mentions a wake script further along its command line
(an editor, ``tail`` on a log) is not one. Drivers need no liveness record:
the process table alone says whether one still runs. An OVERWATCH sweep is
recognised the same way, by ``overwatch-sweep.sh`` in that position.

Command line
------------
``python3 -m pgai_agent_kanban.container.lifecycle VERB [options]``; each
verb reads engine or crontab output from ``--input FILE`` (``-`` or omitted
reads stdin). Text output is ``key=value`` lines; ``--format json`` prints
one JSON object. Exit codes are shared by every verb:

  0  the answer is "yes" (running / found / present)
  3  the answer is "no"  (absent or stopped / none found)
  1  the input could not be read, or the engine query failed
  2  usage error

Verbs:

  container-state   container state from ``container inspect`` output;
                    ``--exit-code`` / ``--stderr-file`` carry the engine's
                    status so a "no such container" failure reads as absent
                    and any other failure exits 1.  0 running, 3 otherwise.
  scheduler         pseudocron processes in a ``ps -eo pid=,args=`` table.
                    0 when at least one runs, 3 when none.
  heartbeat         last heartbeat in ``logs/pseudocron.log``;
                    ``--since`` ignores heartbeats before a start time.
                    0 when one is found, 3 when none.
  crontab-wake-lines  prints each kanban wake line of a crontab.
                    0 when there is at least one, 3 when none.
  sessions          running agent sessions, wake drivers and OVERWATCH
                    sweeps (needs ``--kanban-root`` and the ``ps`` table):
                    one ``role= task= pid= provider=`` line per session,
                    then one ``driver= pid=`` line per wake driver, then
                    one ``sweep= pid=`` line per OVERWATCH sweep.  0 when
                    the container is busy (at least one of any kind runs),
                    3 when it is idle.
  status            the whole status block (or ``--format json``) from the
                    inspect output, the ``ps`` table, the crontab and the
                    heartbeat log.  0 when the container runs with a live
                    scheduler, 3 otherwise, 1 when the state query failed.

Run ``... lifecycle VERB --help`` for each verb's options.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Iterable, Optional, Sequence

# Run by file path or loaded from a file outside its package, this module
# has no parent package and the interpreter puts only its own directory on
# sys.path, so the package imports below would not resolve. Put this file's
# own package root -- the directory holding the pgai_agent_kanban/ this file
# sits in -- at the front of sys.path so they resolve from the same tree,
# never another. Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.env import resolve_kanban_root  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.ops.locks import (  # noqa: E402
    format_age_seconds,
    lock_field,
    read_provenance,
)

__all__ = [
    "AgentSession",
    "ContainerQueryError",
    "ContainerState",
    "Heartbeat",
    "Process",
    "STATE_ABSENT",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "StatusReport",
    "OVERWATCH_SWEEP_SCRIPTS",
    "OverwatchSweep",
    "WAKE_DRIVER_SCRIPTS",
    "WakeDriver",
    "build_status_report",
    "classify_query_failure",
    "crontab_wake_lines",
    "heartbeat_log_path",
    "list_agent_sessions",
    "main",
    "overwatch_sweep_processes",
    "parse_container_state",
    "parse_heartbeat",
    "parse_process_table",
    "read_heartbeat",
    "render_status_text",
    "scheduler_processes",
    "task_liveness_dir",
    "wake_driver_processes",
]

STATE_ABSENT = "absent"
STATE_STOPPED = "stopped"
STATE_RUNNING = "running"

EXIT_YES = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO = 3

#: The scheduler log, relative to the kanban root (docs/docker.md, step 7).
HEARTBEAT_LOG_RELATIVE = pathlib.PurePosixPath("logs") / "pseudocron.log"

#: The per-dispatch liveness records, relative to the kanban root.
TASK_LIVENESS_RELATIVE = pathlib.PurePosixPath("locks") / "task-liveness"

#: The wake scripts whose running process keeps the container busy: each
#: dispatches agents and finalizes their tasks after the agent CLI exits.
WAKE_DRIVER_SCRIPTS = frozenset({"wake-batch.sh", "wake.sh", "wake-now.sh"})

#: The OVERWATCH sweep runner, whose running process keeps the container busy:
#: a sweep stopped mid-repair leaves the project HALT its repair created.
OVERWATCH_SWEEP_SCRIPTS = frozenset({"overwatch-sweep.sh"})

#: Shells that run a wake script named as their first non-option argument.
_DRIVER_SHELLS = frozenset({"bash", "sh", "dash"})

#: Bytes read per step when scanning the heartbeat log backwards.
_TAIL_CHUNK_BYTES = 64 * 1024


class ContainerQueryError(Exception):
    """The engine's answer to a state query could not be read as a state.

    Raised for a failed query that is not "no such container" (the daemon
    is down, permission is denied) and for output that is not inspect JSON.
    The message names what was received so the caller can print it as-is.
    """


@dataclasses.dataclass(frozen=True)
class ContainerState:
    """One container's state as the engine reports it.

    ``image``, ``started_at`` and ``uptime_seconds`` are ``None`` when the
    engine did not report them; ``uptime_seconds`` is set only while the
    container runs. ``engine_status`` is the engine's own status word
    (``running``, ``exited``, ``created`` ...), empty when absent.
    """

    name: str
    state: str
    image: Optional[str] = None
    started_at: Optional[datetime.datetime] = None
    uptime_seconds: Optional[int] = None
    engine_status: str = ""

    @property
    def running(self) -> bool:
        """True when the engine reports the container as running."""
        return self.state == STATE_RUNNING

    def to_dict(self) -> dict:
        """Return the container state as a JSON-ready dict.

        Keys: ``name``, ``state``, ``image``, ``started_at`` (ISO-8601 or None),
        ``uptime_seconds``, ``uptime`` (human-readable age) and ``engine_status``.
        """
        return {
            "name": self.name,
            "state": self.state,
            "image": self.image,
            "started_at": format_iso(self.started_at) if self.started_at else None,
            "uptime_seconds": self.uptime_seconds,
            "uptime": _format_age(self.uptime_seconds),
            "engine_status": self.engine_status,
        }


@dataclasses.dataclass(frozen=True)
class Process:
    """One row of a ``ps -eo pid=,args=`` table."""

    pid: int
    args: str


@dataclasses.dataclass(frozen=True)
class Heartbeat:
    """The last scheduler heartbeat found in the pseudocron log.

    ``last_fired_at`` is ``None`` when the log is missing or holds no
    ``fired`` line (at or after ``since``, when one was given).
    """

    last_fired_at: Optional[datetime.datetime]
    age_seconds: Optional[int]

    @property
    def found(self) -> bool:
        """True when a scheduler heartbeat timestamp was read."""
        return self.last_fired_at is not None

    def to_dict(self) -> dict:
        """Return the heartbeat as a JSON-ready dict.

        Keys: ``found``, ``last_fired_at`` (ISO-8601 or None), ``age_seconds`` and
        ``age`` (human-readable).
        """
        return {
            "found": self.found,
            "last_fired_at": format_iso(self.last_fired_at) if self.last_fired_at else None,
            "age_seconds": self.age_seconds,
            "age": _format_age(self.age_seconds),
        }


@dataclasses.dataclass(frozen=True)
class AgentSession:
    """An agent dispatch still running in the container."""

    role: str
    task_id: str
    pid: int
    provider: str

    def to_dict(self) -> dict:
        """Return the session as a dict with keys ``role``, ``task_id``, ``pid`` and ``provider``."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class WakeDriver:
    """A wake script still running in the container: its pid and the
    script's file name (``wake-batch.sh``, ``wake.sh`` or ``wake-now.sh``)."""

    pid: int
    script: str

    def to_dict(self) -> dict:
        """Return the wake driver as a dict with keys ``pid`` and ``script``."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class OverwatchSweep(WakeDriver):
    """An OVERWATCH sweep still running in the container: its pid and the
    script's file name (``overwatch-sweep.sh``)."""


@dataclasses.dataclass(frozen=True)
class StatusReport:
    """Everything ``container-status.sh`` shows, in one value.

    ``errors`` holds operator-facing problem lines (host cron wake lines
    while the container runs; more than one scheduler). ``exit_code`` is 0
    when the container runs with a live scheduler, 3 otherwise.
    :meth:`to_dict` reports the crontab's wake lines by count only.
    """

    container: ContainerState
    scheduler_pids: tuple[int, ...]
    heartbeat: Heartbeat
    sessions: tuple[AgentSession, ...]
    crontab_wake_lines: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def scheduler_running(self) -> bool:
        """True when at least one scheduler process id was found."""
        return bool(self.scheduler_pids)

    @property
    def exit_code(self) -> int:
        """Return ``EXIT_YES`` (0) when the container and its scheduler are both running, else ``EXIT_NO`` (3)."""
        return EXIT_YES if self.container.running and self.scheduler_running else EXIT_NO

    def to_dict(self) -> dict:
        """Return the whole status report as a JSON-ready dict.

        Keys: ``container`` (``ContainerState.to_dict``), ``scheduler``
        (``running``, ``pids``), ``heartbeat`` (``Heartbeat.to_dict``),
        ``agent_sessions`` (a list of ``AgentSession.to_dict``), ``host_crontab``
        (``wake_lines_present``, ``wake_line_count`` -- the lines themselves are
        never included), ``errors`` and ``exit_code``.
        """
        return {
            "container": self.container.to_dict(),
            "scheduler": {
                "running": self.scheduler_running,
                "pids": list(self.scheduler_pids),
            },
            "heartbeat": self.heartbeat.to_dict(),
            "agent_sessions": [s.to_dict() for s in self.sessions],
            # The wake lines are counted, never echoed: a crontab line may
            # carry an operator's environment assignments.
            "host_crontab": {
                "wake_lines_present": bool(self.crontab_wake_lines),
                "wake_line_count": len(self.crontab_wake_lines),
            },
            "errors": list(self.errors),
            "exit_code": self.exit_code,
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _format_age(seconds: Optional[int]) -> Optional[str]:
    return None if seconds is None else format_age_seconds(seconds)


def _seconds_between(earlier: datetime.datetime, later: datetime.datetime) -> int:
    """Whole seconds from *earlier* to *later*, never negative."""
    return max(int((later - earlier).total_seconds()), 0)


# Engines write sub-second precision of up to nine digits; the stdlib
# parser keeps microseconds, so anything past six digits is dropped first.
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def _parse_engine_time(value: object) -> Optional[datetime.datetime]:
    """Parse an engine timestamp; ``None`` for a blank, unparseable or
    zero-value (``0001-01-01...``, a never-started container) time."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = try_parse_iso(_FRACTION_RE.sub(r"\1", value.strip()))
    if parsed is None or parsed.year <= 1:
        return None
    return parsed


# ---------------------------------------------------------------------------
# Container state
# ---------------------------------------------------------------------------

# How docker and podman word "that container does not exist" on a failed
# inspect: "Error: No such object: x", "Error response from daemon: No such
# container: x", "Error: no such container x".
_NO_SUCH_RE = re.compile(r"no\s+such\s+(?:object|container)", re.IGNORECASE)


def classify_query_failure(stderr: str, exit_code: int) -> str:
    """Classify a failed ``container inspect``.

    Returns ``STATE_ABSENT`` when the engine said the container does not
    exist; raises :class:`ContainerQueryError` for any other failure, whose
    message carries the engine's exit code and its first stderr line.
    """
    if _NO_SUCH_RE.search(stderr or ""):
        return STATE_ABSENT
    first = next((ln.strip() for ln in (stderr or "").splitlines() if ln.strip()), "")
    detail = first or "no error text"
    raise ContainerQueryError(f"container state query failed (engine exit {exit_code}): {detail}")


def parse_container_state(
    inspect_output: str,
    *,
    name: str = "",
    now: Optional[datetime.datetime] = None,
) -> ContainerState:
    """Parse ``<engine> container inspect NAME`` output into a state.

    Empty output, or an empty JSON array, is ``absent``. The first object's
    ``State.Status`` decides the rest: ``running`` is running, every other
    status (``exited``, ``created``, ``paused``, ``restarting``, ``dead``,
    podman's ``stopped`` / ``configured``) is stopped -- a container in
    any of those runs no scheduler. ``State.Running`` stands in when
    ``State.Status`` is missing. The image is ``Config.Image`` (the name the
    container was run with), else podman's ``ImageName``, else ``Image``.

    Raises :class:`ContainerQueryError` when the output is not inspect JSON.
    """
    text = (inspect_output or "").strip()
    if not text:
        return ContainerState(name=name, state=STATE_ABSENT)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContainerQueryError(
            f"container state query returned output that is not inspect JSON ({exc.msg}): {text[:120]!r}"
        ) from None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ContainerQueryError(f"container inspect output is not a JSON array: {text[:120]!r}")
    if not data:
        return ContainerState(name=name, state=STATE_ABSENT)
    obj = data[0]
    if not isinstance(obj, dict):
        raise ContainerQueryError(f"container inspect entry is not a JSON object: {text[:120]!r}")

    state_obj = obj.get("State") if isinstance(obj.get("State"), dict) else {}
    status = str(state_obj.get("Status") or "").strip().lower()
    if status:
        running = status == "running"
    else:
        running = state_obj.get("Running") is True
        status = "running" if running else ""

    config = obj.get("Config") if isinstance(obj.get("Config"), dict) else {}
    image = config.get("Image") or obj.get("ImageName") or obj.get("Image") or None
    reported_name = str(obj.get("Name") or "").lstrip("/")
    started_at = _parse_engine_time(state_obj.get("StartedAt"))

    uptime = None
    if running and started_at is not None:
        uptime = _seconds_between(started_at, now or _utc_now())
    return ContainerState(
        name=name or reported_name,
        state=STATE_RUNNING if running else STATE_STOPPED,
        image=str(image) if image else None,
        started_at=started_at,
        uptime_seconds=uptime,
        engine_status=status,
    )


# ---------------------------------------------------------------------------
# Process table and scheduler
# ---------------------------------------------------------------------------

_PS_ROW_RE = re.compile(r"^\s*(\d+)\s+(\S.*?)\s*$")


def parse_process_table(ps_output: str) -> list[Process]:
    """Parse ``ps -eo pid=,args=`` output into processes.

    Lines that do not start with a pid (a header, a blank line, an error
    message) are skipped, so ``ps -eo pid,args`` with its header parses too.
    """
    processes = []
    for line in (ps_output or "").splitlines():
        match = _PS_ROW_RE.match(line)
        if match:
            processes.append(Process(pid=int(match.group(1)), args=match.group(2)))
    return processes


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def scheduler_processes(processes: Iterable[Process]) -> list[Process]:
    """The pseudocron scheduler processes in a process table.

    A scheduler is a process whose program is ``pseudocron.py`` itself or a
    Python interpreter running it. The tmux server and the ``sh -c`` wrapper
    that started it also mention ``pseudocron.py`` in their arguments and
    are not counted, so one scheduler reads as one and two read as two.
    """
    found = []
    for proc in processes:
        tokens = proc.args.split()
        if not tokens:
            continue
        program = _basename(tokens[0])
        if program == "pseudocron.py":
            found.append(proc)
            continue
        if program.startswith("python") and len(tokens) > 1:
            script = next((t for t in tokens[1:] if not t.startswith("-")), "")
            if _basename(script) == "pseudocron.py":
                found.append(proc)
    return found


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

# pseudocron writes one line per dispatch:
#   2026-10-11T07:49:04Z fired (minute=49): scripts/wake-batch.sh ...
_FIRED_RE = re.compile(r"^\s*(?P<ts>\S+)\s+fired\s*\(\s*minute\s*=\s*\d+\s*\)")


def heartbeat_log_path(kanban_root: os.PathLike | str) -> pathlib.Path:
    """``<kanban root>/logs/pseudocron.log``."""
    return pathlib.Path(kanban_root) / HEARTBEAT_LOG_RELATIVE


def _fired_at(line: str) -> Optional[datetime.datetime]:
    match = _FIRED_RE.match(line)
    if not match:
        return None
    return try_parse_iso(match.group("ts"))


def _last_fired(lines: Iterable[str]) -> Optional[datetime.datetime]:
    """The timestamp of the last ``fired`` line in *lines* (file order)."""
    last = None
    for line in lines:
        fired = _fired_at(line)
        if fired is not None:
            last = fired
    return last


def _heartbeat_from(
    fired: Optional[datetime.datetime],
    since: Optional[datetime.datetime],
    now: Optional[datetime.datetime],
) -> Heartbeat:
    if fired is None or (since is not None and fired < since):
        return Heartbeat(last_fired_at=None, age_seconds=None)
    return Heartbeat(last_fired_at=fired, age_seconds=_seconds_between(fired, now or _utc_now()))


def parse_heartbeat(
    log_text: str,
    *,
    now: Optional[datetime.datetime] = None,
    since: Optional[datetime.datetime] = None,
) -> Heartbeat:
    """The last heartbeat in pseudocron log text.

    The heartbeat is the last ``<timestamp> fired (minute=NN): ...`` line;
    startup banners and error lines are not heartbeats. With *since*, a
    heartbeat older than *since* does not count (a scheduler just started
    has not beaten yet even when an earlier run left ``fired`` lines).
    """
    return _heartbeat_from(_last_fired((log_text or "").splitlines()), since, now)


def _last_fired_in_file(path: pathlib.Path) -> Optional[datetime.datetime]:
    """Scan *path* backwards in chunks for its last ``fired`` line, so a
    long-running log is not read whole on every poll."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        position = fh.tell()
        carry = b""
        while position > 0:
            step = min(_TAIL_CHUNK_BYTES, position)
            position -= step
            fh.seek(position)
            block = fh.read(step) + carry
            lines = block.split(b"\n")
            # The first piece may be the tail of a line that starts in the
            # previous chunk; hold it back unless this is the file's start.
            carry = lines.pop(0) if position > 0 else b""
            for raw in reversed(lines):
                fired = _fired_at(raw.decode("utf-8", errors="replace"))
                if fired is not None:
                    return fired
        return None


def read_heartbeat(
    log_path: os.PathLike | str,
    *,
    now: Optional[datetime.datetime] = None,
    since: Optional[datetime.datetime] = None,
) -> Heartbeat:
    """The last heartbeat in the pseudocron log at *log_path*.

    A missing or unreadable log has no heartbeat. See :func:`parse_heartbeat`.
    """
    path = pathlib.Path(log_path)
    try:
        fired = _last_fired_in_file(path)
    except OSError:
        fired = None
    return _heartbeat_from(fired, since, now)


# ---------------------------------------------------------------------------
# Host crontab
# ---------------------------------------------------------------------------

# A crontab environment assignment: NAME=value (optionally spaced).
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")

# A kanban wake script reached through the install's scripts/ directory,
# as the shipped templates do: <root>/scripts/wake-batch.sh, and the other
# wake entry points beside it.
_WAKE_SCRIPT_RE = re.compile(r"(?:^|[\s/'\"=;&|(])scripts/wake(?:-batch|-now)?\.sh(?=$|[\s'\";&|)])")


def _cron_command(line: str) -> Optional[str]:
    """The command part of a crontab job line, or ``None`` when the line is
    not a job (too few fields for a schedule)."""
    fields = line.split(None, 1 if line.startswith("@") else 5)
    expected = 2 if line.startswith("@") else 6
    if len(fields) < expected:
        return None
    return fields[-1]


def crontab_wake_lines(crontab_text: str) -> list[str]:
    """The kanban wake lines in crontab text, stripped, in file order.

    A wake line is a job line (a five-field or ``@keyword`` schedule then a
    command) whose command invokes a kanban wake script under a
    ``scripts/`` directory -- ``scripts/wake-batch.sh`` as every shipped
    ``crontab-*.example`` does, or ``scripts/wake.sh`` /
    ``scripts/wake-now.sh``. Blank lines, comments (including a
    commented-out wake line), environment assignments and every other job
    are not wake lines.
    """
    found = []
    for raw in (crontab_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or _ENV_ASSIGNMENT_RE.match(line):
            continue
        command = _cron_command(line)
        if command is not None and _WAKE_SCRIPT_RE.search(command):
            found.append(line)
    return found


# ---------------------------------------------------------------------------
# Agent sessions
# ---------------------------------------------------------------------------

# Task IDs open with their role word: <ROLE>-YYYYMMDD-NNN-slug.
_TASK_ROLE_RE = re.compile(r"^([A-Za-z]+)-\d{8}-")


def task_liveness_dir(kanban_root: os.PathLike | str) -> pathlib.Path:
    """``<kanban root>/locks/task-liveness``."""
    return pathlib.Path(kanban_root) / TASK_LIVENESS_RELATIVE


def _role_of(task_id: str) -> str:
    match = _TASK_ROLE_RE.match(task_id)
    return match.group(1).upper() if match else ""


def _names_session(args: str, task_id: str, provider: str) -> bool:
    """True when a command line belongs to the dispatch a record describes."""
    if task_id and task_id in args:
        return True
    if not provider:
        return False
    return any(_basename(token) == provider for token in args.split())


def list_agent_sessions(
    kanban_root: os.PathLike | str,
    processes: Iterable[Process],
) -> list[AgentSession]:
    """The agent sessions running in the container, ordered by task ID.

    A liveness record under ``locks/task-liveness/`` is a running session
    when its pid is in *processes* (the container's own table) and that
    process's command line names the record's task ID or its provider (see
    the module docstring). Records without a usable pid or task are skipped.
    """
    by_pid = {proc.pid: proc for proc in processes}
    directory = task_liveness_dir(kanban_root)
    try:
        records = sorted(p for p in directory.iterdir() if p.name.endswith(".record"))
    except OSError:
        return []
    sessions = []
    for path in records:
        record = read_provenance(path)
        pid_text = lock_field(record, "pid").strip()
        task_id = lock_field(record, "task").strip() or path.name[: -len(".record")]
        provider = lock_field(record, "holder").strip()
        if not pid_text.isdigit() or not task_id:
            continue
        proc = by_pid.get(int(pid_text))
        if proc is None or not _names_session(proc.args, task_id, provider):
            continue
        sessions.append(
            AgentSession(role=_role_of(task_id), task_id=task_id, pid=proc.pid, provider=provider)
        )
    return sorted(sessions, key=lambda s: s.task_id)


def _driver_script(args: str, scripts: frozenset = WAKE_DRIVER_SCRIPTS) -> str:
    """The script in *scripts* a command line runs, or ``""`` when it runs none."""
    tokens = args.split()
    if not tokens:
        return ""
    program = _basename(tokens[0])
    if program in scripts:
        return program
    if program in _DRIVER_SHELLS:
        script = next((t for t in tokens[1:] if not t.startswith("-")), "")
        if _basename(script) in scripts:
            return _basename(script)
    return ""


def wake_driver_processes(processes: Iterable[Process]) -> list[WakeDriver]:
    """The wake drivers in a process table, ordered by pid.

    Each process running a wake script counts once, so a ``wake-batch.sh``
    and the ``wake.sh`` it started read as two (see the module docstring
    for what counts as running one).
    """
    found = []
    for proc in processes:
        script = _driver_script(proc.args)
        if script:
            found.append(WakeDriver(pid=proc.pid, script=script))
    return sorted(found, key=lambda d: d.pid)


def overwatch_sweep_processes(processes: Iterable[Process]) -> list[OverwatchSweep]:
    """The OVERWATCH sweeps in a process table, ordered by pid.

    A process runs a sweep when its program, or the first non-option
    argument of a ``bash``/``sh``/``dash`` program, is ``overwatch-sweep.sh``
    -- the same reading :func:`wake_driver_processes` applies to wake scripts.
    """
    found = []
    for proc in processes:
        script = _driver_script(proc.args, OVERWATCH_SWEEP_SCRIPTS)
        if script:
            found.append(OverwatchSweep(pid=proc.pid, script=script))
    return sorted(found, key=lambda d: d.pid)


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------


def build_status_report(
    container: ContainerState,
    *,
    processes: Sequence[Process] = (),
    kanban_root: os.PathLike | str | None = None,
    crontab_text: str = "",
    now: Optional[datetime.datetime] = None,
) -> StatusReport:
    """Assemble the status of the kanban container.

    *processes* is the container's process table; it is ignored unless the
    container runs (a stopped container runs no scheduler and no session).
    The heartbeat is read from *kanban_root*'s pseudocron log, so it is
    reported even when the container is down (the age shows how long ago
    the board last beat).
    """
    now = now or _utc_now()
    live = list(processes) if container.running else []
    schedulers = tuple(p.pid for p in scheduler_processes(live))
    sessions: tuple[AgentSession, ...] = ()
    heartbeat = Heartbeat(last_fired_at=None, age_seconds=None)
    if kanban_root is not None:
        sessions = tuple(list_agent_sessions(kanban_root, live))
        heartbeat = read_heartbeat(heartbeat_log_path(kanban_root), now=now)
    wake_lines = tuple(crontab_wake_lines(crontab_text))

    errors = []
    if container.running and wake_lines:
        errors.append(
            f"host crontab carries {len(wake_lines)} kanban wake line(s) while container "
            f"{container.name} runs: two schedulers drive one board; remove the host wake lines"
        )
    if len(schedulers) > 1:
        errors.append(
            f"{len(schedulers)} schedulers run in container {container.name} "
            f"(pids {', '.join(str(p) for p in schedulers)}): exactly one may run"
        )
    return StatusReport(
        container=container,
        scheduler_pids=schedulers,
        heartbeat=heartbeat,
        sessions=sessions,
        crontab_wake_lines=wake_lines,
        errors=tuple(errors),
    )


def _label(text: str) -> str:
    return f"{text + ':':<17}"


def render_status_text(report: StatusReport) -> str:
    """The plain status block ``container-status.sh`` prints."""
    c = report.container
    lines = [f"{_label('container')}{c.name or '-'} ({c.state})"]
    lines.append(f"{_label('image')}{c.image or '-'}")
    lines.append(f"{_label('uptime')}{_format_age(c.uptime_seconds) or '-'}")
    if report.scheduler_running:
        pids = ", ".join(str(p) for p in report.scheduler_pids)
        lines.append(f"{_label('scheduler')}running (pid {pids})")
    else:
        lines.append(f"{_label('scheduler')}not running")
    hb = report.heartbeat
    if hb.found:
        lines.append(f"{_label('last heartbeat')}{_format_age(hb.age_seconds)} ago ({format_iso(hb.last_fired_at)})")
    else:
        lines.append(f"{_label('last heartbeat')}none")
    lines.append(f"{_label('agent sessions')}{len(report.sessions)}")
    for session in report.sessions:
        lines.append(f"  {session.role or '-':<8} {session.task_id}")
    count = len(report.crontab_wake_lines)
    lines.append(f"{_label('host crontab')}{count} kanban wake line(s)" if count else f"{_label('host crontab')}no kanban wake lines")
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_input(path: Optional[str]) -> str:
    if path in (None, "-"):
        return sys.stdin.read()
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def _read_optional(path: Optional[str]) -> str:
    """Text of *path*, or ``""`` when no path was given."""
    if not path:
        return ""
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def _parse_time_arg(value: Optional[str], flag: str) -> Optional[datetime.datetime]:
    if value is None:
        return None
    parsed = try_parse_iso(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"{flag} expects an ISO-8601 timestamp such as 2026-10-11T07:49:04Z, got {value!r}")
    return parsed


def _emit(fmt: str, data: dict, text_lines: Sequence[str]) -> None:
    if fmt == "json":
        print(json.dumps(data, indent=2))
    elif text_lines:
        print("\n".join(text_lines))


def _kv(data: dict) -> list[str]:
    """``key=value`` lines for a flat dict; ``None`` renders empty."""
    out = []
    for key, value in data.items():
        if isinstance(value, bool):
            value = "yes" if value else "no"
        elif isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        elif value is None:
            value = ""
        out.append(f"{key}={value}")
    return out


def _container_state_from_args(args: argparse.Namespace, now: Optional[datetime.datetime]) -> ContainerState:
    if args.exit_code:
        # A failed query has no state to parse; its stderr decides between
        # absent and a failed query, and the inspect output is not read.
        classify_query_failure(_read_optional(args.stderr_file), args.exit_code)
        return ContainerState(name=args.name, state=STATE_ABSENT)
    return parse_container_state(_read_input(args.input), name=args.name, now=now)


def _add_common(p: argparse.ArgumentParser, *, input_help: str) -> None:
    p.add_argument("--input", metavar="FILE", default=None, help=f"{input_help} (default: stdin; '-' reads stdin)")
    p.add_argument("--format", choices=("text", "json"), default="text", help="output form (default: text key=value lines)")
    p.add_argument("--now", metavar="ISO", default=None, help="the current time, ISO-8601 (default: the clock); fixes ages for tests")


def _add_state_query(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", default="", help="the container name, reported back (default: the name in the inspect output)")
    p.add_argument(
        "--exit-code", type=int, default=0, metavar="N",
        help="the engine's exit status for the inspect; non-zero reads as absent when the engine said 'no such container', else as a failed query (exit 1)",
    )
    p.add_argument("--stderr-file", metavar="FILE", default=None, help="file holding the engine's stderr for the inspect")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.container.lifecycle",
        description=(
            "Answer the container lifecycle scripts' questions from engine, crontab and log output. "
            "Exit 0 = yes, 3 = no, 1 = unreadable input or failed engine query, 2 = usage error."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="VERB")

    p = sub.add_parser(
        "container-state", help="container state from 'container inspect' output",
        description="Print state, image, started_at and uptime from '<engine> container inspect NAME' output. Exit 0 running, 3 absent or stopped, 1 failed query.",
    )
    _add_common(p, input_help="file holding the inspect output")
    _add_state_query(p)

    p = sub.add_parser(
        "scheduler", help="pseudocron processes in a 'ps -eo pid=,args=' table",
        description="Print whether a pseudocron scheduler runs and its pids. Exit 0 when at least one runs, 3 when none.",
    )
    _add_common(p, input_help="file holding the container's 'ps -eo pid=,args=' output")

    p = sub.add_parser(
        "heartbeat", help="last heartbeat in logs/pseudocron.log",
        description="Print the last 'fired (minute=NN)' heartbeat and its age. Exit 0 when one is found, 3 when none.",
    )
    p.add_argument("--kanban-root", metavar="PATH", default=None, help="kanban root whose logs/pseudocron.log is read (default: $PGAI_AGENT_KANBAN_ROOT_PATH)")
    p.add_argument("--log", metavar="FILE", default=None, help="read this log instead of the kanban root's")
    p.add_argument("--since", metavar="ISO", default=None, help="ignore heartbeats before this ISO-8601 time")
    p.add_argument("--format", choices=("text", "json"), default="text", help="output form (default: text key=value lines)")
    p.add_argument("--now", metavar="ISO", default=None, help="the current time, ISO-8601 (default: the clock)")

    p = sub.add_parser(
        "crontab-wake-lines", help="kanban wake lines in crontab text",
        description="Print each kanban wake line of a crontab ('crontab -l' output). Exit 0 when there is at least one, 3 when none.",
    )
    _add_common(p, input_help="file holding the crontab text")

    p = sub.add_parser(
        "sessions", help="agent sessions, wake drivers and OVERWATCH sweeps running in the container",
        description=(
            "Print one 'role=R task=T pid=P provider=X' line per running agent session, then one "
            "'driver=SCRIPT pid=P' line per running wake driver (wake-batch.sh, wake.sh, wake-now.sh), "
            "then one 'sweep=overwatch-sweep.sh pid=P' line per running OVERWATCH sweep. "
            "Exit 0 when the container is busy (at least one of any kind runs), 3 when it is idle."
        ),
    )
    _add_common(p, input_help="file holding the container's 'ps -eo pid=,args=' output")
    p.add_argument("--kanban-root", metavar="PATH", default=None, help="kanban root holding locks/task-liveness/ (default: $PGAI_AGENT_KANBAN_ROOT_PATH)")

    p = sub.add_parser(
        "status", help="the whole container status block",
        description="Print the container status block (or --format json). Exit 0 running with a live scheduler, 3 otherwise, 1 failed state query.",
    )
    _add_common(p, input_help="file holding the 'container inspect' output")
    _add_state_query(p)
    p.add_argument("--ps-file", metavar="FILE", default=None, help="file holding the container's 'ps -eo pid=,args=' output (omit when the container is not running)")
    p.add_argument("--crontab-file", metavar="FILE", default=None, help="file holding the host 'crontab -l' output (omit for none)")
    p.add_argument("--kanban-root", metavar="PATH", default=None, help="kanban root for the heartbeat log and agent sessions (default: $PGAI_AGENT_KANBAN_ROOT_PATH)")
    return parser


def _kanban_root_arg(value: Optional[str]) -> Optional[str]:
    """Return the kanban root a verb reads, or None when there is none.

    An explicit ``--kanban-root`` wins. Otherwise the root comes from the
    canonical resolver (``$PGAI_AGENT_KANBAN_ROOT_PATH``, made absolute);
    when that variable is unset or empty the resolver refuses, and the
    root is None so each verb decides whether it needs one (``heartbeat``
    and ``sessions`` print a usage error, ``status`` reports without it).
    """
    if value:
        return value
    try:
        return str(resolve_kanban_root())
    except RuntimeError:
        return None


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    now = _parse_time_arg(getattr(args, "now", None), "--now")

    if args.command == "container-state":
        state = _container_state_from_args(args, now)
        _emit(args.format, state.to_dict(), _kv(state.to_dict()))
        return EXIT_YES if state.running else EXIT_NO

    if args.command == "scheduler":
        pids = [p.pid for p in scheduler_processes(parse_process_table(_read_input(args.input)))]
        data = {"scheduler": bool(pids), "pids": pids}
        _emit(args.format, data, _kv(data))
        return EXIT_YES if pids else EXIT_NO

    if args.command == "heartbeat":
        since = _parse_time_arg(args.since, "--since")
        if args.log:
            log_path = pathlib.Path(args.log)
        else:
            root = _kanban_root_arg(args.kanban_root)
            if root is None:
                parser.error("heartbeat needs --log or --kanban-root (or PGAI_AGENT_KANBAN_ROOT_PATH set)")
            log_path = heartbeat_log_path(root)
        hb = read_heartbeat(log_path, now=now, since=since)
        data = {"heartbeat": hb.found, **{k: v for k, v in hb.to_dict().items() if k != "found"}}
        _emit(args.format, data, _kv(data))
        return EXIT_YES if hb.found else EXIT_NO

    if args.command == "crontab-wake-lines":
        lines = crontab_wake_lines(_read_input(args.input))
        _emit(args.format, {"wake_lines": lines}, lines)
        return EXIT_YES if lines else EXIT_NO

    if args.command == "sessions":
        root = _kanban_root_arg(args.kanban_root)
        if root is None:
            parser.error("sessions needs --kanban-root (or PGAI_AGENT_KANBAN_ROOT_PATH set)")
        processes = parse_process_table(_read_input(args.input))
        sessions = list_agent_sessions(root, processes)
        drivers = wake_driver_processes(processes)
        sweeps = overwatch_sweep_processes(processes)
        text = [f"role={s.role} task={s.task_id} pid={s.pid} provider={s.provider}" for s in sessions]
        text += [f"driver={d.script} pid={d.pid}" for d in drivers]
        text += [f"sweep={w.script} pid={w.pid}" for w in sweeps]
        data = {
            "sessions": [s.to_dict() for s in sessions],
            "wake_drivers": [d.to_dict() for d in drivers],
            "overwatch_sweeps": [w.to_dict() for w in sweeps],
        }
        _emit(args.format, data, text)
        return EXIT_YES if sessions or drivers or sweeps else EXIT_NO

    if args.command == "status":
        state = _container_state_from_args(args, now)
        report = build_status_report(
            state,
            processes=parse_process_table(_read_optional(args.ps_file)),
            kanban_root=_kanban_root_arg(args.kanban_root),
            crontab_text=_read_optional(args.crontab_file),
            now=now,
        )
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(render_status_text(report))
        return report.exit_code

    parser.print_help()
    return EXIT_USAGE


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns the exit code (see the module docstring)."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run(args, parser)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    except ContainerQueryError as exc:
        print(f"lifecycle: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"lifecycle: cannot read input: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR  # unreachable: parser.error exits


if __name__ == "__main__":
    sys.exit(main())

"""
runaway.py — the runaway detector: the one path by which OVERWATCH stops a project.

A project is "running away" when the chain repeats itself faster than any
healthy release cycle would: patch release after patch release, an RC opened
and cancelled in a loop, one task blocking the same way again and again, or
the operator inbox filling up. Each of those is a counter with a threshold in
``kanban.cfg [overwatch]``. When a counter passes its threshold the detector
stops that project alone: it writes a project-scoped chain HALT that names
the counter, its threshold and its last events, and parks one question in
the project's operator inbox. Every other project keeps running. Nothing
here asks a model anything; the counters, thresholds and the one action are
code.

Counters
--------
==========================  ==================================  ========  =======
counter                     what one event is                   window    default
==========================  ==================================  ========  =======
``releases_per_hour``       a release that shipped              1 hour    5
``rc_cycles_per_day``       an RC that was opened and cancelled 1 day     2
``reason_repeats``          one block of one task with one      7 days    3
                            reason code that did not follow a
                            triage requeue (counted per task
                            and reason code)
``inbox_entries_per_day``   an operator inbox entry written     1 day     20
==========================  ==================================  ========  =======

A counter trips when the events inside its window EXCEED its threshold
(6 releases in an hour trip the default 5). A threshold of ``0`` turns that
counter off. After a trip, only events newer than the trip count again for
that counter (and, for ``reason_repeats``, that task and reason code), so
clearing the HALT never re-trips on the evidence that caused it.

A block that follows a triage requeue is a retry on a requeue ladder (a
classifier row's ``max_attempts`` or the pipeline ladder), not a repeat, and
is not counted: its Block Record's ``requeues`` is higher than at the same
task's previous block. ``requeues`` stops rising once ``[overwatch]
requeue_budget`` is spent, so from then on every block counts. At the
default 3, the block that ends a ladder reaches its row's decision; a task
that keeps blocking with one reason code after that trips on its third
further block (four counted: its first block and those three). A task
blocked again with no requeue between its blocks trips on its fourth block.

Where the events come from
--------------------------
:func:`observe` reads the project's durable records and adds every event it
has not seen before (each event has a stable key, so reading the same record
twice counts once):

* releases -- ``release-state/<version>.json`` records whose ``outcome`` is
  ``shipped``, dated by ``closed_at``;
* RC cycles -- ``release-state/<version>.json`` and
  ``release-state/history/*.json`` records whose ``outcome`` is
  ``cancelled``, dated by ``closed_at``;
* reason repeats -- each task's ``## Block Record``
  (:mod:`pgai_agent_kanban.overwatch.block_record`): one event per distinct
  ``last_blocked_at``, keyed by task and reason code, carrying the record's
  ``requeues`` as it reads then (above). The decision wake runs
  :func:`observe` each time it fires, and nothing reports a block as it
  happens, so two blocks of one task written between two observations
  count once;
* inbox entries -- every entry in ``operator-inbox.md``, open or done,
  dated by its ``Date``, except the entries this detector wrote itself.

:func:`record_event` adds one event directly, with the same keys, so a
caller that already knows about an event never waits for the next
observation.

Persisted counters
------------------
The events and the trips live in one JSON file per project,
``projects/<name>/overwatch/runaway-counters.json``::

    {
      "events": [{"counter": "...", "key": "...", "at": "ISO", "subject": "...",
                  "detail": "...", "requeues": N}, ...],
      "trips":  [{"counter": "...", "subject": "...", "at": "ISO", "count": N,
                  "threshold": N, "halt": "written|present", "inbox": "INBOX-NNNN|"}, ...]
    }

Events older than the longest window are dropped on every write; the newest
:data:`TRIP_HISTORY` trips are kept. The file is replaced atomically under an
advisory lock, ``<kanban root>/locks/overwatch-runaway-<project>.lock``,
taken with holder/pid/op/task provenance; a writer waits up to
:data:`LOCK_WAIT_SECONDS` for it. A file that cannot be parsed reads as
empty and is rewritten on the next write.

The HALT
--------
:func:`run` observes, evaluates and, when a counter tripped and the project
has no HALT yet, creates ``projects/<name>/HALT`` (exclusively: an existing
HALT is never overwritten) with this body::

    # HALT created by the OVERWATCH runaway detector at <time>: <counter> passed its threshold
    # halt-writer: overwatch-runaway
    # halt-reason: runaway.<counter>: <count> <events> within <window> exceeds the threshold of <n>
    # error:   runaway.<counter>
    # message: <what stopped and why>
    # remedy:  <the status command that prints the evidence>
    # runaway-counter: <counter>
    # runaway-threshold: <n>
    # runaway-window: <window>
    # runaway-count: <count>
    # runaway-subject: <project, or TASK-ID:reason_code>
    # runaway-event: <time> <key> <detail>          (the last N events, oldest first)
    # Resume: <kanban root>/scripts/unhalt.sh --project <name>

The ``halt-writer`` / ``halt-reason`` lines are the provenance block
(:mod:`pgai_agent_kanban.overwatch.halt_provenance`), so the HALT reads as
the chain's. When several counters trip at once the ``runaway-*`` block
repeats per counter. It then writes one inbox entry naming the counter(s)
and how to resume. A project already halted (by anyone) is left alone:
nothing is written and no trip is recorded, so the events still count once
the project resumes.

Configuration
-------------
Five ``[overwatch]`` keys, each read from the project's ``project.cfg``
first and ``kanban.cfg`` second through the shared tier resolver
(:func:`pgai_agent_kanban.lib.project_cfg.resolve_tiered_cfg_value`), the
precedence every ``[overwatch]`` key uses. Absent or blank resolves to the
default; any other unrecognised value refuses (dual-channel, nothing
written):

    runaway_releases_per_hour       whole number >= 0, default 5
    runaway_rc_cycles_per_day       whole number >= 0, default 2
    runaway_reason_repeats          whole number >= 0, default 3
    runaway_inbox_entries_per_day   whole number >= 0, default 20
    halt_after_auto_resume          true|false, default false -- read by the
                                    HALT-clearing stage, resolved here
                                    (:func:`resolve_halt_after_auto_resume`)

Public API
----------
    COUNTERS, CounterSpec, Event, Trip, RunawayResult, RunawayError
    resolve_thresholds(kanban_root, project) -> dict[str, int]
    resolve_halt_after_auto_resume(kanban_root, project) -> bool
    counters_path(ctx, project) -> Path
    record_event(ctx, project, counter, *, key, at, subject=None, detail="", requeues=None) -> bool
    observe(ctx, project, *, now=None) -> int
    evaluate(ctx, project, *, now=None, thresholds=None) -> tuple[Trip, ...]
    run(ctx, project, *, now=None) -> RunawayResult

Command line
------------
``--kanban-root`` defaults to the root the environment names::

    python3 -m pgai_agent_kanban.overwatch.runaway check  --project NAME [--now ISO] [--json]
    python3 -m pgai_agent_kanban.overwatch.runaway status --project NAME [--now ISO] [--json]
    python3 -m pgai_agent_kanban.overwatch.runaway config --project NAME [--json]

``check`` runs :func:`run` (observe, evaluate, stop the project on a trip);
``status`` prints each counter's current count and threshold from the
persisted events without observing or writing; ``config`` prints the five
resolved keys. A trip prints the dual-channel block on stderr and still
exits 0 (the stop is the HALT, not the exit code). A refusal prints the
dual-channel block and exits 1; 2 is a usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import pathlib
import re
import shlex
import sys
import tempfile
import time
from typing import NoReturn
from collections.abc import Iterable

# Started by file path (python3 .../overwatch/runaway.py) the module has no
# parent package and only its own directory on sys.path: put this tree's own
# package root first so the package imports below resolve from the same
# tree, never another. Imported as a package member, sys.path is untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import ini, project_cfg  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import (  # noqa: E402
    emit_error_contract,
    format_error_contract,
    module_file_command,
)
from pgai_agent_kanban.lib.text import one_line  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import ContractError, OpsError  # noqa: E402
from pgai_agent_kanban.ops.locks import LockContended, acquire_lock  # noqa: E402
from pgai_agent_kanban.overwatch import inbox  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import load_block_record  # noqa: E402
from pgai_agent_kanban.overwatch.halt_provenance import (  # noqa: E402
    WRITER_OVERWATCH_RUNAWAY,
    provenance_lines,
    unhalt_command,
)
from pgai_agent_kanban.task_status import TaskStatusError  # noqa: E402

__all__ = [
    "COUNTERS",
    "COUNTER_INBOX_ENTRIES",
    "COUNTER_RC_CYCLES",
    "COUNTER_REASON_REPEATS",
    "COUNTER_RELEASES",
    "COUNTERS_FILENAME",
    "DEFAULT_HALT_AFTER_AUTO_RESUME",
    "EVIDENCE_EVENTS",
    "HALT_AFTER_AUTO_RESUME_KEY",
    "LOCK_WAIT_SECONDS",
    "CounterSpec",
    "Event",
    "RunawayError",
    "RunawayResult",
    "Trip",
    "counters_path",
    "evaluate",
    "observe",
    "record_event",
    "resolve_halt_after_auto_resume",
    "resolve_thresholds",
    "run",
]

# ---------------------------------------------------------------------------
# Counters and configuration
# ---------------------------------------------------------------------------

CONFIG_SECTION = "overwatch"

COUNTER_RELEASES = "releases_per_hour"
COUNTER_RC_CYCLES = "rc_cycles_per_day"
COUNTER_REASON_REPEATS = "reason_repeats"
COUNTER_INBOX_ENTRIES = "inbox_entries_per_day"

_HOUR = 3600
_DAY = 24 * _HOUR


@dataclasses.dataclass(frozen=True)
class CounterSpec:
    """One runaway counter.

    Attributes:
        name:           The counter's name, as the HALT and the trips name it.
        cfg_key:        Its ``[overwatch]`` threshold key.
        default:        The threshold an absent or blank key resolves to.
        window_seconds: How far back an event still counts.
        window_label:   The window in words for the HALT (``1h``, ``1d``, ``7d``).
        noun:           What one event is, plural, for messages.
    """

    name: str
    cfg_key: str
    default: int
    window_seconds: int
    window_label: str
    noun: str


#: Every counter, in the order a HALT that names several lists them.
COUNTERS: tuple[CounterSpec, ...] = (
    CounterSpec(COUNTER_RELEASES, "runaway_releases_per_hour", 5, _HOUR, "1h", "releases shipped"),
    CounterSpec(COUNTER_RC_CYCLES, "runaway_rc_cycles_per_day", 2, _DAY, "1d", "RC open/cancel cycles"),
    CounterSpec(
        COUNTER_REASON_REPEATS,
        "runaway_reason_repeats",
        3,
        7 * _DAY,
        "7d",
        "blocks of one task with one reason code",
    ),
    CounterSpec(COUNTER_INBOX_ENTRIES, "runaway_inbox_entries_per_day", 20, _DAY, "1d", "operator inbox entries"),
)
_BY_NAME = {spec.name: spec for spec in COUNTERS}

HALT_AFTER_AUTO_RESUME_KEY = "halt_after_auto_resume"
DEFAULT_HALT_AFTER_AUTO_RESUME = False

#: The last events a HALT lists per tripped counter.
EVIDENCE_EVENTS = 10

#: Trips kept in the counters file.
TRIP_HISTORY = 100

#: How long a writer waits for another writer's lock before refusing.
LOCK_WAIT_SECONDS = 10.0

COUNTERS_FILENAME = "runaway-counters.json"

#: The subject of a counter that counts the whole project.
PROJECT_SUBJECT = "project"

_RETENTION_SECONDS = max(spec.window_seconds for spec in COUNTERS)
_LOCK_HOLDER = "overwatch-runaway"
_LOCK_POLL_SECONDS = 0.05
_ATOMIC_PREFIX = ".runaway_counters_tmp_"
_HALT_NAME = "HALT"
_MODULE_FILE = pathlib.Path(__file__).resolve()
_WHOLE_RE = re.compile(r"^\d+$")


class RunawayError(ContractError):
    """A runaway operation that did not happen, carrying the dual-channel fields.

    ``str(exc)`` is the three-line house-shape block.

    Attributes:
        error:   Stable machine key, ``runaway.<reason>``.
        message: What did not happen and why.
        remedy:  A command that shows or fixes the cause, runnable as printed.
    """


def _refuse(error: str, message: str, remedy: str) -> NoReturn:
    """Raise :class:`RunawayError` -- the one refusal site every check here shares.

    Nothing has been written when this runs.
    """
    # CONVERT: a refused detector call (an unreadable threshold, a malformed
    # event, a busy lock) only means this project is not checked on this call;
    # no task, wake or release stops, and the next sweep runs it again once
    # the value or the caller is fixed.
    # CENSUS-ANCHOR: runaway-refusal-01
    raise RunawayError(error, message, remedy)


def _sed_remedy(key: str, value: str, source: str) -> str:
    """A runnable command that sets ``key = value`` in *source*."""
    return f"sed -i 's/^[[:space:]]*{key}[[:space:]]*=.*/{key} = {value}/' {shlex.quote(source)}"


def _tiered(kanban_root: pathlib.Path | str, project: str, key: str) -> project_cfg.TieredValue:
    """``[overwatch] key`` from the project's project.cfg, then kanban.cfg."""
    project_root = pathlib.Path(kanban_root) / "projects" / project
    return project_cfg.resolve_tiered_cfg_value(kanban_root, project_root, CONFIG_SECTION, key, legacy=False)


def resolve_thresholds(kanban_root: pathlib.Path | str, project: str) -> dict[str, int]:
    """Every counter's threshold for *project*, keyed by counter name.

    Each ``runaway_*`` key is read from ``projects/<project>/project.cfg``
    first, then ``kanban.cfg``; absent or blank resolves to the counter's
    default. ``0`` turns the counter off.

    Raises:
        RunawayError: a present value that is not a whole number, naming
            the value, the key, the file and the accepted form.
    """
    thresholds: dict[str, int] = {}
    for spec in COUNTERS:
        found = _tiered(kanban_root, project, spec.cfg_key)
        raw = found.value.strip()
        if not raw:
            thresholds[spec.name] = spec.default
            continue
        if not _WHOLE_RE.match(raw):
            _refuse(
                "runaway.bad_threshold",
                f"[{CONFIG_SECTION}] {spec.cfg_key} = {raw!r} in {found.source} is not a whole number "
                f"(accepted: 0 to turn the counter off, or a positive whole number, e.g. {spec.default}); "
                f"the runaway detector checked nothing for project {project!r}.",
                _sed_remedy(spec.cfg_key, str(spec.default), found.source),
            )
        thresholds[spec.name] = int(raw)
    return thresholds


def resolve_halt_after_auto_resume(kanban_root: pathlib.Path | str, project: str) -> bool:
    """``[overwatch] halt_after_auto_resume`` for *project*.

    Whether a HALT the HALT-AFTER promotion wrote, once its drain completed,
    may be cleared to an inbox entry instead of waiting for the operator.
    Read from ``projects/<project>/project.cfg`` first, then ``kanban.cfg``;
    absent or blank resolves to False.

    Raises:
        RunawayError: a present value other than ``true`` or ``false``.
    """
    found = _tiered(kanban_root, project, HALT_AFTER_AUTO_RESUME_KEY)
    raw = found.value.strip()
    if not raw:
        return DEFAULT_HALT_AFTER_AUTO_RESUME
    if raw == "true":
        return True
    if raw == "false":
        return False
    _refuse(
        "runaway.bad_halt_after_auto_resume",
        f"[{CONFIG_SECTION}] {HALT_AFTER_AUTO_RESUME_KEY} = {raw!r} in {found.source} is not a boolean "
        f"(accepted: true|false); no HALT-AFTER HALT is cleared for project {project!r} until it is.",
        _sed_remedy(HALT_AFTER_AUTO_RESUME_KEY, "false", found.source),
    )


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Event:
    """One counted occurrence.

    Attributes:
        counter: The counter it counts toward.
        key:     Stable identity; the same event read twice has the same key.
        at:      When it happened, ISO-8601 UTC.
        subject: What it is counted against: ``project``, or
                 ``TASK-ID:reason_code`` for ``reason_repeats``.
        detail:  One line saying what happened.
        requeues: For ``reason_repeats``, the Block Record's ``requeues``
                 when the block was seen (how many times triage had
                 returned the task to its owner); None when unknown and for
                 every other counter.
    """

    counter: str
    key: str
    at: str
    subject: str = PROJECT_SUBJECT
    detail: str = ""
    requeues: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the event as a JSON-ready dict (``requeues`` only when known)."""
        data: dict[str, object] = dataclasses.asdict(self)
        if self.requeues is None:
            del data["requeues"]
        return data

    @property
    def task_id(self) -> str:
        """The task a ``reason_repeats`` event belongs to (its subject before the reason code)."""
        return self.subject.split(":", 1)[0]

    @classmethod
    def from_dict(cls, raw: object) -> Event | None:
        """The event, or None for a malformed entry or an unknown counter."""
        if not isinstance(raw, dict):
            return None
        requeues = raw.get("requeues")
        try:
            event = cls(
                counter=str(raw["counter"]),
                key=str(raw["key"]),
                at=str(raw["at"]),
                subject=str(raw.get("subject") or PROJECT_SUBJECT),
                detail=str(raw.get("detail") or ""),
                requeues=requeues if isinstance(requeues, int) and not isinstance(requeues, bool) else None,
            )
        except KeyError:
            return None
        if event.counter not in _BY_NAME or try_parse_iso(event.at) is None:
            return None
        return event


@dataclasses.dataclass(frozen=True)
class Trip:
    """A counter whose events in its window exceed its threshold.

    Attributes:
        counter:   The counter's name.
        subject:   What it counted against (see :class:`Event`).
        threshold: The threshold it exceeded.
        count:     Events counted in the window.
        window:    The window in words.
        events:    The last :data:`EVIDENCE_EVENTS` counted events, oldest first.
    """

    counter: str
    subject: str
    threshold: int
    count: int
    window: str
    events: tuple[Event, ...]

    @property
    def error_key(self) -> str:
        """The stable machine key the HALT and the CLI print, ``runaway.<counter>``."""
        return f"runaway.{self.counter}"

    @property
    def reason(self) -> str:
        """One line: the counter, its count, its window and its threshold."""
        noun = _BY_NAME[self.counter].noun
        where = "" if self.subject == PROJECT_SUBJECT else f" for {self.subject}"
        return (
            f"{self.error_key}: {self.count} {noun}{where} within {self.window} "
            f"exceeds the threshold of {self.threshold}"
        )

    def to_dict(self) -> dict[str, object]:
        """Return the trip as a JSON-ready dict."""
        data = dataclasses.asdict(self)
        data["events"] = [e.to_dict() for e in self.events]
        data["error_key"] = self.error_key
        data["reason"] = self.reason
        return data


#: :func:`run` outcomes.
OUTCOME_CLEAR = "clear"
OUTCOME_TRIPPED = "tripped"
OUTCOME_ALREADY_HALTED = "already-halted"


@dataclasses.dataclass(frozen=True)
class RunawayResult:
    """What :func:`run` did.

    Attributes:
        project:     The project checked.
        outcome:     ``clear`` (nothing tripped), ``tripped`` (HALT written)
                     or ``already-halted`` (a counter tripped but the project
                     already had a HALT, so nothing was written).
        trips:       The counters that tripped.
        observed:    New events :func:`observe` added.
        halt_path:   The HALT written, or None.
        inbox_entry: The inbox entry ID written, or ``""``.
        error:       The dual-channel fields of the stop (``error``,
                     ``message``, ``remedy``) when a counter tripped, else {}.
        warnings:    Lines about what could not be done (an inbox write that
                     failed); the HALT stands regardless.
    """

    project: str
    outcome: str
    trips: tuple[Trip, ...] = ()
    observed: int = 0
    halt_path: str | None = None
    inbox_entry: str = ""
    error: dict = dataclasses.field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the result as a JSON-ready dict."""
        data = dataclasses.asdict(self)
        data["trips"] = [t.to_dict() for t in self.trips]
        data["warnings"] = list(self.warnings)
        return data


@dataclasses.dataclass
class _State:
    """The persisted counters: every event in retention and the recent trips."""

    events: list[Event]
    trips: list[dict]

    def keys(self) -> set[str]:
        return {e.key for e in self.events}


# ---------------------------------------------------------------------------
# Paths, time, validation
# ---------------------------------------------------------------------------


def counters_path(ctx: OpsContext, project: str) -> pathlib.Path:
    """Return ``<project root>/overwatch/runaway-counters.json`` (it need not exist)."""
    return ctx.project_root(project) / "overwatch" / COUNTERS_FILENAME


def _lock_path(ctx: OpsContext, project: str) -> pathlib.Path:
    """``<kanban root>/locks/overwatch-runaway-<project>.lock``; the ``-<project>.lock``
    ending lets the project's lock listings include it."""
    return ctx.kanban_root / "locks" / f"{_LOCK_HOLDER}-{project}.lock"


def _status_command(ctx: OpsContext, project: str) -> str:
    return module_file_command(_MODULE_FILE, "status", "--project", project, "--kanban-root", str(ctx.kanban_root))


def _checked_project(ctx: OpsContext, project: str) -> pathlib.Path:
    """The project's directory, refusing a malformed name or a missing directory."""
    projects_dir = ctx.kanban_root / "projects"
    if not isinstance(project, str) or not ini.is_project_name(project):
        _refuse(
            "runaway.bad_project",
            f"project name {project!r} is not a project name (lower-case letters, digits and '-', "
            "starting with a letter or digit); nothing was read or written.",
            f"ls {shlex.quote(str(projects_dir))}",
        )
    project_dir = ctx.project_root(project)
    if not project_dir.is_dir():
        _refuse(
            "runaway.unknown_project",
            f"project {project!r} has no directory at {project_dir}; nothing was read or written.",
            f"ls {shlex.quote(str(projects_dir))}",
        )
    return project_dir


def _now(now: datetime.datetime | None) -> datetime.datetime:
    value = now if now is not None else datetime.datetime.now(datetime.UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value


def _when(text: str) -> datetime.datetime | None:
    """*text* as an aware datetime, or None when it is not ISO-8601."""
    return try_parse_iso(text) if isinstance(text, str) and text.strip() else None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load(path: pathlib.Path) -> _State:
    """The persisted counters; a missing or unparseable file reads as empty."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _State([], [])
    if not isinstance(raw, dict):
        return _State([], [])
    events = [e for e in (Event.from_dict(item) for item in raw.get("events") or []) if e is not None]
    trips = [t for t in raw.get("trips") or [] if isinstance(t, dict)]
    return _State(events, trips)


def _save(path: pathlib.Path, state: _State, now: datetime.datetime) -> None:
    """Write *state* back atomically, dropping events past retention."""
    cutoff = now - datetime.timedelta(seconds=_RETENTION_SECONDS)
    events = [e for e in state.events if (_when(e.at) or now) >= cutoff]
    events.sort(key=lambda e: (_when(e.at) or now, e.key))
    data = {
        "events": [e.to_dict() for e in events],
        "trips": state.trips[-TRIP_HISTORY:],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", prefix=_ATOMIC_PREFIX)


class _CountersLock:
    """The writers' advisory lock, waited for up to :data:`LOCK_WAIT_SECONDS`."""

    def __init__(self, ctx: OpsContext, project: str, op: str) -> None:
        self._ctx = ctx
        self._project = project
        self._op = op
        self._held = None

    def __enter__(self) -> _CountersLock:
        lock_file = _lock_path(self._ctx, self._project)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                self._held = acquire_lock(lock_file, holder=_LOCK_HOLDER, pid=os.getpid(), op=self._op, task="none")
                return self
            except LockContended as busy:
                if time.monotonic() >= deadline:
                    _refuse(
                        "runaway.lock_busy",
                        f"the runaway counters for project {self._project!r} are locked by "
                        f"{busy.describe()} and stayed locked for {LOCK_WAIT_SECONDS:g}s; "
                        "nothing was checked or written.",
                        f"cat {shlex.quote(str(lock_file))}",
                    )
                time.sleep(_LOCK_POLL_SECONDS)
            except OpsError as exc:
                _refuse(
                    "runaway.lock_failed",
                    f"cannot take the runaway counters lock {lock_file}: {exc}",
                    f"ls -ld {shlex.quote(str(lock_file.parent))}",
                )

    def __exit__(self, *exc_info: object) -> None:
        if self._held is not None:
            self._held.release()
            self._held = None


def _add(state: _State, events: Iterable[Event], seen: set[str]) -> int:
    """Append each event whose key is not in *seen*; return how many were added."""
    added = 0
    for event in events:
        if event.key in seen:
            continue
        seen.add(event.key)
        state.events.append(event)
        added += 1
    return added


# ---------------------------------------------------------------------------
# Durable sources
# ---------------------------------------------------------------------------


def _rc_records(project_dir: pathlib.Path) -> Iterable[tuple[pathlib.Path, dict]]:
    """Every per-RC JSON record, the active slots and the archived history."""
    state_dir = project_dir / "release-state"
    for directory in (state_dir, state_dir / "history"):
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError:
            continue
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                yield path, data


def _release_events(project_dir: pathlib.Path) -> Iterable[Event]:
    for path, data in _rc_records(project_dir):
        outcome = str(data.get("outcome") or "").strip().lower()
        closed = _when(str(data.get("closed_at") or ""))
        if closed is None:
            continue
        rc = one_line(data.get("rc") or path.stem)
        if outcome == "shipped":
            yield Event(COUNTER_RELEASES, f"release:{rc}", format_iso(closed), PROJECT_SUBJECT, f"shipped {rc}")
        elif outcome == "cancelled":
            stamp = format_iso(closed)
            opened = one_line(data.get("opened_at") or "unknown")
            yield Event(
                COUNTER_RC_CYCLES,
                f"rc-cancel:{rc}:{stamp}",
                stamp,
                PROJECT_SUBJECT,
                f"cancelled {rc} (opened {opened})",
            )


def _block_events(project_dir: pathlib.Path) -> Iterable[Event]:
    tasks_dir = project_dir / "tasks"
    try:
        status_files = sorted(tasks_dir.glob("*/status.md"))
    except OSError:
        return
    for status_file in status_files:
        try:
            record = load_block_record(status_file)
        except (TaskStatusError, OSError, ValueError):
            continue
        if record is None:
            continue
        blocked = _when(record.last_blocked_at)
        if blocked is None:
            continue
        task_id = status_file.parent.name
        yield _block_event(task_id, record.reason_code, format_iso(blocked), record.requeues)


def _block_event(task_id: str, reason_code: str, at: str, requeues: int | None = None) -> Event:
    """One ``reason_repeats`` event: *task_id* blocked with *reason_code* at *at*,
    its Block Record having been requeued *requeues* times (None: unknown)."""
    return Event(
        COUNTER_REASON_REPEATS,
        f"block:{task_id}:{at}",
        at,
        f"{task_id}:{reason_code}",
        f"{task_id} blocked: {reason_code}",
        requeues,
    )


def _inbox_events(ctx: OpsContext, project: str, own_entries: set[str]) -> Iterable[Event]:
    try:
        box = inbox.read_inbox(ctx, project)
    except inbox.InboxError:
        return
    for entry in box.open + box.done:
        if entry.entry_id in own_entries:
            continue
        written = _when(entry.date)
        if written is None:
            continue
        question = one_line(entry.question)[:120]
        yield Event(
            COUNTER_INBOX_ENTRIES,
            f"inbox:{entry.entry_id}",
            format_iso(written),
            PROJECT_SUBJECT,
            f"{entry.entry_id} from {one_line(entry.source)}: {question}",
        )


def _own_inbox_entries(state: _State) -> set[str]:
    return {str(t.get("inbox")) for t in state.trips if t.get("inbox")}


def _observe_into(
    ctx: OpsContext, project: str, project_dir: pathlib.Path, state: _State, now: datetime.datetime
) -> int:
    cutoff = now - datetime.timedelta(seconds=_RETENTION_SECONDS)
    seen = state.keys()
    fresh = (
        e
        for source in (
            _release_events(project_dir),
            _block_events(project_dir),
            _inbox_events(ctx, project, _own_inbox_entries(state)),
        )
        for e in source
        if (_when(e.at) or now) >= cutoff
    )
    return _add(state, fresh, seen)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _last_trips(state: _State) -> dict[tuple[str, str], datetime.datetime]:
    """The latest trip time per (counter, subject)."""
    latest: dict[tuple[str, str], datetime.datetime] = {}
    for trip in state.trips:
        at = _when(str(trip.get("at") or ""))
        if at is None:
            continue
        key = (str(trip.get("counter")), str(trip.get("subject") or PROJECT_SUBJECT))
        if key not in latest or at > latest[key]:
            latest[key] = at
    return latest


def _requeue_retries(events: Iterable[Event]) -> set[str]:
    """The keys of ``reason_repeats`` events that follow a triage requeue.

    A block whose Block Record ``requeues`` is higher than it was at the
    same task's previous block (whatever its reason code) was blocked again
    after triage returned the task to its owner: it is a retry on a requeue
    ladder, not a repeat. ``requeues`` stops rising once
    ``[overwatch] requeue_budget`` is spent, so every block after that
    counts. A block with no earlier block of its task to compare with, or
    either side unknown, counts.
    """
    by_task: dict[str, list[tuple[datetime.datetime, Event]]] = {}
    for event in events:
        at = _when(event.at)
        if event.counter == COUNTER_REASON_REPEATS and at is not None:
            by_task.setdefault(event.task_id, []).append((at, event))
    retries: set[str] = set()
    for blocks in by_task.values():
        blocks.sort(key=lambda pair: (pair[0], pair[1].key))
        for (_, before), (_, block) in zip(blocks, blocks[1:]):
            if before.requeues is not None and block.requeues is not None and block.requeues > before.requeues:
                retries.add(block.key)
    return retries


def _counted(
    state: _State,
    spec: CounterSpec,
    now: datetime.datetime,
    latest: dict[tuple[str, str], datetime.datetime],
    retries: set[str],
) -> dict[str, list[tuple[datetime.datetime, Event]]]:
    """*spec*'s counted events per subject, oldest first: inside its window,
    newer than the subject's last trip, and (for ``reason_repeats``) not a
    retry after a triage requeue (*retries*, from :func:`_requeue_retries`)."""
    window_start = now - datetime.timedelta(seconds=spec.window_seconds)
    groups: dict[str, list[tuple[datetime.datetime, Event]]] = {}
    for event in state.events:
        if event.counter != spec.name or event.key in retries:
            continue
        at = _when(event.at)
        if at is None or at <= window_start:
            continue
        reset = latest.get((spec.name, event.subject))
        if reset is not None and at <= reset:
            continue
        groups.setdefault(event.subject, []).append((at, event))
    for counted in groups.values():
        counted.sort(key=lambda pair: (pair[0], pair[1].key))
    return groups


def _evaluate_state(state: _State, thresholds: dict[str, int], now: datetime.datetime) -> tuple[Trip, ...]:
    latest = _last_trips(state)
    retries = _requeue_retries(state.events)
    trips: list[Trip] = []
    for spec in COUNTERS:
        threshold = thresholds.get(spec.name, spec.default)
        if threshold <= 0:
            continue
        groups = _counted(state, spec, now, latest, retries)
        for subject in sorted(groups):
            counted = groups[subject]
            if len(counted) > threshold:
                trips.append(
                    Trip(
                        counter=spec.name,
                        subject=subject,
                        threshold=threshold,
                        count=len(counted),
                        window=spec.window_label,
                        events=tuple(e for _, e in counted[-EVIDENCE_EVENTS:]),
                    )
                )
    return tuple(trips)


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


def record_event(
    ctx: OpsContext,
    project: str,
    counter: str,
    *,
    key: str,
    at: datetime.datetime | str,
    subject: str | None = None,
    detail: str = "",
    requeues: int | None = None,
) -> bool:
    """Add one event to *project*'s persisted counters; return False when its key was already there.

    For ``reason_repeats`` pass the task ID as *subject*, the reason code
    as *detail* and the Block Record's ``requeues`` as *requeues*: the event
    is stored the way :func:`observe` stores a Block Record, so one block
    reported both ways counts once when *at* is the record's
    ``last_blocked_at``. Without *requeues* the block always counts (it
    cannot be told apart from a repeat). *requeues* is ignored for every
    other counter.

    Raises:
        RunawayError: an unknown counter, a blank key, a time that is not
            ISO-8601, a malformed or missing project, or a busy lock.
    """
    _checked_project(ctx, project)
    if counter not in _BY_NAME:
        _refuse(
            "runaway.unknown_counter",
            f"counter {counter!r} is not a runaway counter (known: {', '.join(_BY_NAME)}); nothing was recorded.",
            _status_command(ctx, project),
        )
    when = at if isinstance(at, datetime.datetime) else _when(str(at))
    if when is None:
        _refuse(
            "runaway.bad_time",
            f"event time {at!r} is not ISO-8601 (e.g. 2026-01-01T10:00:00+00:00); nothing was recorded.",
            "date -u +%Y-%m-%dT%H:%M:%S+00:00",
        )
    stamp = format_iso(_now(when))
    if counter == COUNTER_REASON_REPEATS:
        task_id = one_line(subject or "")
        reason = one_line(detail)
        if not task_id or not reason:
            _refuse(
                "runaway.bad_event",
                "a reason_repeats event needs the task ID as subject and the reason code as detail; "
                "nothing was recorded.",
                _status_command(ctx, project),
            )
        event = _block_event(task_id, reason, stamp, requeues)
    else:
        if not one_line(key):
            _refuse(
                "runaway.bad_event",
                f"a {counter} event needs a non-blank key; nothing was recorded.",
                _status_command(ctx, project),
            )
        event = Event(counter, one_line(key), stamp, one_line(subject or PROJECT_SUBJECT), one_line(detail))
    path = counters_path(ctx, project)
    with _CountersLock(ctx, project, "runaway record"):
        state = _load(path)
        added = _add(state, [event], state.keys())
        if added:
            # Retention is measured from the event itself, so a back-dated
            # event is kept for the evaluation that asks about its own time.
            _save(path, state, _now(when))
    return bool(added)


def observe(ctx: OpsContext, project: str, *, now: datetime.datetime | None = None) -> int:
    """Read *project*'s durable records into its persisted counters; return the new events.

    Raises:
        RunawayError: a malformed or missing project, or a busy lock.
    """
    project_dir = _checked_project(ctx, project)
    moment = _now(now)
    path = counters_path(ctx, project)
    with _CountersLock(ctx, project, "runaway observe"):
        state = _load(path)
        added = _observe_into(ctx, project, project_dir, state, moment)
        if added:
            _save(path, state, moment)
    return added


def evaluate(
    ctx: OpsContext,
    project: str,
    *,
    now: datetime.datetime | None = None,
    thresholds: dict[str, int] | None = None,
) -> tuple[Trip, ...]:
    """The counters that trip now, from the persisted events alone. Reads only.

    Raises:
        RunawayError: a malformed or missing project, or an unreadable threshold.
    """
    _checked_project(ctx, project)
    limits = thresholds if thresholds is not None else resolve_thresholds(ctx.kanban_root, project)
    return _evaluate_state(_load(counters_path(ctx, project)), limits, _now(now))


def _stop_contract(ctx: OpsContext, project: str, trips: tuple[Trip, ...]) -> dict[str, str]:
    first = trips[0]
    more = f" (and {len(trips) - 1} more counter(s))" if len(trips) > 1 else ""
    return {
        "error": first.error_key,
        "message": (
            f"OVERWATCH stopped project {project!r} because it is running away: {first.reason}{more}. "
            f"Only this project is halted; resume it with {unhalt_command(ctx.kanban_root, project)} once the cause is fixed."
        ),
        "remedy": _status_command(ctx, project),
    }


def _halt_body(
    ctx: OpsContext, project: str, trips: tuple[Trip, ...], contract: dict[str, str], now: datetime.datetime
) -> str:
    first = trips[0]
    reason = first.reason if len(trips) == 1 else f"{first.reason} (and {len(trips) - 1} more counter(s))"
    lines = [
        f"# HALT created by the OVERWATCH runaway detector at {format_iso(now)}: "
        f"{', '.join(t.counter for t in trips)} passed its threshold",
        *provenance_lines(WRITER_OVERWATCH_RUNAWAY, reason),
        *(f"# {line}" for line in format_error_contract(**contract).splitlines()),
    ]
    for trip in trips:
        lines += [
            f"# runaway-counter: {trip.counter}",
            f"# runaway-threshold: {trip.threshold}",
            f"# runaway-window: {trip.window}",
            f"# runaway-count: {trip.count}",
            f"# runaway-subject: {trip.subject}",
            *(f"# runaway-event: {e.at} {e.key} {one_line(e.detail)}".rstrip() for e in trip.events),
        ]
    lines.append(f"# Resume: {unhalt_command(ctx.kanban_root, project)}")
    return "\n".join(lines) + "\n"


def _create_halt(halt_path: pathlib.Path, body: str) -> bool:
    """Create *halt_path* holding *body*, never replacing an existing file.

    The body is written to a temporary file beside it and hard-linked into
    place, so a reader sees the whole body or no HALT. Returns False when a
    HALT was already there.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=".runaway_halt_tmp_", dir=str(halt_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        try:
            os.link(tmp_name, halt_path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _inbox_text(ctx: OpsContext, project: str, trips: tuple[Trip, ...], halt_path: pathlib.Path) -> dict[str, str]:
    first = trips[0]
    if first.counter == COUNTER_REASON_REPEATS:
        source = first.subject.split(":", 1)[0]
    elif first.counter in (COUNTER_RELEASES, COUNTER_RC_CYCLES) and first.events:
        source = "rc:" + first.events[-1].key.split(":")[1]
    else:
        source = f"project:{project}"
    counters = "; ".join(t.reason for t in trips)
    return {
        "source": source,
        "question": (
            f"OVERWATCH stopped project {project} as a runaway ({counters}). "
            "What is repeating, and is it fixed so the project can resume?"
        ),
        "meanwhile": (
            f"Wrote a chain HALT at {halt_path} naming the counter, its threshold and its last events; "
            "every other project keeps running."
        ),
        "how_to_answer": (
            f"Inspect the counters with {_status_command(ctx, project)}; once the cause is fixed, resume with "
            f"{unhalt_command(ctx.kanban_root, project)} and close this entry with "
            f"{shlex.quote(str(ctx.kanban_root / 'scripts' / 'inbox.sh'))} --project {shlex.quote(project)} "
            "close --id followed by this entry's INBOX ID."
        ),
    }


def run(ctx: OpsContext, project: str, *, now: datetime.datetime | None = None) -> RunawayResult:
    """Observe, evaluate and, on a trip, stop *project* with a chain HALT and one inbox entry.

    A project that already has a HALT is left alone (``already-halted``):
    nothing is written and the trip is not recorded.

    Raises:
        RunawayError: a malformed or missing project, an unreadable
            threshold, or a busy lock. Nothing is written.
    """
    project_dir = _checked_project(ctx, project)
    thresholds = resolve_thresholds(ctx.kanban_root, project)
    moment = _now(now)
    path = counters_path(ctx, project)
    halt_path = project_dir / _HALT_NAME
    with _CountersLock(ctx, project, "runaway check"):
        state = _load(path)
        observed = _observe_into(ctx, project, project_dir, state, moment)
        trips = _evaluate_state(state, thresholds, moment)
        if not trips:
            if observed:
                _save(path, state, moment)
            return RunawayResult(project, OUTCOME_CLEAR, (), observed)
        contract = _stop_contract(ctx, project, trips)
        if halt_path.exists():
            if observed:
                _save(path, state, moment)
            return RunawayResult(project, OUTCOME_ALREADY_HALTED, trips, observed, error=contract)
        body = _halt_body(ctx, project, trips, contract, moment)
        # BLOCK-WORTHY: a runaway is the chain moving backwards -- minting
        # releases, cycling RCs, failing one task the same way -- so this
        # project stops until the operator resumes it; sibling projects run on.
        # CENSUS-ANCHOR: runaway-halt-01
        if not _create_halt(halt_path, body):
            return RunawayResult(project, OUTCOME_ALREADY_HALTED, trips, observed, error=contract)
        warnings: list[str] = []
        entry_id = ""
        try:
            entry = inbox.write_entry(ctx, project, now=moment, **_inbox_text(ctx, project, trips, halt_path))
            entry_id = entry.entry_id
        except inbox.InboxError as exc:
            warnings.append(f"the HALT stands but no inbox entry was written: {exc.error}: {exc.message}")
        stamp = format_iso(moment)
        for trip in trips:
            state.trips.append(
                {
                    "counter": trip.counter,
                    "subject": trip.subject,
                    "at": stamp,
                    "count": trip.count,
                    "threshold": trip.threshold,
                    "halt": "written",
                    "inbox": entry_id,
                }
            )
        _save(path, state, moment)
    return RunawayResult(project, OUTCOME_TRIPPED, trips, observed, str(halt_path), entry_id, contract, tuple(warnings))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.runaway",
        description=(
            "The runaway detector: count releases, RC cancel cycles, repeated block reasons and inbox "
            "entries per project, and stop a project that passes a threshold with a chain HALT and an "
            "inbox entry."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", required=True, help="the project to check, e.g. pgai-agent-kanban")
        p.add_argument(
            "--kanban-root",
            default="",
            help="the kanban root (default: the root the environment names, PGAI_AGENT_KANBAN_ROOT_PATH)",
        )
        p.add_argument("--json", action="store_true", help="print the result as one JSON object")

    check = sub.add_parser(
        "check", help="observe the project's records, evaluate every counter and stop the project on a trip"
    )
    common(check)
    check.add_argument("--now", default="", help="evaluate as of this ISO-8601 time (default: now)")
    status = sub.add_parser(
        "status", help="print each counter's count and threshold from the persisted events (reads only)"
    )
    common(status)
    status.add_argument("--now", default="", help="evaluate as of this ISO-8601 time (default: now)")
    config = sub.add_parser("config", help="print the resolved [overwatch] runaway keys for the project")
    common(config)
    return parser


def _context(kanban_root: str) -> OpsContext:
    if kanban_root.strip():
        return OpsContext(kanban_root=pathlib.Path(kanban_root))
    try:
        return OpsContext.from_env()
    except RuntimeError as exc:
        _refuse(
            "runaway.no_kanban_root",
            f"no kanban root: --kanban-root was not given and {exc}.",
            'export PGAI_AGENT_KANBAN_ROOT_PATH="$HOME/pgai_agent_kanban"',
        )


def _cli_now(text: str) -> datetime.datetime | None:
    if not text.strip():
        return None
    when = _when(text)
    if when is None:
        _refuse(
            "runaway.bad_time",
            f"--now {text!r} is not ISO-8601 (e.g. 2026-01-01T10:00:00+00:00); nothing was checked.",
            "date -u +%Y-%m-%dT%H:%M:%S+00:00",
        )
    return when


def _status_rows(ctx: OpsContext, project: str, now: datetime.datetime) -> list[dict[str, object]]:
    thresholds = resolve_thresholds(ctx.kanban_root, project)
    state = _load(counters_path(ctx, project))
    latest = _last_trips(state)
    retries = _requeue_retries(state.events)
    rows: list[dict[str, object]] = []
    for spec in COUNTERS:
        counts = {subject: len(events) for subject, events in _counted(state, spec, now, latest, retries).items()}
        subject, count = max(counts.items(), key=lambda kv: (kv[1], kv[0]), default=(PROJECT_SUBJECT, 0))
        rows.append(
            {
                "counter": spec.name,
                "key": spec.cfg_key,
                "window": spec.window_label,
                "threshold": thresholds[spec.name],
                "count": count,
                "subject": subject,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    """Run the command line; return the exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    project = args.project if ini.is_project_name(args.project or "") else None
    try:
        ctx = _context(args.kanban_root)
        if args.command == "check":
            result = run(ctx, args.project, now=_cli_now(args.now))
            if args.json:
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            else:
                print(f"{result.project}: {result.outcome} ({result.observed} new event(s) observed)")
                for trip in result.trips:
                    print(f"  {trip.reason}")
                if result.halt_path:
                    print(f"  HALT: {result.halt_path}")
                if result.inbox_entry:
                    print(f"  inbox: {result.inbox_entry}")
            for line in result.warnings:
                print(f"runaway: {line}", file=sys.stderr)
            if result.outcome == OUTCOME_TRIPPED:
                emit_error_contract(**result.error, project=project)
        elif args.command == "status":
            _checked_project(ctx, args.project)
            rows = _status_rows(ctx, args.project, _now(_cli_now(args.now)))
            if args.json:
                print(json.dumps(rows, indent=2, sort_keys=True))
            else:
                for row in rows:
                    state = "off" if row["threshold"] == 0 else f"{row['count']}/{row['threshold']}"
                    where = "" if row["subject"] == PROJECT_SUBJECT else f" (highest: {row['subject']})"
                    print(f"{row['counter']:<24} {state:>8} within {row['window']}{where}")
        else:
            _checked_project(ctx, args.project)
            thresholds = resolve_thresholds(ctx.kanban_root, args.project)
            values: dict[str, object] = {spec.cfg_key: thresholds[spec.name] for spec in COUNTERS}
            values[HALT_AFTER_AUTO_RESUME_KEY] = resolve_halt_after_auto_resume(ctx.kanban_root, args.project)
            if args.json:
                print(json.dumps(values, indent=2, sort_keys=True))
            else:
                for key, value in values.items():
                    print(f"{key} = {str(value).lower() if isinstance(value, bool) else value}")
    except RunawayError as exc:
        emit_error_contract(exc.error, exc.message, exc.remedy, project=project)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

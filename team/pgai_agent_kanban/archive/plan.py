"""
plan.py — choose the finished items an archive run would move, and move nothing.

``plan(project, count)`` reads one live project and returns an
:class:`ArchivePlan`: the items eligible to leave the live tree, the queue
lines and training logs that follow them, and every older item that stays
live with the reason it stays. It only reads; the live tree, the archive
tree and every other file are left byte-for-byte as they were. Moving the
planned items is a separate step that consumes the plan.

Selection, per category
-----------------------
Bugs, priorities, requirements and tasks are counted separately, and tasks
are counted per role: each role named at the head of a task ID (PM, CODER,
WRITER, TESTER, CM, PO, OVERWATCH, ...) is its own group. In each category,
or role, the newest ``count`` items stay live whatever their state; only the
older items are candidates, and a candidate is selected only when it is
finished and nothing live still needs it.

    category      live item                  newest by           finished when
    bugs          bugs/BUG-NNNN-*.md         bug number          ## Status done, wont-do,
                                                                 duplicate or rejected
    priority      priority/PRIORITY-NNNN-*.md  priority number   ## Status done or wont-do
    requirements  requirements/vX.Y.Z-*.md   semver of the name  ## Status done or shipped, and
                                                                 ## Target Version <= Last Released
    tasks         tasks/<TASK-ID>/           per role: release   status.md ## State DONE or WONT-DO
                                             version, then task
                                             date and sequence

Roles are counted apart because each numbers its tasks from its own daily
counter (PM's run ``-001``, ``-002``, ... while other roles share a counter
that runs far higher), so one cross-role order would treat a role with a low
counter as old and archive its recent history first.

Within a role a task's ``## Release Version`` (read by
``cm.release_version.release_version_of``, so ``ai_v1.3.0`` and a
backticked ``v1.3.0`` both mean v1.3.0) orders it first, so a task of a newer release is never
archived before one of an older release. A task with no readable release
version (absent, ``none``, unreadable README) takes the highest version of
the tasks its role created before it (by date, then sequence), so it sits
where it was created; with none before it, it is the oldest of its role.

Versions are ordered and compared only through ``lib.semver``; Last Released
is read through ``lib.released_version.last_released_version``; closed states
come from ``lib.terminal_states`` (a legacy ``superseded`` reads as
``wont-do``). Ties in the order (two bug files with one number, two
requirements files for one version, two tasks of one role with one date and
sequence) break by name, so the plan is stable.

An older, finished item still stays live while anything live references it
as unfinished:

- a task named in the ``## Prerequisites`` of a live task that is not DONE
  or WONT-DO (a task whose own state cannot be read counts as not done);
- a bug or priority listed in the ``## Bundled Items`` of a bundle whose
  ``## Status`` is not ``done``, ``shipped`` or ``wont-do`` (an empty or
  absent status reads as open, as ``lib.bundle_items`` reads it);
- a requirement whose ``## Target Version`` is the Active RC;
- a requirement that lists, under ``## Bundled Items``, an item whose own
  ``## Status`` is still ``running`` (``lib.bundle_items.running_items``):
  moving it would leave that item running with no live bundle naming it,
  so the hold names the item;
- a task whose ``## Release Version`` is the Active RC.

Fail closed
-----------
An item whose own status cannot be read — a missing or empty field, an
unreadable file, a version that does not parse — is never selected. When
``release-state.md`` fails validation the Active RC is unknown, so no task
and no requirement is selected (bugs and priorities do not depend on it);
the plan's ``notes`` say so.

Followers
---------
Each selected task, bug or priority brings along every line in
``tasks/queues/*_backlog.md`` that names it (a Markdown list item ``- [<m>]
<ID> ...``, whatever the brackets hold -- the same lines the shared
queue-marker writer matches for that ID), and each selected task brings along every
``logs/training/<role>/*<TASK-ID>*.md`` file. A training log matches a task
only when the ID is not continued by another slug character, so the log of
``…-024-archive`` is never claimed for ``…-024-archive-plan``.

Public API
----------
``plan(project, count=None, *, kanban_root=None) -> ArchivePlan``
    ``count`` (items kept live per category, and per role for tasks)
    defaults to the project's ``[archive] archive_count``
    (``config.effective_archive_count``); ``kanban_root`` defaults to the
    root ``env.resolve_kanban_root`` reads. ``count`` 0 is an empty plan.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import pathlib
import re
from typing import Iterable, Optional, Union

from pgai_agent_kanban.archive.config import effective_archive_count
from pgai_agent_kanban.archive.layout import (
    CATEGORY_BUGS,
    CATEGORY_PRIORITY,
    CATEGORY_REQUIREMENTS,
    CATEGORY_TASKS,
    ITEM_CATEGORIES,
    ArchiveLayoutError,
    ProjectLayout,
    live_layout,
)
from pgai_agent_kanban.archive.lookup import classify_item_id
from pgai_agent_kanban.cm.read_state_field import ReleaseStateValidationError, read_release_state_field
from pgai_agent_kanban.cm.release_version import release_version_of
from pgai_agent_kanban.env import resolve_kanban_root
from pgai_agent_kanban.lib import md_fields, semver
from pgai_agent_kanban.lib.bundle_items import CLOSED_BUNDLE_STATUSES, bundle_listings, running_items
from pgai_agent_kanban.lib.released_version import last_released_version
from pgai_agent_kanban.lib.terminal_states import CLOSED_STATES, canonical_state
from pgai_agent_kanban.task_status import read_state

__all__ = [
    "ACTIVE_RC_UNKNOWN",
    "ArchivePlan",
    "HOLD_ACTIVE_RC_UNKNOWN",
    "HOLD_CARRIED_ITEM",
    "HOLD_IN_ACTIVE_RC",
    "HOLD_IN_OPEN_BUNDLE",
    "HOLD_NOT_RELEASED",
    "HOLD_PREREQUISITE",
    "HOLD_RELEASE_VERSION_UNREADABLE",
    "HOLD_STATUS_UNREADABLE",
    "HOLD_UNFINISHED",
    "HeldItem",
    "PlannedItem",
    "QueueLine",
    "TrainingLog",
    "plan",
]

PathLike = Union[str, "os.PathLike[str]"]

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: ``## Status`` values (canonical, lower-case) that make each intake item
#: finished. Bugs add the two triage closures to the shared closed states.
_BUG_FINISHED = frozenset((*CLOSED_STATES, "duplicate", "rejected"))
_PRIORITY_FINISHED = frozenset(CLOSED_STATES)
_REQUIREMENT_FINISHED = frozenset(("done", "shipped"))
#: Task ``## State`` values, compared after ``canonical_state``.
_TASK_FINISHED = frozenset(CLOSED_STATES)
#: A bundle in one of these states no longer holds the items it lists.
_BUNDLE_CLOSED = frozenset((*CLOSED_BUNDLE_STATUSES, "shipped"))

#: ``ArchivePlan.active_rc`` when release-state.md failed validation.
ACTIVE_RC_UNKNOWN = "unknown"
_NO_RC = "none"

#: ``HeldItem.code`` values — why an older item stays live.
HOLD_UNFINISHED = "unfinished"
HOLD_STATUS_UNREADABLE = "status-unreadable"
HOLD_NOT_RELEASED = "not-released"
HOLD_IN_ACTIVE_RC = "in-active-rc"
HOLD_ACTIVE_RC_UNKNOWN = "active-rc-unknown"
HOLD_RELEASE_VERSION_UNREADABLE = "release-version-unreadable"
HOLD_PREREQUISITE = "prerequisite-of-live-task"
HOLD_IN_OPEN_BUNDLE = "in-open-bundle"
HOLD_CARRIED_ITEM = "lists-running-item"

_MARKDOWN_SUFFIX = ".md"
_QUEUE_GLOB = "*_backlog.md"
_RELEASE_STATE_FILE = "release-state.md"

# A queue entry: "- [<marker>] <ID> ...". The brackets may hold anything up
# to the closing bracket ("[]", "[ ]", "[x]", or a hand-edited "[ x ]"), the
# same span the shared queue-marker writer (ops.write) matches, so every line
# that writer would remove for an ID is planned to follow it. Groups:
# ``marker`` (the bracket contents as written) and ``token`` (the ID word).
_QUEUE_ENTRY_RE = re.compile(r"^\s*-\s*\[(?P<marker>[^\]\n]*)\]\s+(?P<token>\S+)")
# The role, date and sequence inside a canonical task ID. A legacy
# ``CLAUDE-<ROLE>-...`` ID counts with its role; a bare ``CLAUDE-<date>-...``
# ID is its own ``CLAUDE`` group.
_TASK_ORDER_RE = re.compile(r"^(?:CLAUDE-)?(?P<role>[A-Z]+)-(?P<date>[0-9]{8})-(?P<sequence>[0-9]{3})-")
#: The version key of a task with no readable release version and no
#: versioned task of its role created before it: below every real release.
_NO_VERSION_KEY = (-1, -1, -1)
# Where a task ID can start inside a training-log file name; group 0 is the
# ID's "<AGENT>-<date>-<seq>-" head, the lookup key for selected tasks.
_TASK_HEAD_RE = re.compile(r"(?:CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-")
# A character that continues a task-ID slug.
_SLUG_CHAR_RE = re.compile(r"[a-z0-9-]")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TOKEN_TRIM = ".,;:()[]`'\""


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PlannedItem:
    """One item selected to leave the live tree.

    Attributes:
        category: One of ``layout.ITEM_CATEGORIES``.
        item_id:  The canonical ID (``lookup.classify_item_id``):
                  ``BUG-NNNN``, ``PRIORITY-NNNN``, the requirements file
                  name, or the task ID.
        path:     The item's live path (a file, or a task directory).
        relpath:  *path* relative to the live project root, ``/``-separated;
                  the item keeps this relative path in the archive tree.
    """

    category: str
    item_id: str
    path: pathlib.Path
    relpath: str


@dataclasses.dataclass(frozen=True)
class HeldItem:
    """An item older than the newest ``count`` that stays live.

    Attributes:
        category: One of ``layout.ITEM_CATEGORIES``.
        item_id:  The canonical ID.
        path:     The item's live path.
        code:     A ``HOLD_*`` constant: the first rule that kept it.
        detail:   The same reason in plain language, naming the value read.
    """

    category: str
    item_id: str
    path: pathlib.Path
    code: str
    detail: str


@dataclasses.dataclass(frozen=True)
class QueueLine:
    """A queue-file line that follows a selected item.

    Attributes:
        queue_file:  The live ``tasks/queues/<agent>_backlog.md`` file.
        line_number: 1-based line number in *queue_file*.
        text:        The line exactly as read, without its line ending.
        category:    The followed item's category.
        item_id:     The followed item's canonical ID.
    """

    queue_file: pathlib.Path
    line_number: int
    text: str
    category: str
    item_id: str


@dataclasses.dataclass(frozen=True)
class TrainingLog:
    """A training log that follows a selected task.

    Attributes:
        task_id: The followed task's ID.
        role:    The ``logs/training/<role>/`` directory it sits in.
        path:    The live file.
        relpath: *path* relative to the live project root, ``/``-separated.
    """

    task_id: str
    role: str
    path: pathlib.Path
    relpath: str


@dataclasses.dataclass(frozen=True)
class ArchivePlan:
    """What one archive run would move for one project.

    Attributes:
        project:       Project name.
        kanban_root:   The kanban root that was read.
        count:         Items kept live per category, and per role for
                       tasks; 0 means archiving is off.
        active_rc:     ``## Active RC`` as read (``none``, ``vX.Y.Z``), or
                       :data:`ACTIVE_RC_UNKNOWN`.
        last_released: The project's last released version as
                       ``released_version.last_released_version`` reads it.
        items:         Selected items, by category (``ITEM_CATEGORIES``
                       order), oldest first within each; tasks by role
                       (in name order), oldest first within each role.
        queue_lines:   Lines that follow selected items, by file then line.
        training_logs: Training logs that follow selected tasks, by path.
        held:          Older items that stay live, in category order.
        notes:         Plain-language notes on context the plan could not
                       read and what it held because of it.
    """

    project: str
    kanban_root: pathlib.Path
    count: int
    active_rc: str
    last_released: str
    items: tuple[PlannedItem, ...] = ()
    queue_lines: tuple[QueueLine, ...] = ()
    training_logs: tuple[TrainingLog, ...] = ()
    held: tuple[HeldItem, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when the plan moves nothing."""
        return not (self.items or self.queue_lines or self.training_logs)

    def items_in(self, category: str) -> tuple[PlannedItem, ...]:
        """The selected items of *category*, oldest first."""
        return tuple(item for item in self.items if item.category == category)

    def ids_in(self, category: str) -> tuple[str, ...]:
        """The selected IDs of *category*, oldest first."""
        return tuple(item.item_id for item in self.items_in(category))

    def queue_lines_for(self, item_id: str) -> tuple[QueueLine, ...]:
        """The queue lines that follow *item_id* (a canonical ID)."""
        return tuple(line for line in self.queue_lines if line.item_id == item_id)

    def training_logs_for(self, task_id: str) -> tuple[TrainingLog, ...]:
        """The training logs that follow *task_id*."""
        return tuple(log for log in self.training_logs if log.task_id == task_id)

    def to_dict(self) -> dict:
        """A JSON-ready rendering, paths as strings, for a CLI or API surface."""

        def _row(obj) -> dict:
            return {
                key: (str(value) if isinstance(value, pathlib.PurePath) else value)
                for key, value in dataclasses.asdict(obj).items()
            }

        return {
            "project": self.project,
            "kanban_root": str(self.kanban_root),
            "count": self.count,
            "active_rc": self.active_rc,
            "last_released": self.last_released,
            "items": [_row(item) for item in self.items],
            "queue_lines": [_row(line) for line in self.queue_lines],
            "training_logs": [_row(log) for log in self.training_logs],
            "held": [_row(item) for item in self.held],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Reading the live tree
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Candidate:
    """A live item and the key that orders it within its group.

    ``group`` is the set the newest ``count`` is counted in: ``""`` (the
    whole category) for bugs, priorities and requirements, the role for a
    task.
    """

    category: str
    item_id: str
    path: pathlib.Path
    sort_key: object
    group: str = ""


@dataclasses.dataclass(frozen=True)
class _TaskFacts:
    """What a task's status.md and README.md say, read once per task.

    ``release_version`` is ``None`` when README.md cannot be read.
    """

    state: str
    release_version: Optional[str]
    prerequisites: tuple[str, ...]


def _list_dir(directory: pathlib.Path) -> list[str]:
    """Entry names in *directory*, sorted; empty when it is absent or unreadable."""
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def _read_text(path: pathlib.Path) -> Optional[str]:
    """The UTF-8 text of *path*, or ``None`` when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _first_token(value: str) -> str:
    """The first whitespace-separated word of *value*, lower-cased; ``""`` if none."""
    tokens = value.split()
    return tokens[0].lower() if tokens else ""


def _numbered_candidates(layout: ProjectLayout, category: str) -> list[_Candidate]:
    """Bug or priority files in *layout*, keyed by (number, name)."""
    directory = layout.category_dir(category)
    found = []
    for name in _list_dir(directory):
        if not name.endswith(_MARKDOWN_SUFFIX):
            continue
        parsed = classify_item_id(name)
        path = directory / name
        if parsed is None or parsed.category != category or not path.is_file():
            continue
        found.append(_Candidate(category, parsed.canonical, path, (parsed.number, name)))
    return found


def _compare_requirement_names(a: str, b: str) -> int:
    """Order two requirements file names by their version, then by name."""
    by_version = semver.compare(semver.from_filename(a), semver.from_filename(b))
    if by_version:
        return by_version
    return (a > b) - (a < b)


def _requirement_candidates(layout: ProjectLayout) -> list[_Candidate]:
    """``vX.Y.Z-*.md`` files in *layout*'s requirements directory.

    The sort key orders by semver of the version in the name
    (:func:`_compare_requirement_names`), never by the name as text.
    """
    directory = layout.requirements
    found = []
    key = functools.cmp_to_key(_compare_requirement_names)
    for name in _list_dir(directory):
        parsed = classify_item_id(name)
        path = directory / name
        if parsed is None or parsed.category != CATEGORY_REQUIREMENTS or parsed.canonical != name:
            continue
        if not path.is_file():
            continue
        found.append(_Candidate(CATEGORY_REQUIREMENTS, name, path, key(name)))
    return found


def _task_candidates(layout: ProjectLayout) -> list[_Candidate]:
    """Task directories in *layout*, grouped by role, keyed by (date, sequence, ID).

    This is the creation order within a role; :func:`_order_tasks` puts
    the release version in front of it once each task's README is read.
    """
    directory = layout.tasks
    found = []
    for name in _list_dir(directory):
        parsed = classify_item_id(name)
        path = directory / name
        order = _TASK_ORDER_RE.match(name)
        if parsed is None or parsed.category != CATEGORY_TASKS or order is None or not path.is_dir():
            continue
        created = (order.group("date"), order.group("sequence"), name)
        found.append(_Candidate(CATEGORY_TASKS, name, path, created, order.group("role")))
    return found


def _release_version_key(release_version: Optional[str]) -> Optional[tuple[int, int, int]]:
    """The release a ``## Release Version`` value names, parsed, or ``None``.

    The value is read by :func:`release_version_of` (branch prefix, bold
    and backticks tolerated). ``None`` for an unreadable README, an empty
    field, ``none``, or a value that names no release version.
    """
    version = release_version_of(release_version or "")
    return semver.parse_version(version) if version else None


def _order_tasks(tasks: list[_Candidate], task_facts: dict) -> list[_Candidate]:
    """*tasks* re-keyed by (release version, date, sequence, ID) within each role.

    A task without a readable release version takes the highest version of
    the tasks its role created before it, or :data:`_NO_VERSION_KEY` when
    there is none, so it orders by when it was created.
    """
    rekeyed = []
    highest: dict[str, tuple[int, int, int]] = {}
    for candidate in sorted(tasks, key=lambda c: (c.group, c.sort_key)):
        own = _release_version_key(task_facts[candidate.item_id].release_version)
        seen = highest.get(candidate.group, _NO_VERSION_KEY)
        if own is not None and semver.gt(own, seen):
            highest[candidate.group] = own
        version = own if own is not None else seen
        rekeyed.append(dataclasses.replace(candidate, sort_key=(version, *candidate.sort_key)))
    return rekeyed


def _parse_prerequisites(readme_text: str) -> tuple[str, ...]:
    """Task IDs named in a README's ``## Prerequisites`` section.

    Read the way the wake reads them (a list line, comma-separated tokens,
    ``none`` meaning none, HTML comments ignored), and more liberally: any
    word on a line that has the canonical task-ID shape counts, so a
    prerequisite written with a trailing note still protects its task.
    """
    body = md_fields.section_body(readme_text, "Prerequisites")
    if not body:
        return ()
    found = []
    for line in _HTML_COMMENT_RE.sub("", body).splitlines():
        for word in line.replace(",", " ").split():
            parsed = classify_item_id(word.strip(_TOKEN_TRIM))
            if parsed is not None and parsed.category == CATEGORY_TASKS and parsed.canonical not in found:
                found.append(parsed.canonical)
    return tuple(found)


def _task_facts(task_dir: pathlib.Path) -> _TaskFacts:
    """Read one task's state, release version and prerequisites."""
    state = canonical_state(read_state(task_dir / "status.md"))
    readme = _read_text(task_dir / "README.md")
    if readme is None:
        return _TaskFacts(state, None, ())
    return _TaskFacts(
        state,
        md_fields.field_value(readme, "Release Version"),
        _parse_prerequisites(readme),
    )


def _read_active_rc(project_root: pathlib.Path) -> tuple[str, Optional[str]]:
    """``(active_rc, note)``: the validated ``## Active RC``, or unknown with a note.

    A missing release-state.md reads as ``none`` (a project that never opened
    an RC); a file that fails validation reads as :data:`ACTIVE_RC_UNKNOWN`.
    """
    path = project_root / _RELEASE_STATE_FILE
    try:
        return read_release_state_field(str(path), "Active RC"), None
    except ReleaseStateValidationError:
        return ACTIVE_RC_UNKNOWN, (
            f"{path} failed validation, so the Active RC is unknown; "
            "no task and no requirement is selected until it validates "
            f"(check it with: python3 -m pgai_agent_kanban.cm.read_state_field --validate {path})."
        )


def _same_version(a: str, b: str) -> bool:
    """True when *a* and *b* are the same release (both must parse)."""
    pa, pb = semver.parse_version(a), semver.parse_version(b)
    return pa is not None and pb is not None and semver.eq(pa, pb)


def _open_bundle_listings(layout: ProjectLayout) -> dict[str, str]:
    """Item file name -> status of an open bundle that lists it."""
    held = {}
    for name, statuses in bundle_listings(layout.requirements).items():
        for status in statuses:
            if canonical_state(status) not in _BUNDLE_CLOSED:
                held.setdefault(name, status)
                break
    return held


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Context:
    """Everything the hold rules read, gathered once per plan."""

    active_rc: str
    last_released: str
    open_bundles: dict
    live_prerequisites: dict
    task_facts: dict


def _intake_hold(
    candidate: _Candidate, finished: frozenset, context: _Context
) -> Optional[tuple[str, str]]:
    """The hold for an older bug or priority, or ``None`` when it may move."""
    status = _first_token(md_fields.read_field(candidate.path, "Status"))
    if not status:
        return HOLD_STATUS_UNREADABLE, "## Status is missing, empty or unreadable"
    if canonical_state(status) not in finished:
        return HOLD_UNFINISHED, f"## Status is '{status}'"
    bundle_status = context.open_bundles.get(candidate.path.name)
    if bundle_status is not None:
        return HOLD_IN_OPEN_BUNDLE, f"listed by a bundle whose ## Status is '{bundle_status}'"
    return None


def _requirement_hold(candidate: _Candidate, context: _Context) -> Optional[tuple[str, str]]:
    """The hold for an older requirement, or ``None`` when it may move."""
    text = _read_text(candidate.path)
    status = _first_token(md_fields.field_value(text, "Status")) if text is not None else ""
    if not status:
        return HOLD_STATUS_UNREADABLE, "## Status is missing, empty or unreadable"
    if canonical_state(status) not in _REQUIREMENT_FINISHED:
        return HOLD_UNFINISHED, f"## Status is '{status}'"
    target = md_fields.field_value(text, "Target Version").split()
    target_version = semver.parse_version(target[0]) if target else None
    if target_version is None:
        return HOLD_STATUS_UNREADABLE, "## Target Version is missing or not vX.Y.Z"
    if context.active_rc == ACTIVE_RC_UNKNOWN:
        return HOLD_ACTIVE_RC_UNKNOWN, "release-state.md failed validation; the Active RC is unknown"
    if context.active_rc != _NO_RC and _same_version(target[0], context.active_rc):
        return HOLD_IN_ACTIVE_RC, f"## Target Version {target[0]} is the Active RC"
    released = semver.parse_version(context.last_released)
    if released is None or not semver.le(target_version, released):
        return HOLD_NOT_RELEASED, (
            f"## Target Version {target[0]} is not at or below Last Released {context.last_released}"
        )
    running = running_items(candidate.path)
    if running:
        return HOLD_CARRIED_ITEM, (
            f"lists {', '.join(running)}, whose ## Status is still 'running'; "
            "moving it would leave that item with no live bundle"
        )
    return None


def _task_hold(candidate: _Candidate, context: _Context) -> Optional[tuple[str, str]]:
    """The hold for an older task, or ``None`` when it may move."""
    facts = context.task_facts[candidate.item_id]
    if not facts.state:
        return HOLD_STATUS_UNREADABLE, "status.md ## State is missing, empty or unreadable"
    if facts.state not in _TASK_FINISHED:
        return HOLD_UNFINISHED, f"status.md ## State is '{facts.state.upper()}'"
    if context.active_rc == ACTIVE_RC_UNKNOWN:
        return HOLD_ACTIVE_RC_UNKNOWN, "release-state.md failed validation; the Active RC is unknown"
    if context.active_rc != _NO_RC:
        release = facts.release_version
        if release is None:
            return HOLD_RELEASE_VERSION_UNREADABLE, (
                f"README.md is unreadable, so membership of the Active RC {context.active_rc} is unknown"
            )
        if release and release.lower() != _NO_RC:
            if not release_version_of(release):
                return HOLD_RELEASE_VERSION_UNREADABLE, (
                    f"## Release Version '{release}' is not vX.Y.Z while {context.active_rc} is active"
                )
            if _same_version(release_version_of(release), context.active_rc):
                return HOLD_IN_ACTIVE_RC, f"## Release Version {release} is the Active RC"
    dependants = context.live_prerequisites.get(candidate.item_id)
    if dependants:
        return HOLD_PREREQUISITE, f"a prerequisite of live unfinished task {dependants[0]}"
    return None


def _older(candidates: list[_Candidate], count: int) -> list[_Candidate]:
    """The candidates beyond the newest *count* of their group.

    Groups come in name order, oldest first within each; the newest
    *count* of every group stay out of the result.
    """
    groups: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.group, []).append(candidate)
    older = []
    for group in sorted(groups):
        ordered = sorted(groups[group], key=lambda c: c.sort_key)
        older.extend(ordered[: max(len(ordered) - count, 0)])
    return older


def _queue_followers(layout: ProjectLayout, followed: dict, notes: list) -> list[QueueLine]:
    """Lines in ``tasks/queues/*_backlog.md`` naming a followed ID.

    *followed* maps a canonical ID to its category.
    """
    lines = []
    for name in _list_dir(layout.queues):
        queue_file = layout.queues / name
        if not queue_file.match(_QUEUE_GLOB) or not queue_file.is_file():
            continue
        try:
            text = queue_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            notes.append(f"{queue_file} could not be read; no line in it is planned to follow its item.")
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            entry = _QUEUE_ENTRY_RE.match(line)
            if entry is None:
                continue
            parsed = classify_item_id(entry.group("token").strip(_TOKEN_TRIM))
            if parsed is None or parsed.canonical not in followed:
                continue
            lines.append(QueueLine(queue_file, number, line, followed[parsed.canonical], parsed.canonical))
    return lines


def _training_followers(layout: ProjectLayout, task_ids: Iterable[str], notes: list) -> list[TrainingLog]:
    """``logs/training/<role>/*<TASK-ID>*.md`` files of the selected tasks."""
    by_head: dict[str, list[str]] = {}
    for task_id in task_ids:
        head = _TASK_HEAD_RE.match(task_id)
        if head is not None:
            by_head.setdefault(head.group(0), []).append(task_id)
    if not by_head:
        return []
    logs = []
    for role in _list_dir(layout.training):
        if not (layout.training / role).is_dir():
            continue
        try:
            role_dir = layout.training_for(role)
        except ArchiveLayoutError:
            notes.append(
                f"{layout.training / role} is not a single safe role name; no training log under it is planned."
            )
            continue
        for name in _list_dir(role_dir):
            path = role_dir / name
            if not name.endswith(_MARKDOWN_SUFFIX) or not path.is_file():
                continue
            task_id = _task_in_name(name, by_head)
            if task_id is not None:
                logs.append(TrainingLog(task_id, role, path, path.relative_to(layout.root).as_posix()))
    return logs


def _task_in_name(name: str, by_head: dict) -> Optional[str]:
    """The selected task ID *name* contains whole, or ``None``.

    Whole means the character after the ID does not continue a slug.
    """
    for head in _TASK_HEAD_RE.finditer(name):
        for task_id in by_head.get(head.group(0), ()):
            end = head.start() + len(task_id)
            if name.startswith(task_id, head.start()) and not _SLUG_CHAR_RE.match(name, end):
                return task_id
    return None


def plan(
    project: str,
    count: Optional[int] = None,
    *,
    kanban_root: Optional[PathLike] = None,
) -> ArchivePlan:
    """Return what an archive run would move for *project*; move nothing.

    Args:
        project:     Project name (a single path component).
        count:       Items kept live per category, and per role for
                     tasks. ``None`` reads the
                     project's ``[archive] archive_count``; ``0`` (archiving
                     disabled) returns an empty plan without reading items.
        kanban_root: The kanban root holding ``projects/<project>/``.
                     ``None`` uses ``env.resolve_kanban_root()``.

    Returns:
        An :class:`ArchivePlan`. See the module docstring for the rules.

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
        ValueError: *count* is negative.
        RuntimeError: *kanban_root* is ``None`` and the kanban root
            environment variable is unset.
    """
    root = pathlib.Path(resolve_kanban_root() if kanban_root is None else kanban_root)
    layout = live_layout(root, project)
    if count is None:
        count = effective_archive_count(root, project)
    if count < 0:
        raise ValueError(f"archive plan: count must be 0 or more, got {count}")

    notes: list[str] = []
    active_rc, rc_note = _read_active_rc(layout.root)
    if rc_note:
        notes.append(rc_note)
    last_released = last_released_version(root, project)
    if count == 0:
        return ArchivePlan(project, root, 0, active_rc, last_released, notes=tuple(notes))

    tasks = _task_candidates(layout)
    task_facts = {task.item_id: _task_facts(task.path) for task in tasks}
    tasks = _order_tasks(tasks, task_facts)
    live_prerequisites: dict[str, list[str]] = {}
    for task_id, facts in task_facts.items():
        if facts.state in _TASK_FINISHED:
            continue
        for prerequisite in facts.prerequisites:
            live_prerequisites.setdefault(prerequisite, []).append(task_id)
    context = _Context(active_rc, last_released, _open_bundle_listings(layout), live_prerequisites, task_facts)

    by_category = {
        CATEGORY_BUGS: (_numbered_candidates(layout, CATEGORY_BUGS),
                        lambda c: _intake_hold(c, _BUG_FINISHED, context)),
        CATEGORY_PRIORITY: (_numbered_candidates(layout, CATEGORY_PRIORITY),
                            lambda c: _intake_hold(c, _PRIORITY_FINISHED, context)),
        CATEGORY_REQUIREMENTS: (_requirement_candidates(layout),
                                lambda c: _requirement_hold(c, context)),
        CATEGORY_TASKS: (tasks, lambda c: _task_hold(c, context)),
    }

    items: list[PlannedItem] = []
    held: list[HeldItem] = []
    for category in ITEM_CATEGORIES:
        candidates, hold_of = by_category[category]
        for candidate in _older(candidates, count):
            hold = hold_of(candidate)
            if hold is None:
                relpath = candidate.path.relative_to(layout.root).as_posix()
                items.append(PlannedItem(category, candidate.item_id, candidate.path, relpath))
            else:
                held.append(HeldItem(category, candidate.item_id, candidate.path, *hold))

    followed = {item.item_id: item.category for item in items if item.category != CATEGORY_REQUIREMENTS}
    queue_lines = _queue_followers(layout, followed, notes)
    training_logs = _training_followers(
        layout, (item.item_id for item in items if item.category == CATEGORY_TASKS), notes
    )
    return ArchivePlan(
        project,
        root,
        count,
        active_rc,
        last_released,
        tuple(items),
        tuple(queue_lines),
        tuple(sorted(training_logs, key=lambda log: log.relpath)),
        tuple(held),
        tuple(notes),
    )

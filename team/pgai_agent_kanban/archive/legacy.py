"""
legacy.py — fold the requirements archive kept inside a live project into the archive.

Before the archive tree existed, ``cleanup.sh`` moved shipped requirements
into the live project itself, at
``<kanban_root>/projects/<project>/tasks/archive/requirements/`` (priority
bundles in its ``priority/`` sub-directory). That is a second archive,
invisible to every reader that falls back to
``<kanban_root>/archive/<project>/``. This module empties it into the
archive tree:

- a file named like a priority item (``PRIORITY-NNNN-*.md``) moves to
  ``archive/<project>/priority/<file name>``;
- every other file, at any depth, moves to
  ``archive/<project>/requirements/<file name>``.

Files land flat in their category directory, under their own file name, so
``lookup.find_item`` finds them by ID exactly as it finds an item the
archive run moved. Directories are left in place, empty; nothing is
deleted.

Name clashes
------------
A file never overwrites anything. When the destination name is already
taken — the same requirement was archived from the live tree as well, or
the legacy tree holds one file name twice — the file moves to the first
free name of the form ``<stem>.legacy-<n><suffix>``
(``vX.Y.Z-slug.legacy-1.md``), so both copies are kept and the legacy
directory still ends up empty.

Moves and the ledger
--------------------
:func:`migrate_legacy` takes the project's repo lock (the same lock
``apply.apply`` takes, under the same rule: a busy lock skips the
migration and moves nothing, except that with ``accept_chain_lock`` a lock
held by the wake chain this process runs inside is worked under, as
``apply.apply`` describes), plans again under it, and moves one file at a time with
``move.move_path``, appending one ``ARCHIVE-LEDGER.tsv`` row per file
(category ``requirements`` or ``priority``, the file's ID, its legacy path
and its archive path). A failure stops the migration at that file, as an
archive run stops; files moved before it keep their rows and the next run
moves the rest. Once the legacy directory is empty a run finds nothing,
takes no lock and writes no row, which is what makes it safe to run on
every archive run.

Public API
----------
``legacy_requirements_dir(kanban_root, project) -> Path``
``plan_legacy(project, *, kanban_root=None) -> LegacyPlan``
``migrate_legacy(project, *, kanban_root=None, task="none", log=None, accept_chain_lock=False) -> RunResult``
``render_legacy_plan(plan) -> list[str]``
``LegacyMove``, ``LegacyPlan``, ``OP_MIGRATE``.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import shlex
from typing import Callable, Optional, Union

from pgai_agent_kanban.archive.apply import (
    _RUN_ERRORS,
    NO_TASK,
    RESULT_OK,
    RunResult,
    _fail,
    _move_item,
    _Run,
    _take_repo_lock,
)
from pgai_agent_kanban.archive.layout import (
    CATEGORY_PRIORITY,
    CATEGORY_REQUIREMENTS,
    TASKS_DIR,
    archive_layout,
    live_layout,
)
from pgai_agent_kanban.archive.lookup import classify_item_id
from pgai_agent_kanban.env import resolve_kanban_root
from pgai_agent_kanban.lib.released_version import last_released_version

__all__ = [
    "LEGACY_REQUIREMENTS_DIR",
    "OP_MIGRATE",
    "LegacyMove",
    "LegacyPlan",
    "legacy_requirements_dir",
    "migrate_legacy",
    "plan_legacy",
    "render_legacy_plan",
]

PathLike = Union[str, "os.PathLike[str]"]

#: The legacy requirements archive, relative to the live project root.
LEGACY_REQUIREMENTS_DIR = f"{TASKS_DIR}/archive/requirements"

#: Repo-lock ``op`` provenance of a migration.
OP_MIGRATE = "archive-legacy-migrate"

#: Inserted before a clashing file's suffix: ``<stem>.legacy-<n><suffix>``.
_CLASH_MARK = ".legacy-"


@dataclasses.dataclass(frozen=True)
class LegacyMove:
    """One legacy file and where it goes.

    Attributes:
        category:    ``priority`` or ``requirements``.
        item_id:     The ID recorded in the ledger: the canonical ID of the
                     destination name (``PRIORITY-NNNN``, ``vX.Y.Z-slug.md``),
                     or the destination file name when it has no ID shape.
        source:      The file in the legacy directory.
        destination: Its path in the archive tree.
    """

    category: str
    item_id: str
    source: pathlib.Path
    destination: pathlib.Path


@dataclasses.dataclass(frozen=True)
class LegacyPlan:
    """What a migration would move; computing it moves nothing.

    Attributes:
        kanban_root: The kanban root.
        project:     Project name.
        legacy_dir:  ``projects/<project>/tasks/archive/requirements``.
        moves:       One :class:`LegacyMove` per file, in path order.
        notes:       Directories that could not be listed; their files stay
                     where they are.
    """

    kanban_root: pathlib.Path
    project: str
    legacy_dir: pathlib.Path
    moves: tuple[LegacyMove, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to move."""
        return not self.moves


def legacy_requirements_dir(kanban_root: PathLike, project: str) -> pathlib.Path:
    """``<kanban_root>/projects/<project>/tasks/archive/requirements``.

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
    """
    return live_layout(kanban_root, project).root / LEGACY_REQUIREMENTS_DIR


def _legacy_files(directory: pathlib.Path, notes: list[str]) -> list[pathlib.Path]:
    """Every file (and symlink) under *directory*, sorted by relative path.

    A symlink is listed as an entry of its own and never followed. A
    directory that cannot be listed is named in *notes* and skipped.
    """
    if not directory.is_dir():
        return []

    def _unlistable(exc: OSError) -> None:
        notes.append(f"legacy archive: cannot list {exc.filename} ({exc.strerror}); its files stay where they are.")

    found = []
    for dirpath, dirnames, filenames in os.walk(directory, onerror=_unlistable):
        base = pathlib.Path(dirpath)
        found.extend(base / name for name in filenames)
        found.extend(base / name for name in dirnames if (base / name).is_symlink())
    return sorted(found, key=lambda path: path.relative_to(directory).as_posix())


def _free_destination(directory: pathlib.Path, name: str, taken: set[pathlib.Path]) -> pathlib.Path:
    """``directory/name``, or the first ``<stem>.legacy-<n><suffix>`` not on disk or in *taken*."""
    candidate = directory / name
    stem, suffix = os.path.splitext(name)
    number = 0
    while candidate in taken or os.path.lexists(candidate):
        number += 1
        candidate = directory / f"{stem}{_CLASH_MARK}{number}{suffix}"
    return candidate


def plan_legacy(project: str, *, kanban_root: Optional[PathLike] = None) -> LegacyPlan:
    """Plan the move of every legacy requirements-archive file of *project*.

    Args:
        project:     Project name.
        kanban_root: The kanban root; ``None`` uses
                     ``env.resolve_kanban_root()``.

    Returns:
        A :class:`LegacyPlan`; empty when the legacy directory is absent or
        holds no file.

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
    """
    root = pathlib.Path(resolve_kanban_root() if kanban_root is None else kanban_root)
    source_dir = legacy_requirements_dir(root, project)
    archive = archive_layout(root, project)
    notes: list[str] = []
    taken: set[pathlib.Path] = set()
    moves = []
    for source in _legacy_files(source_dir, notes):
        parsed = classify_item_id(source.name)
        category = CATEGORY_PRIORITY if parsed is not None and parsed.category == CATEGORY_PRIORITY else CATEGORY_REQUIREMENTS
        destination = _free_destination(archive.category_dir(category), source.name, taken)
        taken.add(destination)
        landed = classify_item_id(destination.name)
        moves.append(LegacyMove(category, landed.canonical if landed else destination.name, source, destination))
    return LegacyPlan(root, project, source_dir, tuple(moves), tuple(notes))


def _rel(root: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def render_legacy_plan(planned: LegacyPlan) -> list[str]:
    """The dry-run report of *planned*: a count line, then one line per file."""
    root = planned.kanban_root
    lines = [
        f"legacy requirements archive: {len(planned.moves)} file(s) would move from "
        f"{_rel(root, planned.legacy_dir)}"
    ]
    for move in planned.moves:
        lines.append(f"  {move.item_id}  {_rel(root, move.source)} -> {_rel(root, move.destination)}")
    lines.extend(f"note: {note}" for note in planned.notes)
    return lines


def migrate_legacy(
    project: str,
    *,
    kanban_root: Optional[PathLike] = None,
    task: str = NO_TASK,
    log: Optional[Callable[[str], None]] = None,
    accept_chain_lock: bool = False,
) -> RunResult:
    """Move every file of *project*'s legacy requirements archive into the archive tree.

    Args:
        project:     Project name.
        kanban_root: The kanban root; ``None`` uses
                     ``env.resolve_kanban_root()``.
        task:        The task this run is for (repo-lock provenance);
                     ``none`` when it is not for one task.
        log:         Receives each narration line (default: stderr).
        accept_chain_lock: Work under the repo lock when the wake chain
                     this process runs inside holds it, as
                     :func:`~pgai_agent_kanban.archive.apply.apply` does.

    Returns:
        A :class:`~pgai_agent_kanban.archive.apply.RunResult` with ``op``
        :data:`OP_MIGRATE` and one ledger row per file moved: ``ok`` (also
        when there was nothing to move), ``skipped`` (the repo lock was
        held; nothing moved) or ``failed`` (stopped at ``failed_item``).

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
    """
    root = pathlib.Path(resolve_kanban_root() if kanban_root is None else kanban_root)
    run = _Run(root, project, OP_MIGRATE, log)
    if plan_legacy(project, kanban_root=root).is_empty:
        return run.result(RESULT_OK)

    held = _take_repo_lock(run, task, accept_chain_lock)
    if isinstance(held, RunResult):
        return held
    with held:
        planned = plan_legacy(project, kanban_root=root)
        for note in planned.notes:
            run.say(f"archive: {note}")
        release = last_released_version(root, project)
        for move in planned.moves:
            try:
                _move_item(run, move.item_id, move.source, move.destination, release, move.category)
            except _RUN_ERRORS as exc:
                return _fail(run, move.item_id, exc, f"ls -ld {shlex.quote(str(move.source))}")
        run.say(
            f"archive: project '{project}': moved {len(run.rows)} file(s) from the legacy requirements "
            f"archive {planned.legacy_dir} to {run.archive.root}."
        )
        return run.result(RESULT_OK)

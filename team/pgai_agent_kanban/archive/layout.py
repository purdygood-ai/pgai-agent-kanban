"""
layout.py — where a project's live items and its archived items live.

The archive mirrors the live project: an item archived from
``<kanban_root>/projects/<project>/<relative path>`` lands at
``<kanban_root>/archive/<project>/<relative path>``, under the same
sub-directory names. Both trees are described by one :class:`ProjectLayout`
built from the same relative-name constants, so the two can never disagree
about where a category lives::

    <root>/
      bugs/                    BUG-NNNN-*.md
      priority/                PRIORITY-NNNN-*.md
      requirements/            vX.Y.Z-*.md
      tasks/                   <TASK-ID>/
      tasks/queues/            <agent>_backlog.md
      logs/training/<role>/    *<TASK-ID>*.md
      ARCHIVE-LEDGER.tsv       (archive tree only: one row per move)

Public API
----------
``archive_root(kanban_root, project) -> Path``
    ``<kanban_root>/archive/<project>``.

``archive_layout(kanban_root, project) -> ProjectLayout``
    The archive tree for *project*.

``live_layout(kanban_root, project) -> ProjectLayout``
    The live project tree, ``<kanban_root>/projects/<project>``.

``archive_layout_for_category_dir(category_dir, category) -> ProjectLayout | None``
    The archive tree that belongs to a live category directory such as
    ``<kanban_root>/projects/<project>/bugs``, or ``None`` when
    *category_dir* does not have that shape.

``archive_layout_for_tasks_dir(tasks_dir) -> ProjectLayout | None``
    The same, for a live ``tasks/`` directory
    (``<kanban_root>/projects/<project>/tasks``).

``display_relpath(root, path) -> str``
    *path* relative to *root*, ``/``-separated, as archive reports print it;
    *path* unchanged when it is not under *root*.

Nothing here touches the filesystem: every function only builds paths, so
a layout can be computed for an archive that does not exist yet.

A project name or training role that is not a single safe path component
is refused with :class:`ArchiveLayoutError` before any path is built, so no
caller can be steered outside the kanban root's ``archive/`` or
``projects/`` directory by a crafted name.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import shlex
from typing import Optional, Union

from pgai_agent_kanban.lib.error_contract import format_error_contract

__all__ = [
    "ARCHIVE_DIR_NAME",
    "ArchiveLayoutError",
    "BUGS_DIR",
    "CATEGORY_BUGS",
    "CATEGORY_PRIORITY",
    "CATEGORY_REQUIREMENTS",
    "CATEGORY_TASKS",
    "ITEM_CATEGORIES",
    "LEDGER_FILE_NAME",
    "LIVE_PROJECTS_DIR_NAME",
    "PRIORITY_DIR",
    "ProjectLayout",
    "QUEUES_DIR",
    "REQUIREMENTS_DIR",
    "TASKS_DIR",
    "TRAINING_DIR",
    "archive_layout",
    "archive_layout_for_category_dir",
    "archive_layout_for_tasks_dir",
    "archive_root",
    "display_relpath",
    "live_layout",
]

PathLike = Union[str, "os.PathLike[str]"]

#: Directory under the kanban root that holds one archive tree per project.
ARCHIVE_DIR_NAME = "archive"
#: Directory under the kanban root that holds the live projects.
LIVE_PROJECTS_DIR_NAME = "projects"
#: Ledger file at the root of a project's archive tree.
LEDGER_FILE_NAME = "ARCHIVE-LEDGER.tsv"

# Relative names, shared by the live and the archive tree.
BUGS_DIR = "bugs"
PRIORITY_DIR = "priority"
REQUIREMENTS_DIR = "requirements"
TASKS_DIR = "tasks"
QUEUES_DIR = "tasks/queues"
TRAINING_DIR = "logs/training"

#: Category names: the item kinds that are counted and archived on their own.
#: Each equals its directory's relative name.
CATEGORY_BUGS = BUGS_DIR
CATEGORY_PRIORITY = PRIORITY_DIR
CATEGORY_REQUIREMENTS = REQUIREMENTS_DIR
CATEGORY_TASKS = TASKS_DIR
ITEM_CATEGORIES = (CATEGORY_BUGS, CATEGORY_PRIORITY, CATEGORY_REQUIREMENTS, CATEGORY_TASKS)

# A project name, per the kanban's project-naming rule; a training role is
# held to the same shape (the role directories are lower-case agent names).
_PATH_COMPONENT_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


class ArchiveLayoutError(ValueError):
    """A project name or role is not a single safe path component.

    ``str(exc)`` is the three-line dual-channel block.

    Attributes:
        error:   Stable machine key, ``archive.invalid_path_component``.
        message: What was refused and why.
        remedy:  A runnable command that lists the accepted names.
    """

    def __init__(self, error: str, message: str, remedy: str) -> None:
        self.error = error
        self.message = message
        self.remedy = remedy
        super().__init__(format_error_contract(error, message, remedy))


def _require_path_component(value: str, what: str, listing: str) -> str:
    """Return *value* when it is one safe path component, else refuse.

    *what* names the value in the message (``project name``, ``training
    role``); *listing* is the directory whose entries are the accepted
    names, printed in the remedy.
    """
    if _PATH_COMPONENT_RE.fullmatch(value or ""):
        return value
    # BLOCK-WORTHY (docs/blocking-site-census.md): a name carrying a path
    # separator, '..', or upper-case/odd characters would build an archive
    # path outside <kanban_root>/archive/<project>/, and a later move to it
    # would scatter or overwrite live state. Only the operation that asked
    # for the path is refused; the archive run treats it as a failed run.
    # CENSUS-ANCHOR: archive-layout-path-component-01
    raise ArchiveLayoutError(
        "archive.invalid_path_component",
        f"archive: {what} '{value}' is not a single path component "
        "(expected lower-case letters, digits and '-', starting with a letter or digit); "
        "no archive path was built for it.",
        f"ls -1 {shlex.quote(listing)}",
    )


@dataclasses.dataclass(frozen=True)
class ProjectLayout:
    """One project tree — live or archived — and its fixed sub-paths.

    Attributes:
        root: The tree's root directory.
    """

    root: pathlib.Path

    @property
    def bugs(self) -> pathlib.Path:
        """``<root>/bugs`` — ``BUG-NNNN-*.md`` files."""
        return self.root / BUGS_DIR

    @property
    def priority(self) -> pathlib.Path:
        """``<root>/priority`` — ``PRIORITY-NNNN-*.md`` files."""
        return self.root / PRIORITY_DIR

    @property
    def requirements(self) -> pathlib.Path:
        """``<root>/requirements`` — ``vX.Y.Z-*.md`` files."""
        return self.root / REQUIREMENTS_DIR

    @property
    def tasks(self) -> pathlib.Path:
        """``<root>/tasks`` — one ``<TASK-ID>/`` folder per task."""
        return self.root / TASKS_DIR

    @property
    def queues(self) -> pathlib.Path:
        """``<root>/tasks/queues`` — ``<agent>_backlog.md`` queue files."""
        return self.root / QUEUES_DIR

    @property
    def training(self) -> pathlib.Path:
        """``<root>/logs/training`` — one sub-directory per role."""
        return self.root / TRAINING_DIR

    @property
    def ledger(self) -> pathlib.Path:
        """``<root>/ARCHIVE-LEDGER.tsv`` — meaningful in the archive tree only."""
        return self.root / LEDGER_FILE_NAME

    def training_for(self, role: str) -> pathlib.Path:
        """``<root>/logs/training/<role>``.

        Raises:
            ArchiveLayoutError: *role* is not a single safe path component.
        """
        return self.training / _require_path_component(role, "training role", str(self.training))

    def category_dir(self, category: str) -> pathlib.Path:
        """The directory holding *category*'s items (one of ``ITEM_CATEGORIES``).

        Raises:
            KeyError: *category* is not one of ``ITEM_CATEGORIES``.
        """
        return {
            CATEGORY_BUGS: self.bugs,
            CATEGORY_PRIORITY: self.priority,
            CATEGORY_REQUIREMENTS: self.requirements,
            CATEGORY_TASKS: self.tasks,
        }[category]


def archive_root(kanban_root: PathLike, project: str) -> pathlib.Path:
    """Return ``<kanban_root>/archive/<project>``.

    Raises:
        ArchiveLayoutError: *project* is not a single safe path component.
    """
    root = pathlib.Path(kanban_root)
    name = _require_path_component(project, "project name", str(root / LIVE_PROJECTS_DIR_NAME))
    return root / ARCHIVE_DIR_NAME / name


def archive_layout(kanban_root: PathLike, project: str) -> ProjectLayout:
    """Return the archive tree for *project*. See :func:`archive_root`."""
    return ProjectLayout(archive_root(kanban_root, project))


def live_layout(kanban_root: PathLike, project: str) -> ProjectLayout:
    """Return the live tree, ``<kanban_root>/projects/<project>``.

    Raises:
        ArchiveLayoutError: *project* is not a single safe path component.
    """
    root = pathlib.Path(kanban_root) / LIVE_PROJECTS_DIR_NAME
    return ProjectLayout(root / _require_path_component(project, "project name", str(root)))


def archive_layout_for_category_dir(category_dir: PathLike, category: str) -> Optional[ProjectLayout]:
    """Return the archive tree that belongs to the live directory *category_dir*.

    *category_dir* is read as ``<kanban_root>/projects/<project>/<category>``
    — the directory :func:`live_layout` builds for *category* (one of
    ``ITEM_CATEGORIES``) — and the archive returned is
    ``<kanban_root>/archive/<project>/`` of that same kanban root. Deriving
    the archive from the directory a reader was handed, rather than from an
    environment variable, keeps the two in one tree: a reader pointed at a
    sandbox directory can only ever consult that sandbox's archive.

    Returns ``None`` — never raises — when *category* is not one of
    ``ITEM_CATEGORIES``, *category_dir* is not named for it, its parent's
    parent is not named ``projects``, or the project name is not a single
    safe path component (a single-project install keeps its categories
    directly under the kanban root and has no per-project archive). The
    path is taken as given; symlinks are not resolved.
    """
    if category not in ITEM_CATEGORIES:
        return None
    live_dir = pathlib.Path(category_dir)
    project_root = live_dir.parent
    projects_dir = project_root.parent
    if live_dir.name != category or projects_dir.name != LIVE_PROJECTS_DIR_NAME:
        return None
    if not _PATH_COMPONENT_RE.fullmatch(project_root.name):
        return None
    return archive_layout(projects_dir.parent, project_root.name)


def archive_layout_for_tasks_dir(tasks_dir: PathLike) -> Optional[ProjectLayout]:
    """Return the archive tree that belongs to the live ``tasks/`` directory *tasks_dir*.

    *tasks_dir* is read as ``<kanban_root>/projects/<project>/tasks``; see
    :func:`archive_layout_for_category_dir`, which this is for the
    ``tasks`` category. Returns ``None`` — never raises — when *tasks_dir*
    does not have that shape.
    """
    return archive_layout_for_category_dir(tasks_dir, CATEGORY_TASKS)


def display_relpath(root: PathLike, path: PathLike) -> str:
    """*path* relative to *root*, ``/``-separated, when it lies under it; else *path* as given.

    The form every archive report prints a path in, so a dry run reads the
    same whichever surface (CLI or REST) asked for it.
    """
    try:
        return pathlib.Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)

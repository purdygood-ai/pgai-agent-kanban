"""
lookup.py — find an item by ID in a project tree, live or archived.

An item ID names exactly one item in one category:

    ID shape                          category       item in the tree
    BUG-NNNN                          bugs           bugs/BUG-NNNN-*.md
    PRIORITY-NNNN                     priority       priority/PRIORITY-NNNN-*.md
    vX.Y.Z-slug (".md" optional)      requirements   requirements/vX.Y.Z-slug.md
    <AGENT>-YYYYMMDD-NNN-slug         tasks          tasks/<TASK-ID>/

Reading is liberal where the meaning is unambiguous: surrounding
whitespace is ignored; the ``BUG``/``PRIORITY`` prefix is matched in any
case, may be joined to its number by ``-`` or ``_`` or nothing, and may be
followed by the file's slug; bugs and priorities match by number, so
zero-padding does not matter; a requirements ID may carry or omit ``.md``. A task ID must
have the canonical shape ``team/scripts/lib/task_ids.sh`` documents
(``^(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$``): it becomes a
directory name, and that shape cannot contain ``/`` or ``..``. No ID shape
here can name a path outside its category directory.

Public API
----------
``classify_item_id(item_id) -> ItemId | None``
    The category and normalized form of *item_id*, or ``None`` when it has
    none of the shapes above.

``find_item(layout, item_id) -> Path | None``
    The item's path in *layout* (any :class:`ProjectLayout`, live or
    archived), or ``None``.

``find_archived(kanban_root, project, item_id) -> Path | None``
    The item's path in ``<kanban_root>/archive/<project>/`` when it has been
    archived there, else ``None`` — including when the project has no
    archive directory at all.

``archived_task_ids(tasks_dir, task_ids) -> list[str]``
    Those of *task_ids* that have no live folder under the live ``tasks/``
    directory *tasks_dir* and whose folder is in the archive of the project
    *tasks_dir* belongs to. Prerequisite resolution treats each one as DONE:
    only finished tasks are ever archived.

``archived_task_dirs(tasks_dir) -> list[Path]``
    Every task folder in the archive of the project the live ``tasks/``
    directory *tasks_dir* belongs to whose ID has no live folder, sorted by
    task ID.

``task_dirs_with_archived(tasks_dir) -> list[Path]``
    The live task folders under *tasks_dir* (every sub-directory except
    ``queues``) together with :func:`archived_task_dirs`, sorted by task
    ID. The token and metrics aggregators walk this list, so a roll-up
    rebuilt after an archive run counts the same tasks it counted before.

Lookup only reads directory listings; it never creates, moves or opens an
item.

Module CLI
----------
``python3 -m pgai_agent_kanban.archive archived_tasks --tasks-root=DIR ID...``
prints each ID :func:`archived_task_ids` returns, one per line, in the order
given, and exits 0 (no output when none is archived). A usage error exits
2 (argparse). It is how the wake prerequisite gate
(``team/scripts/lib/wake_task_id_gate.sh``) reaches the archive through
``pp_run_ops`` without inline Python; the gate treats any non-zero exit as
"nothing archived", so a lookup fault leaves a prerequisite unsatisfied
rather than satisfying it.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from typing import Iterable, Optional, Union

from pgai_agent_kanban.archive.layout import (
    CATEGORY_BUGS,
    CATEGORY_PRIORITY,
    CATEGORY_REQUIREMENTS,
    CATEGORY_TASKS,
    ProjectLayout,
    archive_layout,
    archive_layout_for_tasks_dir,
)

__all__ = [
    "CLI_VERBS",
    "ItemId",
    "archived_task_dirs",
    "archived_task_ids",
    "classify_item_id",
    "find_archived",
    "find_item",
    "task_dirs_with_archived",
]

#: Verbs ``python3 -m pgai_agent_kanban.archive`` routes to this module.
CLI_VERBS: tuple[str, ...] = ("archived_tasks",)

PathLike = Union[str, "os.PathLike[str]"]

# Numbered items: the ID a caller passes, and the file names in the tree.
_NUMBERED_PREFIXES = {CATEGORY_BUGS: "BUG", CATEGORY_PRIORITY: "PRIORITY"}
_NUMBERED_ID_RE = {
    category: re.compile(rf"{prefix}[-_ ]?([0-9]+)(?:[-_.].*)?", re.IGNORECASE)
    for category, prefix in _NUMBERED_PREFIXES.items()
}
_NUMBERED_FILE_RE = {
    category: re.compile(rf"{prefix}-([0-9]+)(?:-.*)?\.md", re.IGNORECASE)
    for category, prefix in _NUMBERED_PREFIXES.items()
}

# A requirements file name without its ".md": vX.Y.Z and an optional
# "-slug". The slug is one path component (no separator).
_REQUIREMENT_ID_RE = re.compile(r"[vV]([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9._-]+)?)")
_MARKDOWN_SUFFIX = ".md"

# The canonical task-id shape, as team/scripts/lib/task_ids.sh's
# kanban_task_id_pattern prints it.
_TASK_ID_RE = re.compile(r"(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+")


@dataclasses.dataclass(frozen=True)
class ItemId:
    """A classified item ID.

    Attributes:
        category:  One of the ``CATEGORY_*`` names in ``archive.layout``.
        canonical: The normalized ID: ``BUG-NNNN`` or ``PRIORITY-NNNN``
                   (number zero-padded to four digits), the requirements
                   file name ``vX.Y.Z-slug.md``, or the task ID.
        number:    The item number for bugs and priorities, else ``None``.
    """

    category: str
    canonical: str
    number: Optional[int] = None


def classify_item_id(item_id: str) -> Optional[ItemId]:
    """Return the category and normalized form of *item_id*, or ``None``."""
    text = (item_id or "").strip()
    if not text:
        return None
    for category, pattern in _NUMBERED_ID_RE.items():
        match = pattern.fullmatch(text)
        if match:
            number = int(match.group(1))
            return ItemId(category, f"{_NUMBERED_PREFIXES[category]}-{number:04d}", number)
    stem = text[: -len(_MARKDOWN_SUFFIX)] if text.lower().endswith(_MARKDOWN_SUFFIX) else text
    match = _REQUIREMENT_ID_RE.fullmatch(stem)
    if match:
        return ItemId(CATEGORY_REQUIREMENTS, f"v{match.group(1)}{_MARKDOWN_SUFFIX}")
    if _TASK_ID_RE.fullmatch(text):
        return ItemId(CATEGORY_TASKS, text)
    return None


def _find_numbered(directory: pathlib.Path, category: str, number: int) -> Optional[pathlib.Path]:
    """The first file (by name) in *directory* numbered *number*, or None."""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return None
    pattern = _NUMBERED_FILE_RE[category]
    for name in names:
        match = pattern.fullmatch(name)
        if match and int(match.group(1)) == number:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def find_item(layout: ProjectLayout, item_id: str) -> Optional[pathlib.Path]:
    """Return *item_id*'s path in *layout*, or ``None`` when it is not there.

    A bug or priority is a file whose number matches; a requirement is the
    file of that name; a task is the directory of that name. ``None`` also
    covers an ID with no recognised shape and a category directory that
    does not exist.
    """
    parsed = classify_item_id(item_id)
    if parsed is None:
        return None
    directory = layout.category_dir(parsed.category)
    if parsed.number is not None:
        return _find_numbered(directory, parsed.category, parsed.number)
    candidate = directory / parsed.canonical
    if parsed.category == CATEGORY_TASKS:
        return candidate if candidate.is_dir() else None
    return candidate if candidate.is_file() else None


def find_archived(kanban_root: PathLike, project: str, item_id: str) -> Optional[pathlib.Path]:
    """Return *item_id*'s path in *project*'s archive, or ``None``.

    Args:
        kanban_root: The kanban root holding ``archive/<project>/``.
        project:     Project name (a single path component).
        item_id:     ``BUG-NNNN``, ``PRIORITY-NNNN``, a ``vX.Y.Z-slug``
                     requirements file name, or a task ID.

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
    """
    return find_item(archive_layout(kanban_root, project), item_id)


def archived_task_ids(tasks_dir: PathLike, task_ids: Iterable[str]) -> list[str]:
    """Return those of *task_ids* that are archived and not live, in the order given.

    *tasks_dir* is a live project's ``tasks/`` directory
    (``<kanban_root>/projects/<project>/tasks``); the archive consulted is
    that same kanban root's ``archive/<project>/tasks/``
    (:func:`~pgai_agent_kanban.archive.layout.archive_layout_for_tasks_dir`).

    An ID is returned only when all of these hold:

    * it has the canonical task-id shape (anything else is never joined
      onto a path);
    * ``<tasks_dir>/<ID>`` does not exist — a live task is always read from
      its own ``status.md``, so a live folder, even one with no or an
      unreadable ``status.md``, is never answered from the archive;
    * ``<archive>/tasks/<ID>/`` is a directory.

    Every other case — *tasks_dir* not in the live-project shape, no archive
    directory, an ID not archived — contributes nothing, so a caller that
    falls back to this lookup behaves exactly as it did without an archive.
    Duplicates are reported once.
    """
    layout = archive_layout_for_tasks_dir(tasks_dir)
    if layout is None:
        return []
    live_tasks = pathlib.Path(tasks_dir)
    found: list[str] = []
    for task_id in task_ids:
        text = (task_id or "").strip()
        if text in found or not _TASK_ID_RE.fullmatch(text):
            continue
        if os.path.lexists(live_tasks / text):
            continue
        if find_item(layout, text) is not None:
            found.append(text)
    return found


def archived_task_dirs(tasks_dir: PathLike) -> list[pathlib.Path]:
    """Return the archived task folders of *tasks_dir*'s project that are not live.

    *tasks_dir* is a live project's ``tasks/`` directory
    (``<kanban_root>/projects/<project>/tasks``); the folders returned are
    ``<kanban_root>/archive/<project>/tasks/<ID>/``, sorted by ID. A folder
    is returned only when its name has the canonical task-id shape (so the
    archive's ``tasks/queues/`` is never one) and ``<tasks_dir>/<ID>`` does
    not exist: a task that is live is always read from its live folder,
    exactly as :func:`archived_task_ids` decides.

    Returns an empty list — never raises — when *tasks_dir* is not in the
    live-project shape, the project has no archive, or the archive's
    ``tasks/`` directory cannot be listed, so a reader that adds these
    folders to its live walk reads exactly what it read without an archive.
    """
    layout = archive_layout_for_tasks_dir(tasks_dir)
    if layout is None:
        return []
    try:
        names = sorted(os.listdir(layout.tasks))
    except OSError:
        return []
    live_tasks = pathlib.Path(tasks_dir)
    found: list[pathlib.Path] = []
    for name in names:
        if not _TASK_ID_RE.fullmatch(name) or os.path.lexists(live_tasks / name):
            continue
        candidate = layout.tasks / name
        if candidate.is_dir():
            found.append(candidate)
    return found


#: Sub-directories of a live ``tasks/`` directory that are not task folders.
_NON_TASK_DIR_NAMES = frozenset({"queues", "queue"})


def task_dirs_with_archived(tasks_dir: PathLike) -> list[pathlib.Path]:
    """Return the live and the archived task folders of *tasks_dir*'s project, sorted by ID.

    The live folders are every sub-directory of *tasks_dir* except
    ``queues`` (and its singular spelling); the archived ones are
    :func:`archived_task_dirs`. The list is sorted by folder name alone, so
    a task appears at the same position whether it is live or archived, and
    a reader that walks it in order produces the same output before and
    after an archive run. A missing *tasks_dir* contributes no live folders.
    """
    live = pathlib.Path(tasks_dir)
    folders: list[pathlib.Path] = []
    if live.is_dir():
        folders = [entry for entry in live.iterdir() if entry.is_dir() and entry.name not in _NON_TASK_DIR_NAMES]
    folders.extend(archived_task_dirs(live))
    return sorted(folders, key=lambda entry: entry.name)


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run :func:`archived_task_ids` from the command line.

    ``archived_tasks --tasks-root DIR ID...``: prints each archived ID on
    its own line and exits 0. See the module docstring.
    """
    import argparse
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        sys.stderr.write(f"pgai_agent_kanban.archive.lookup: VERB must be one of: {', '.join(CLI_VERBS)}\n")
        return 2
    parser = argparse.ArgumentParser(prog="python3 -m pgai_agent_kanban.archive archived_tasks")
    parser.add_argument("--tasks-root", required=True, help="the live project's tasks/ directory")
    parser.add_argument("task_ids", nargs="*", metavar="ID", help="task IDs to look up")
    opts = parser.parse_args(args[1:])
    for task_id in archived_task_ids(opts.tasks_root, opts.task_ids):
        print(task_id)
    return 0

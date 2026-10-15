#!/usr/bin/env python3
"""
bundle_items.py — read which intake items the bundles in requirements/ list,
which of those items a shipped bundle left behind, and which bundle still
holds discovery's bundling slot.

A bundle is a requirements file with a ``## Bundled Items`` section. Each
item line names the item file by absolute path in backticks:

    - BUG-0001-example.md (`/abs/projects/p/bugs/BUG-0001-example.md`)

Discovery sets an item's ``## Status`` to ``running`` when it bundles it, and
the release that ships the bundle (cm-release Step 16b) promotes the item to
``done`` only when a DONE task claims its fix. An item no DONE task claims is
left at ``running`` while the bundle itself becomes ``done``: the item was
carried. A carried item is a candidate for the next bundle, exactly as an
``open`` item is. Discovery's item scan and the bug-backlog cache both read
that answer from :func:`carried_item_names`.

What counts as carried
----------------------
An item file whose own ``## Status`` is ``running`` is carried when at least
one bundle lists it and every bundle that lists it is closed -- its own
``## Status`` is ``done`` or ``wont-do``. An item listed by a bundle that is
still open or running is in flight and is not carried.

What counts as orphaned
-----------------------
A ``running`` item that no bundle in requirements/ lists at all is orphaned:
the bundle that set it running was archived, purged or deleted while the
item was still unfinished, so nothing will ever close it. Discovery's item
scan re-offers an orphaned item with a WARNING (:func:`is_orphaned`), so an
install whose bundle was already moved heals on its next tick. The archive
plan and the shipped-bundle purge hold a bundle while any item it lists is
still ``running`` (:func:`running_items`), so a new orphan is not made.

Superseded bundles
------------------
An RC unwind renames the RC's bundle to
``<name>.SUPERSEDED-on-cancel-<timestamp>.md`` and sets its ``## Status`` to
``wont-do``. A bundle with that name is closed whatever its ``## Status``
says, so a file renamed before its status was written never holds the slot
or the items it lists.

The bundling slot
-----------------
:func:`pending_bundle` answers discovery's mint governor: the first bundle
of one type (``bugfix`` or ``priority``) that has not reached a closed
``## Status`` holds the slot, and no second bundle of that type is minted
while it does. A ``running`` bundle whose ``## PM Task`` is DONE or WONT-DO
and whose version is not the Active RC is stuck instead -- its PM decomposed
nothing that will ever ship it -- and is reported rather than holding the
slot. Statuses are read through ``terminal_states.canonical_state``, so a
legacy ``superseded`` status reads as ``wont-do``.

Items are matched by file name, so a bundle written on another install root
still names the same item.

Public API:
    parse_bundled_item_paths(bundle_text) -> list[str]
    bundle_listings(requirements_dir) -> dict[str, list[str]]
    carried_names_from(listings) -> frozenset[str]
    carried_item_names(requirements_dir) -> frozenset[str]
    is_carried(status, name, carried) -> bool
    is_orphaned(status, name, listed) -> bool
    is_superseded_name(name) -> bool
    running_items(bundle_path) -> list[str]
    pending_bundle(requirements_dir, type_slug, tasks_dir=None,
                   release_state=None) -> SlotAnswer

Commands (``python3 -m pgai_agent_kanban.lib.bundle_items <command> ...``):

  pending REQUIREMENTS_DIR TYPE_SLUG [TASKS_DIR] [RELEASE_STATE]
      Print one ``stuck<TAB>PATH<TAB>PM_TASK<TAB>PM_STATE<TAB>VERSION`` line
      per stuck bundle of TYPE_SLUG, then ``pending<TAB>PATH`` for the bundle
      holding the slot when one does. TASKS_DIR defaults to the ``tasks``
      directory beside REQUIREMENTS_DIR, RELEASE_STATE to the
      ``release-state.md`` beside it.
  running-items BUNDLE
      Print the file name of each item BUNDLE lists whose own ``## Status``
      is ``running``, one per line, in listed order.

Both commands exit 0; a wrong argument count prints the usage on stderr and
exits 2. ``--help`` prints this text and exits 0.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import sys
from typing import Iterable, Mapping, Optional, Union

# Run by file path (``python3 .../bundle_items.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.read_state_field import (  # noqa: E402
    ReleaseStateValidationError,
    read_release_state_field,
)
from pgai_agent_kanban.lib import md_fields, semver  # noqa: E402
from pgai_agent_kanban.lib.terminal_states import (  # noqa: E402
    CLOSED_STATES,
    TERMINAL_STATES,
    canonical_state,
)
from pgai_agent_kanban.task_status import read_state  # noqa: E402

__all__ = [
    "CLOSED_BUNDLE_STATUSES",
    "SUPERSEDED_ON_CANCEL_MARKER",
    "SlotAnswer",
    "StuckBundle",
    "bundle_listings",
    "carried_item_names",
    "carried_names_from",
    "is_carried",
    "is_orphaned",
    "is_superseded_name",
    "parse_bundled_item_paths",
    "pending_bundle",
    "running_items",
]

PathLike = Union[str, "os.PathLike[str]"]

#: A bundle in one of these states has shipped or been abandoned; the items
#: it lists are no longer in flight because of it. The shared closed-state
#: list (lib.terminal_states.CLOSED_STATES); a bundle still carrying the
#: retired "superseded" reads as wont-do, so it is closed too.
CLOSED_BUNDLE_STATUSES = frozenset(CLOSED_STATES)

#: The infix an RC unwind puts in the name of the bundle it supersedes:
#: ``<name>.SUPERSEDED-on-cancel-<timestamp>.md``.
SUPERSEDED_ON_CANCEL_MARKER = ".SUPERSEDED-on-cancel-"

_RUNNING = "running"
_WONT_DO = "wont-do"
_NO_RC = "none"
_INTAKE_DIRS = ("bugs", "priority")

_BUNDLED_ITEMS_HEADING_RE = re.compile(r"^\s*##\s+Bundled Items\s*$", re.IGNORECASE)
_SECTION_HEADING_RE = re.compile(r"^\s*##")
_ITEM_PATH_RE = re.compile(r"\(`([^`]+)`\)")
# A ## PM Task value that can name a task folder: one path component.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse_bundled_item_paths(bundle_text: str) -> list[str]:
    """Extract the item paths from the ``## Bundled Items`` section.

    Returns the paths in listed order; an empty list when the section is
    absent or holds no backtick-in-parentheses path. Each item line reads:

        - FILENAME.md (`/absolute/path/to/FILENAME.md`)
    """
    in_section = False
    item_paths: list[str] = []
    for line in bundle_text.splitlines():
        if _BUNDLED_ITEMS_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section:
            if _SECTION_HEADING_RE.match(line):
                break
            m = _ITEM_PATH_RE.search(line)
            if m:
                item_paths.append(m.group(1).strip())
    return item_paths


def is_superseded_name(name: str) -> bool:
    """True when *name* is a bundle an RC unwind renamed (superseded)."""
    return SUPERSEDED_ON_CANCEL_MARKER.lower() in name.lower()


def _status_of(text: str) -> str:
    """The lower-cased first token of the ``## Status`` value; ``open`` when
    the field is absent or empty (discovery's default for a bundle)."""
    tokens = md_fields.field_value(text, "Status").split()
    return tokens[0].lower() if tokens else "open"


def _bundle_status(name: str, text: str) -> str:
    """A bundle's status as written, or ``wont-do`` for a superseded name."""
    return _WONT_DO if is_superseded_name(name) else _status_of(text)


def bundle_listings(requirements_dir: PathLike) -> dict[str, list[str]]:
    """Map each item file name a bundle lists to the statuses of the bundles
    that list it, in file-name order of the bundles.

    Reads every ``*.md`` file directly under *requirements_dir* that has a
    ``## Bundled Items`` section. A superseded bundle's status reads as
    ``wont-do`` whatever its file says. A missing directory or an unreadable
    file contributes nothing.
    """
    listings: dict[str, list[str]] = {}
    root = pathlib.Path(requirements_dir)
    if not root.is_dir():
        return listings
    for bundle in sorted(root.glob("*.md")):
        try:
            text = bundle.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        paths = parse_bundled_item_paths(text)
        if not paths:
            continue
        status = _bundle_status(bundle.name, text)
        for item_path in paths:
            listings.setdefault(pathlib.PurePath(item_path).name, []).append(status)
    return listings


def carried_names_from(listings: Mapping[str, list[str]]) -> frozenset[str]:
    """The names in *listings* (from :func:`bundle_listings`) whose every
    listing bundle is closed."""
    return frozenset(
        name
        for name, statuses in listings.items()
        if statuses and all(canonical_state(status) in CLOSED_BUNDLE_STATUSES for status in statuses)
    )


def carried_item_names(requirements_dir: PathLike) -> frozenset[str]:
    """Return the file names of the items whose every listing bundle is closed.

    The caller applies the item's own ``## Status`` (see :func:`is_carried`):
    only a ``running`` item in this set is carried.
    """
    return carried_names_from(bundle_listings(requirements_dir))


def is_carried(status: str, name: str, carried: Iterable[str]) -> bool:
    """True when an item at *status* named *name* was carried: it is
    ``running`` and its name is in *carried* (from :func:`carried_item_names`)."""
    return status.strip().lower() == _RUNNING and name in carried


def is_orphaned(status: str, name: str, listed: Iterable[str]) -> bool:
    """True when an item at *status* named *name* is orphaned: it is
    ``running`` and no bundle lists it (*listed* is the key set of
    :func:`bundle_listings`)."""
    return status.strip().lower() == _RUNNING and name not in listed


def _item_status(path: pathlib.Path) -> str:
    """The canonical first token of an item file's ``## Status``; ``""`` if none."""
    tokens = md_fields.read_field(path, "Status").split()
    return canonical_state(tokens[0]) if tokens else ""


def _item_file(project_root: pathlib.Path, listed_path: str) -> Optional[pathlib.Path]:
    """The live file of a listed item: by name in the intake directories
    beside the bundle, else the path as listed; ``None`` when neither exists."""
    name = pathlib.PurePath(listed_path).name
    for directory in _INTAKE_DIRS:
        candidate = project_root / directory / name
        if candidate.is_file():
            return candidate
    listed = pathlib.Path(listed_path)
    return listed if listed.is_file() else None


def running_items(bundle_path: PathLike) -> list[str]:
    """File names of the items *bundle_path* lists whose own ``## Status`` is
    ``running``, in listed order, repeats dropped.

    Each item is looked up by name in the ``bugs/`` and ``priority/``
    directories beside the bundle's ``requirements/`` directory, then at the
    path the bundle lists. An item whose file is gone is not running. An
    unreadable bundle lists nothing.
    """
    bundle = pathlib.Path(bundle_path)
    try:
        text = bundle.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    project_root = bundle.parent.parent
    found: list[str] = []
    for listed_path in parse_bundled_item_paths(text):
        item = _item_file(project_root, listed_path)
        if item is None or item.name in found:
            continue
        if _item_status(item) == _RUNNING:
            found.append(item.name)
    return found


# ---------------------------------------------------------------------------
# The bundling slot
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StuckBundle:
    """A ``running`` bundle nothing will ship.

    Attributes:
        path:     The bundle file.
        pm_task:  Its ``## PM Task`` value.
        pm_state: That task's ``## State``, canonical (``done``/``wont-do``).
        version:  The bundle's version (``## Target Version``, else the
                  version in its name).
    """

    path: pathlib.Path
    pm_task: str
    pm_state: str
    version: str


@dataclasses.dataclass(frozen=True)
class SlotAnswer:
    """What :func:`pending_bundle` found for one bundle type.

    Attributes:
        pending: The first bundle holding the slot, or ``None``.
        stuck:   Every stuck bundle of the type, in file-name order.
    """

    pending: Optional[pathlib.Path]
    stuck: tuple[StuckBundle, ...] = ()


def _read_active_rc(release_state: pathlib.Path) -> Optional[str]:
    """The validated ``## Active RC``; ``none`` when the file is absent;
    ``None`` (unknown) when it fails validation or cannot be read."""
    try:
        return read_release_state_field(str(release_state), "Active RC")
    except (ReleaseStateValidationError, OSError, UnicodeDecodeError):
        return None


def _bundle_version(name: str, text: str) -> str:
    """``## Target Version``'s first token, else the version in *name*."""
    tokens = md_fields.field_value(text, "Target Version").split()
    if tokens and semver.parse_version(tokens[0]) is not None:
        return tokens[0]
    return semver.from_filename(name)


def _stuck(
    path: pathlib.Path, text: str, tasks_dir: pathlib.Path, active_rc: Optional[str]
) -> Optional[StuckBundle]:
    """The :class:`StuckBundle` for a ``running`` bundle, or ``None`` when it
    may still ship (or when anything needed to tell is unknown)."""
    tokens = md_fields.field_value(text, "PM Task").split()
    pm_task = tokens[0] if tokens else ""
    if not _TASK_ID_RE.match(pm_task) or pm_task.lower() == _NO_RC:
        return None
    status_file = tasks_dir / pm_task / "status.md"
    if not status_file.is_file():
        return None
    pm_state = canonical_state(read_state(status_file))
    if pm_state not in TERMINAL_STATES or active_rc is None:
        return None
    version = _bundle_version(path.name, text)
    if active_rc != _NO_RC:
        ours, active = semver.parse_version(version), semver.parse_version(active_rc)
        if ours is None or active is None or semver.eq(ours, active):
            return None
    return StuckBundle(path, pm_task, pm_state, version)


def pending_bundle(
    requirements_dir: PathLike,
    type_slug: str,
    tasks_dir: Optional[PathLike] = None,
    release_state: Optional[PathLike] = None,
) -> SlotAnswer:
    """Find the ``*-<type_slug>-bundle-*.md`` bundle holding discovery's slot.

    A bundle holds the slot unless its name is superseded, its canonical
    ``## Status`` is closed (``done``/``wont-do``; ``superseded`` reads as
    ``wont-do``), or it is ``running`` and stuck: its ``## PM Task`` reads
    DONE or WONT-DO and its version is not the Active RC. A bundle with no
    ``## Status`` or an unreadable file holds the slot (it reads as open). A
    stuck verdict needs every fact: an unknown PM task, a missing task
    folder, a release-state.md that fails validation or a version that does
    not parse while an RC is active leave the bundle holding the slot.

    Args:
        requirements_dir: The project's ``requirements/`` directory.
        type_slug:        ``bugfix`` or ``priority``.
        tasks_dir:        The project's task folders; defaults to ``tasks``
                          beside *requirements_dir*.
        release_state:    The project's release-state.md; defaults to the
                          one beside *requirements_dir*.

    Returns:
        A :class:`SlotAnswer`: the first bundle holding the slot in
        file-name order, and every stuck bundle of the type.
    """
    root = pathlib.Path(requirements_dir)
    tasks = pathlib.Path(tasks_dir) if tasks_dir else root.parent / "tasks"
    state_file = pathlib.Path(release_state) if release_state else root.parent / "release-state.md"
    if not root.is_dir():
        return SlotAnswer(None)
    pending: Optional[pathlib.Path] = None
    stuck: list[StuckBundle] = []
    active_rc: Optional[str] = None
    active_rc_read = False
    for bundle in sorted(root.glob(f"*-{type_slug}-bundle-*.md")):
        if is_superseded_name(bundle.name) or not bundle.is_file():
            continue
        try:
            text = bundle.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        status = canonical_state(_status_of(text))
        if status in CLOSED_BUNDLE_STATUSES:
            continue
        if status == _RUNNING:
            if not active_rc_read:
                active_rc, active_rc_read = _read_active_rc(state_file), True
            found = _stuck(bundle, text, tasks, active_rc)
            if found is not None:
                stuck.append(found)
                continue
        if pending is None:
            pending = bundle
    return SlotAnswer(pending, tuple(stuck))


_USAGE = {
    "pending": "pending REQUIREMENTS_DIR TYPE_SLUG [TASKS_DIR] [RELEASE_STATE]",
    "running-items": "running-items BUNDLE",
}
_ARITY = {"pending": (2, 4), "running-items": (1, 1)}


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    command, rest = args[0], args[1:]
    arity = _ARITY.get(command)
    if arity is None or not arity[0] <= len(rest) <= arity[1]:
        print("usage: python3 -m pgai_agent_kanban.lib.bundle_items "
              + (_USAGE.get(command) or "{" + ",".join(_USAGE) + "} ..."), file=sys.stderr)
        return 2
    if command == "pending":
        answer = pending_bundle(
            rest[0], rest[1],
            rest[2] if len(rest) > 2 else None,
            rest[3] if len(rest) > 3 else None,
        )
        for found in answer.stuck:
            print("\t".join(("stuck", str(found.path), found.pm_task, found.pm_state, found.version)))
        if answer.pending is not None:
            print(f"pending\t{answer.pending}")
    else:
        for name in running_items(rest[0]):
            print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

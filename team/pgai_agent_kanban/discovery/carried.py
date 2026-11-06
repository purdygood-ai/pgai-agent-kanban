#!/usr/bin/env python3
"""
carried.py — park carried intake items on an operator question.

A release that ships a bundle without a DONE task claiming one of its items
leaves that item at ``running`` while the bundle itself closes: the item was
carried (see pgai_agent_kanban.lib.bundle_items). Bundling it again would
loop, because whatever kept it out of the last plan usually still holds. The
discovery pass therefore converts each carried item to ``needs-operator``
through the intake verb ``ops.write.set_needs_operator``, which also writes
one operator-inbox entry asking what to do with it. Answering that entry
sets the item back to ``open``, and the next discovery pass bundles it as
usual.

An item is carried when its own ``## Status`` is ``running``, at least one
bundle in the requirements directory lists it, and every bundle that lists
it is closed (``done``, ``wont-do`` or ``duplicate``; a superseded bundle
name reads as ``wont-do``). A ``running`` item no bundle lists is orphaned,
not carried, and is left alone here: the item scan re-offers it.

The inbox question names the item, the bundle it was carried from (the
newest closed bundle listing it, by version), and the reason recorded for
it: every sentence of that bundle's ``## PM Task`` status.md that names the
item's ID (``BUG-NNNN`` / ``PRIORITY-NNNN``, else its file stem). When the
bundle names no PM task, the task has no status.md, or no sentence names the
item, the question says no reason was recorded.

Commands (``python3 -m pgai_agent_kanban.discovery.carried <command> ...``):

  list DIR [REQUIREMENTS_DIR] [TASKS_DIR]
      Print one ``<path>\\t<bundle path>`` line per carried item in DIR (a
      project's bugs/ or priority/ directory), in file-name order. Reads
      only.
  convert DIR [REQUIREMENTS_DIR] [TASKS_DIR]
      Convert every carried item in DIR to ``needs-operator`` with its inbox
      question. The project is the directory DIR sits in, which must be
      ``<kanban root>/projects/<project>/``. Each conversion prints one
      ``converted <name> to needs-operator: ...`` line on stderr; nothing is
      printed on stdout. An item whose conversion is refused or fails is
      left as it was and named in one ``WARNING:`` stderr line, and the
      next pass tries again. A second pass over the same tree finds nothing
      carried and writes nothing.

REQUIREMENTS_DIR defaults to the ``requirements`` directory beside DIR and
TASKS_DIR to the ``tasks`` directory beside it. Both commands exit 0; a
wrong argument count prints the usage on stderr and exits 2. ``--help``
prints this text and exits 0.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import sys
from typing import TextIO

# Run by file path (``python3 .../carried.py``) or loaded from a file outside
# its package, this module has no parent package and the interpreter puts
# only its own directory on sys.path, so the package import below would not
# resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import bundle_items, md_fields, semver  # noqa: E402
from pgai_agent_kanban.lib.terminal_states import (  # noqa: E402
    NEEDS_OPERATOR_STATE,
    RUNNING_STATE,
    canonical_state,
)
from pgai_agent_kanban.ops.context import context_for_project  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402

__all__ = [
    "CarriedItem",
    "carried_items",
    "carried_question",
    "carried_reason",
    "convert_carried",
]

#: The longest reason quoted into a question, in characters; a longer one is
#: cut at a word boundary and ends in "...".
REASON_MAX_CHARS = 600

# The ID an item is cited by: BUG-NNNN / PRIORITY-NNNN at the start of its name.
_ITEM_ID_RE = re.compile(r"^((?:BUG|PRIORITY)-\d+)", re.IGNORECASE)
# Sentence ends: ., ! or ? followed by whitespace.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_HEADING_RE = re.compile(r"^\s*#")


@dataclasses.dataclass(frozen=True)
class CarriedItem:
    """A ``running`` item every bundle listing it has closed.

    Attributes:
        path:    The item file.
        bundle:  The newest closed bundle listing it (by the version in its
                 name, then by name).
        pm_task: That bundle's ``## PM Task`` when it names a task folder,
                 else ``""``.
        reason:  The sentences of the PM task's status.md that name the
                 item, joined; ``""`` when none was recorded.
    """

    path: pathlib.Path
    bundle: pathlib.Path
    pm_task: str
    reason: str


def _item_status(text: str) -> str:
    """The canonical first token of *text*'s ``## Status``; ``""`` if none."""
    tokens = md_fields.field_value(text, "Status").split()
    return canonical_state(tokens[0]) if tokens else ""


def _newest(bundles: list[pathlib.Path]) -> pathlib.Path:
    """The bundle with the highest version in its name; ties by name."""
    return max(bundles, key=lambda path: (semver.version_sort_key(semver.from_filename(path.name)), path.name))


def _item_id(name: str) -> str:
    """The ID an item file *name* is cited by: ``BUG-NNNN`` / ``PRIORITY-NNNN``,
    else the file stem."""
    stem = name[:-3] if name.endswith(".md") else name
    match = _ITEM_ID_RE.match(stem)
    return match.group(1) if match else stem


def _pm_task_of(bundle: pathlib.Path) -> str:
    """The bundle's ``## PM Task`` (``bundle_items.bundle_pm_task``), or
    ``""`` when the file cannot be read."""
    try:
        text = bundle.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return bundle_items.bundle_pm_task(text)


def carried_reason(status_text: str, item_name: str) -> str:
    """The sentences of *status_text* that name the item called *item_name*.

    Headings are skipped; each paragraph is split into sentences, and every
    sentence that names the item's ID (whole word, any case) is kept once,
    in order, joined by one space. The result is cut to
    :data:`REASON_MAX_CHARS`. ``""`` when no sentence names it.
    """
    item_re = re.compile(rf"(?<![A-Za-z0-9]){re.escape(_item_id(item_name))}(?![0-9])", re.IGNORECASE)
    paragraphs: list[list[str]] = [[]]
    for line in status_text.splitlines():
        if not line.strip() or _HEADING_RE.match(line):
            paragraphs.append([])
            continue
        paragraphs[-1].append(line.strip())
    found: list[str] = []
    for paragraph in paragraphs:
        for sentence in _SENTENCE_END_RE.split(" ".join(paragraph)):
            sentence = sentence.strip()
            if sentence and item_re.search(sentence) and sentence not in found:
                found.append(sentence)
    reason = " ".join(found)
    if len(reason) > REASON_MAX_CHARS:
        reason = reason[:REASON_MAX_CHARS].rsplit(" ", 1)[0] + " ..."
    return reason


def _reason_for(tasks_dir: pathlib.Path, pm_task: str, item_name: str) -> str:
    """:func:`carried_reason` over the PM task's status.md; ``""`` if unreadable."""
    if not pm_task:
        return ""
    try:
        text = (tasks_dir / pm_task / "status.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return carried_reason(text, item_name)


def carried_items(
    dir_path: pathlib.Path,
    requirements_dir: pathlib.Path | None = None,
    tasks_dir: pathlib.Path | None = None,
) -> list[CarriedItem]:
    """The carried items in *dir_path*, in file-name order. Reads only.

    *requirements_dir* defaults to ``requirements`` beside *dir_path*, and
    *tasks_dir* to ``tasks`` beside it.
    """
    dir_path = pathlib.Path(dir_path)
    if requirements_dir is None:
        requirements_dir = dir_path.parent / "requirements"
    if tasks_dir is None:
        tasks_dir = dir_path.parent / "tasks"
    if not dir_path.is_dir():
        return []
    listings = bundle_items.listing_bundles(requirements_dir)
    carried = bundle_items.carried_names_from(
        {name: [bundle.status for bundle in bundles] for name, bundles in listings.items()}
    )
    found: list[CarriedItem] = []
    for entry in sorted(dir_path.iterdir()):
        if entry.name not in carried or not entry.is_file() or not entry.name.endswith(".md"):
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _item_status(text) != RUNNING_STATE:
            continue
        bundle = _newest([listing.path for listing in listings[entry.name]])
        pm_task = _pm_task_of(bundle)
        found.append(CarriedItem(entry, bundle, pm_task, _reason_for(tasks_dir, pm_task, entry.name)))
    return found


def carried_question(item: CarriedItem) -> str:
    """The operator-inbox question for a carried *item*.

    Names the item, the bundle it was carried from and the recorded reason
    (or says none was recorded), then asks whether to bundle it again.
    """
    stem = item.path.stem
    if item.reason:
        why = f"Reason recorded by PM task {item.pm_task}: {item.reason}"
    elif item.pm_task:
        why = f"No reason was recorded: PM task {item.pm_task}'s status.md does not name {_item_id(item.path.name)}."
    else:
        why = f"No reason was recorded: bundle {item.bundle.name} names no PM task."
    return (
        f"{stem} was carried: bundle {item.bundle.name} closed with it still 'running', "
        f"so no task of that release fixed it. {why} "
        f"Should the next bundle take it again? Answering this entry sets it back to open "
        f"for bundling; to drop it instead, close it with close.sh --state wont-do or duplicate."
    )


def convert_carried(
    dir_path: pathlib.Path,
    requirements_dir: pathlib.Path | None = None,
    tasks_dir: pathlib.Path | None = None,
    *,
    err: TextIO = sys.stderr,
) -> list[CarriedItem]:
    """Convert each carried item in *dir_path* to ``needs-operator``.

    Every write goes through ``ops.write.set_needs_operator`` (the item's
    ``## Status`` and its inbox entry together). Returns the items
    converted. A conversion that is refused or fails leaves its item as it
    was and is narrated on *err*; the rest still run.
    """
    items = carried_items(dir_path, requirements_dir, tasks_dir)
    if not items:
        return []
    # Imported here: ops.write is large and only a pass with work needs it.
    from pgai_agent_kanban.ops.write import set_needs_operator

    try:
        ctx, project_name = context_for_project(pathlib.Path(dir_path), intake_dir=True)
    except OpsError:
        # CONVERT: the carried items stay at 'running' and the item scan
        # leaves them out; nothing is written and the pass carries on.
        names = ", ".join(item.path.name for item in items)
        print(
            f"WARNING: cannot convert carried item(s) {names} to {NEEDS_OPERATOR_STATE}: "
            f"{pathlib.Path(dir_path).resolve().parent} is not a <kanban root>/projects/<project>/ "
            f"directory, so there is no operator inbox to ask in",
            file=err,
        )
        return []
    converted: list[CarriedItem] = []
    for item in items:
        try:
            entry = set_needs_operator(ctx, project_name, item.path.stem, carried_question(item))
        except OpsError as exc:
            # CONVERT: this item stays at 'running', out of every bundle,
            # and the next discovery pass retries the conversion.
            print(
                f"WARNING: could not convert carried item {item.path.name} to "
                f"{NEEDS_OPERATOR_STATE}; it stays 'running' and the next pass retries: {exc}",
                file=err,
            )
            continue
        print(
            f"converted {item.path.name} to {NEEDS_OPERATOR_STATE}: carried from "
            f"{item.bundle.name}; operator question {entry.entry_id}",
            file=err,
        )
        converted.append(item)
    return converted


_USAGE = {
    "list": "list DIR [REQUIREMENTS_DIR] [TASKS_DIR]",
    "convert": "convert DIR [REQUIREMENTS_DIR] [TASKS_DIR]",
}


def main(argv: list | None = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    command, rest = args[0], args[1:]
    if command not in _USAGE or not 1 <= len(rest) <= 3:
        print(
            "usage: python3 -m pgai_agent_kanban.discovery.carried "
            + (_USAGE.get(command) or "{" + ",".join(_USAGE) + "} ..."),
            file=sys.stderr,
        )
        return 2
    dir_path = pathlib.Path(rest[0])
    requirements_dir = pathlib.Path(rest[1]) if len(rest) > 1 and rest[1] else None
    tasks_dir = pathlib.Path(rest[2]) if len(rest) > 2 and rest[2] else None
    if command == "list":
        for item in carried_items(dir_path, requirements_dir, tasks_dir):
            print(f"{item.path.resolve()}\t{item.bundle}")
    else:
        convert_carried(dir_path, requirements_dir, tasks_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

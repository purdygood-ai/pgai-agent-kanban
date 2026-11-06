"""
queue_task_id_scan.py -- find the queue entries whose task id is outside the
canonical shape.

The wake's queue scans (pending, WAITING, BLOCKED, stale-active) each act
on the lines of an agent backlog whose marker is in one set. Before a scan
reads ids, team/scripts/lib/wake_task_id_gate.sh asks this module which of
those lines name an id outside the canonical task-id shape, and refuses
each one dual-channel; the scan itself matches ids with the same shape, so
a refused id never becomes a path.

A queue entry is ``[- ][<marker>] <id>[ <description>]``: an optional list
dash, a bracket holding one marker character, then the id. The marker
class is the body of a regex character class naming the markers the
calling scan acts on (``' B'`` for the pending scan, ``'W '`` for the
WAITING scan, ``'A'`` for the stale-active scan). The BACKLOG marker is a
space, so only a class that contains a space also admits the empty ``[]``
alias; any other class must see its marker character inside the bracket.
Whitespace around the dash, the bracket and the marker is tolerated.

The canonical shape is passed in, not defined here: it is the one regex
``kanban_task_id_pattern`` (team/scripts/lib/task_ids.sh) prints, and an id
is canonical when that pattern matches from the id's first character.

Usage:
    python3 -m pgai_agent_kanban.lib.queue_task_id_scan [--] QUEUE_FILE MARKER_CLASS CANONICAL_PATTERN

prints one ``<line number>\\t<line, stripped>`` line on stdout per entry in
MARKER_CLASS whose id does not match CANONICAL_PATTERN, in file order, and
exits 0. A queue file that cannot be read (missing, a directory, no
permission) prints nothing and exits 0: there is nothing to refuse.
``--help`` prints this text and exits 0.

Public API:
    queue_entry_pattern(marker_class) -> re.Pattern
    malformed_queue_entries(lines, marker_class, canonical_pattern) -> list[tuple[int, str]]
    scan_queue_file(queue_file, marker_class, canonical_pattern) -> list[tuple[int, str]]
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections.abc import Iterable

__all__ = [
    "main",
    "malformed_queue_entries",
    "queue_entry_pattern",
    "scan_queue_file",
]


def queue_entry_pattern(marker_class: str) -> re.Pattern[str]:
    """Compile the queue-entry pattern for the markers in *marker_class*.

    Group 1 is the id after the bracket; group 2 the optional description.
    When *marker_class* contains a space (the BACKLOG marker) the marker is
    optional, so the ``[]`` alias matches too; otherwise the bracket must
    hold one of the class's characters.
    """
    if " " in marker_class:
        return re.compile(r"^\s*-?\s*\[\s*(?:[" + marker_class + r"])?\s*\]\s+(\S+)(\s+.*)?$")
    return re.compile(r"^\s*-?\s*\[\s*[" + marker_class + r"]\s*\]\s+(\S+)(\s+.*)?$")


def malformed_queue_entries(lines: Iterable[str], marker_class: str, canonical_pattern: str) -> list:
    """Return ``(line_number, stripped_line)`` for every entry in
    *marker_class* whose id *canonical_pattern* does not match.

    Line numbers count from 1 over *lines*. A line that is not a queue entry
    for the class, and an entry whose id is canonical, are left out.
    """
    entry = queue_entry_pattern(marker_class)
    canonical = re.compile(canonical_pattern)
    found = []
    for number, line in enumerate(lines, start=1):
        m = entry.match(line)
        if m and not canonical.match(m.group(1)):
            found.append((number, line.strip()))
    return found


def scan_queue_file(queue_file: pathlib.Path | str, marker_class: str, canonical_pattern: str) -> list:
    """Read *queue_file* and return :func:`malformed_queue_entries` over its lines.

    The file is read as text in the interpreter's default encoding. A file
    that cannot be opened or read (``OSError``) returns an empty list.
    """
    try:
        lines = pathlib.Path(queue_file).read_text().splitlines()
    except OSError:
        return []
    return malformed_queue_entries(lines, marker_class, canonical_pattern)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.queue_task_id_scan",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("queue_file", help="The agent backlog file to scan.")
    parser.add_argument(
        "marker_class",
        help="Body of a regex character class naming the markers to scan, e.g. ' B'.",
    )
    parser.add_argument(
        "canonical_pattern",
        help="The canonical task-id regex (kanban_task_id_pattern prints it).",
    )
    return parser


def main(argv: list | None = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = _build_parser().parse_args(argv)
    for number, line in scan_queue_file(args.queue_file, args.marker_class, args.canonical_pattern):
        print(f"{number}\t{line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

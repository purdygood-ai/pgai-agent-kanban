#!/usr/bin/env python3
"""
status_line.py — the facts the dashboard's bottom status line renders,
gathered in one process.

``team/scripts/dashboard/status-bottom.sh`` renders the bottom status line
for the global bar and for a per-project drill bar. Formatting (glyphs,
colour markup, segment order, the width budget) stays in that script; the
file-system facts behind the flag segments come from one call to this
module instead of one Python process per halt scope, per draining token and
per bug file:

* every active HALT / HALT-AFTER scope, with a draining token already put
  into words by :func:`pgai_agent_kanban.halt_after.token.describe_token`;
* how many HUMAN-APPROVE gate tasks are pending (WAITING or BACKLOG);
* how many open bugs the deferral posture holds back, per deferral scope.

The deferral posture itself (is deferral on, at which level, until which
version) is resolved before this module is called -- by the dashboard's
bash resolver, whose answer equals
:func:`pgai_agent_kanban.lib.defer_posture.resolve_defer_posture` -- and
arrives here as a LEVEL argument; this module only counts the bugs a
resolved level defers.

Severity decision
-----------------
:func:`severity_defer_decision` and :data:`SEVERITY_VOCABULARY` come from
:mod:`pgai_agent_kanban.lib.defer_posture`, the one Python reader of the
deferral posture: the Python form of ``_disc_severity_defer_decision`` in
``team/scripts/lib/discovery_defer.sh`` -- the per-bug decision discovery
applies -- which must answer exactly as it does: the vocabulary
``low < medium < high < critical`` matched case-insensitively after
trimming surrounding whitespace and then one trailing period; anything else
is ``"unparseable"`` and never deferred. A unit test drives both
implementations with the same inputs.

Usage (CLI)
-----------
::

    python3 -m pgai_agent_kanban.dashboard.status_line facts KANBAN_ROOT \\
        [--halt-scope NAME DIR]... [--defer-count NAME LEVEL [BUGS_DIR...]]...

Prints one line per fact, fields joined by the ASCII unit separator
(0x1F, the dashboard's DFS_SEP), in this order:

* ``halt<SEP>STATE<SEP>SCOPE<SEP>DESCRIBED_EVENT`` -- one per active scope
  (STATE ``halted`` or ``draining``), in ``--halt-scope`` order; a scope
  whose directory is missing or that is not halted prints nothing;
* ``approvals<SEP>N`` -- always exactly one line;
* ``defer<SEP>NAME<SEP>N`` -- one per ``--defer-count``, in order.

::

    python3 -m pgai_agent_kanban.dashboard.status_line deferred-ids BUGS_DIR LEVEL

Prints the compact ID (``BUG-NNNN``, else the file stem) of every open bug
in BUGS_DIR the LEVEL defers, one per line, in file-name order.

Exit codes: 0 always for a well-formed call (unreadable files are skipped);
2 for a malformed argument list (argparse's usage error).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Iterable, Sequence

# Run by file path (``python3 .../status_line.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.dashboard.halt_state import compute_halt_state  # noqa: E402
from pgai_agent_kanban.halt_after.token import describe_token  # noqa: E402
from pgai_agent_kanban.lib.defer_posture import (  # noqa: E402
    SEVERITY_VOCABULARY,
    severity_defer_decision,
)
from pgai_agent_kanban.task_status import read_state  # noqa: E402

__all__ = [
    "FIELD_SEP",
    "GLOBAL_SCOPE",
    "SEVERITY_VOCABULARY",
    "deferred_bug_ids",
    "halt_rows",
    "pending_approval_count",
    "severity_defer_decision",
    "status_facts",
]

#: The dashboard's field separator (DFS_SEP in dashboard/lib/defer_scope.sh):
#: not whitespace, so bash ``read`` keeps empty fields.
FIELD_SEP = "\x1f"

#: The scope word for the kanban-root sentinels.
GLOBAL_SCOPE = "GLOBAL"

_PENDING_APPROVAL_STATES = frozenset({"WAITING", "BACKLOG"})

# The "## Status" reader the dashboard's bug cards use (the same pattern as
# pgai_agent_kanban.board.read_intake_status) and the "**Severity:**"
# reader discovery uses (_disc_bug_severity), so a count here can never
# disagree with what either of them reads.
_BUG_STATUS_RE = re.compile(r"^##\s+Status\s*\n+\s*(\S+)", re.M | re.IGNORECASE)
_BUG_SEVERITY_RE = re.compile(r"\*\*Severity:\*\*\s*(.+)", re.IGNORECASE)
_COMPACT_BUG_ID_RE = re.compile(r"(BUG-[0-9]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# HALT / HALT-AFTER
# ---------------------------------------------------------------------------


def halt_rows(
    scopes: Iterable[tuple[str, "pathlib.Path | str"]],
) -> list[tuple[str, str, str]]:
    """Return ``(state, scope, described_event)`` for every active scope.

    *scopes* is ``(scope_name, directory)`` pairs in display order --
    ``GLOBAL`` and the kanban root first, then each project and its own
    root. A directory that does not exist, or whose state is ``normal``, is
    skipped. *described_event* is the draining token in words
    (``"until v1.2.3"``, ``"coder"``); it is ``""`` for a halted scope and for
    a draining scope without a token. A scope whose sentinel cannot be read
    at all reads as ``normal``, as the bash halt-scope helper treats a
    failed probe.
    """
    rows: list[tuple[str, str, str]] = []
    for scope, directory in scopes:
        root = pathlib.Path(directory)
        if not root.is_dir():
            continue
        try:
            state, event = compute_halt_state(root)
        except Exception:  # noqa: BLE001 -- an unreadable sentinel reads as normal
            continue
        if state == "halted":
            rows.append(("halted", scope, ""))
        elif state == "draining":
            described = ""
            if event:
                try:
                    described = describe_token(event)
                except Exception:  # noqa: BLE001 -- show the raw token instead
                    described = event
            rows.append(("draining", scope, described))
    return rows


# ---------------------------------------------------------------------------
# APPROVAL
# ---------------------------------------------------------------------------


def pending_approval_count(kanban_root: "pathlib.Path | str") -> int:
    """Return how many HUMAN-APPROVE gate tasks are WAITING or BACKLOG.

    Scans ``projects/*/tasks/HUMAN-APPROVE-*/status.md`` under
    *kanban_root*; the state is read by :func:`read_state`.
    """
    count = 0
    for status_file in pathlib.Path(kanban_root).glob(
        "projects/*/tasks/HUMAN-APPROVE-*/status.md"
    ):
        if status_file.is_file() and read_state(status_file) in _PENDING_APPROVAL_STATES:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Deferred bugs
# ---------------------------------------------------------------------------


def _read_text(path: pathlib.Path) -> "str | None":
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def deferred_bug_ids(bugs_dir: "pathlib.Path | str", level: str) -> list[str]:
    """Return the compact IDs of the open bugs in *bugs_dir* *level* defers.

    A bug is a candidate when its ``## Status`` is ``open``; README files
    are skipped. Its ``**Severity:**`` value is judged by
    :func:`severity_defer_decision`. The ID is the file stem's leading
    ``BUG-<digits>`` (the form every bug card displays), else the whole
    stem. A missing directory yields ``[]``.
    """
    directory = pathlib.Path(bugs_dir)
    if not directory.is_dir():
        return []
    ids: list[str] = []
    for bug_file in sorted(directory.glob("*.md")):
        if not bug_file.is_file() or bug_file.name.upper().startswith("README"):
            continue
        text = _read_text(bug_file)
        if text is None:
            continue
        status = _BUG_STATUS_RE.search(text)
        if not status or status.group(1).strip().lower() != "open":
            continue
        severity = _BUG_SEVERITY_RE.search(text)
        severity_value = severity.group(1).strip() if severity else ""
        if severity_defer_decision(severity_value, level) != "deferred":
            continue
        compact = _COMPACT_BUG_ID_RE.match(bug_file.stem)
        ids.append(compact.group(1) if compact else bug_file.stem)
    return ids


# ---------------------------------------------------------------------------
# All facts, one call
# ---------------------------------------------------------------------------


def status_facts(
    kanban_root: "pathlib.Path | str",
    halt_scopes: Sequence[tuple[str, "pathlib.Path | str"]],
    defer_counts: Sequence[tuple[str, str, Sequence["pathlib.Path | str"]]],
) -> list[tuple[str, ...]]:
    """Return every fact the status line needs, as field tuples.

    ``("halt", state, scope, described_event)`` per active halt scope, then
    ``("approvals", n)``, then ``("defer", name, n)`` per *defer_counts*
    entry ``(name, level, bugs_dirs)`` -- n summed over its bug directories.
    """
    facts: list[tuple[str, ...]] = [
        ("halt", state, scope, described) for state, scope, described in halt_rows(halt_scopes)
    ]
    facts.append(("approvals", str(pending_approval_count(kanban_root))))
    for name, level, bugs_dirs in defer_counts:
        total = sum(len(deferred_bug_ids(bugs_dir, level)) for bugs_dir in bugs_dirs)
        facts.append(("defer", name, str(total)))
    return facts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="status_line.py",
        description=(
            "Gather the facts the dashboard's bottom status line renders "
            "(halt scopes, pending approvals, deferred-bug counts) in one call."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    facts = sub.add_parser(
        "facts",
        help="print halt, approvals and defer facts, one per line",
        description=(
            "Print one line per fact, fields joined by the 0x1F unit "
            "separator: halt<SEP>STATE<SEP>SCOPE<SEP>DESCRIBED_EVENT per "
            "active scope, approvals<SEP>N, then defer<SEP>NAME<SEP>N per "
            "--defer-count."
        ),
    )
    facts.add_argument("kanban_root", metavar="KANBAN_ROOT",
                       help="the kanban root whose projects/*/tasks are scanned for approvals")
    facts.add_argument(
        "--halt-scope", nargs=2, action="append", default=[], metavar=("NAME", "DIR"),
        help="a halt scope to check, in display order: its name (GLOBAL or a "
             "project) and the directory holding its HALT / HALT-AFTER files",
    )
    facts.add_argument(
        "--defer-count", nargs="+", action="append", default=[],
        metavar="NAME LEVEL BUGS_DIR",
        help="count open bugs LEVEL (low, medium or high) defers across zero "
             "or more BUGS_DIR directories, reported under NAME",
    )

    ids = sub.add_parser(
        "deferred-ids",
        help="print the IDs of the open bugs a level defers",
        description="Print the compact ID of every open bug in BUGS_DIR that LEVEL defers.",
    )
    ids.add_argument("bugs_dir", metavar="BUGS_DIR", help="a project's bugs/ directory")
    ids.add_argument("level", metavar="LEVEL", help="the resolved deferral level: low, medium or high")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    """Run the status-line CLI: print the bottom status line's facts.

    ``facts KANBAN_ROOT [--halt-scope NAME DIR ...] [--defer-count NAME LEVEL
    [BUGS_DIR ...] ...]`` prints one 0x1F-separated fact per line;
    ``deferred-ids BUGS_DIR LEVEL`` prints one deferred open bug ID per line.
    Exit codes: 0 printed; 2 usage error (argparse, including a
    ``--defer-count`` with fewer than NAME and LEVEL).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "deferred-ids":
        for bug_id in deferred_bug_ids(args.bugs_dir, args.level):
            print(bug_id)
        return 0

    defer_counts = []
    for entry in args.defer_count:
        if len(entry) < 2:
            parser.error("--defer-count needs NAME and LEVEL, then zero or more BUGS_DIR")
        defer_counts.append((entry[0], entry[1], entry[2:]))
    halt_scopes = [(name, directory) for name, directory in args.halt_scope]
    for fact in status_facts(args.kanban_root, halt_scopes, defer_counts):
        print(FIELD_SEP.join(fact))
    return 0


if __name__ == "__main__":
    sys.exit(main())

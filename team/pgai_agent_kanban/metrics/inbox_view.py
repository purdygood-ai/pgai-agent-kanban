"""
inbox_view.py — the operator-inbox figures the status surfaces print.

Three surfaces show a project's operator inbox
(``projects/<name>/operator-inbox.md``), and all three read it through this
module — :func:`read_summary` (``overwatch.inbox.inbox_summary``) or
:func:`read_open_entries` (``overwatch.inbox.list_open_newest_first``) — so
the inbox module stays the file's only reader:

  * the dashboard attention pane's OPERATOR INBOX block
    (``dashboard.scan_attention.scan_inbox_section``): each project's open
    count and one line per open entry, newest first (:func:`read_open_entries`);
  * ``metrics-report.sh --inbox``: one line per project,
    ``  <project>: <N> open`` (:func:`count_lines`);
  * ``metrics-report.sh --day <YYYY-MM-DD>``: the per-day rollup JSON with
    one more top-level key, ``"inbox_open"`` — the project's open count at
    the time the report is printed (:func:`day_report_text`). The key is
    added when printing, never stored in the rollup file, so the rollup
    stays a pure function of the task folders.

An inbox that cannot be read (bad UTF-8, a permissions failure) is never an
error on these surfaces: the count line reads ``inbox unreadable``, the
attention pane says the same, and ``"inbox_open"`` is ``null``. A missing
inbox file is an empty inbox: ``0 open``.

Usage (CLI — what ``metrics-report.sh`` runs):
    python3 -m pgai_agent_kanban.metrics.inbox_view counts --kanban-root DIR PROJECT [PROJECT ...]
    python3 -m pgai_agent_kanban.metrics.inbox_view day --kanban-root DIR --project NAME DAY_FILE

Usage (import — what the attention pane calls):
    from pgai_agent_kanban.metrics.inbox_view import read_open_entries
    entries, problem = read_open_entries(kanban_root, "pgai-agent-kanban")
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections.abc import Iterable

# Run by file path, this module has no parent package and only its own
# directory is on sys.path. Put this file's own package root -- the
# directory holding the pgai_agent_kanban/ this file sits in -- first, so
# the imports below resolve from the same tree, never another. Imported as
# a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.overwatch.inbox import (  # noqa: E402
    InboxEntry,
    InboxError,
    InboxNotFound,
    InboxSummary,
    inbox_summary,
    list_open_newest_first,
)

__all__ = [
    "DAY_KEY",
    "PROBLEM_NO_PROJECT",
    "PROBLEM_UNREADABLE",
    "count_line",
    "count_lines",
    "day_report_text",
    "open_count",
    "read_open_entries",
    "read_summary",
]

#: The key ``metrics-report.sh --day`` adds to the per-day rollup JSON.
DAY_KEY = "inbox_open"

#: What a surface prints in place of a count it could not read.
PROBLEM_UNREADABLE = "inbox unreadable"
PROBLEM_NO_PROJECT = "no project directory"

_ENTRY_INDENT = "  "


def read_summary(kanban_root: object, project: str) -> tuple[InboxSummary | None, str]:
    """Return *project*'s inbox summary, or why there is none.

    Returns ``(summary, "")`` when the inbox reads (a missing file is an
    empty inbox); otherwise ``(None, problem)`` — :data:`PROBLEM_NO_PROJECT`
    when the project directory does not exist, :data:`PROBLEM_UNREADABLE`
    for any other refusal or I/O failure. Never raises.
    """
    try:
        return inbox_summary(OpsContext(kanban_root=pathlib.Path(str(kanban_root))), project), ""
    except InboxNotFound:
        return None, PROBLEM_NO_PROJECT
    except (InboxError, OSError, ValueError):
        return None, PROBLEM_UNREADABLE


def read_open_entries(kanban_root: object, project: str) -> tuple[tuple[InboxEntry, ...] | None, str]:
    """Return *project*'s open inbox entries, newest first, or why there are none.

    Returns ``(entries, "")`` when the inbox reads (a missing file gives
    ``()``); otherwise ``(None, problem)`` with the same problems
    :func:`read_summary` returns. Never raises.
    """
    try:
        context = OpsContext(kanban_root=pathlib.Path(str(kanban_root)))
        return list_open_newest_first(context, project), ""
    except InboxNotFound:
        return None, PROBLEM_NO_PROJECT
    except (InboxError, OSError, ValueError):
        return None, PROBLEM_UNREADABLE


def open_count(kanban_root: object, project: str) -> int | None:
    """Return *project*'s open-entry count, or ``None`` when it cannot be read."""
    summary, _ = read_summary(kanban_root, project)
    return summary.open_count if summary is not None else None


def count_line(kanban_root: object, project: str) -> str:
    """Return ``  <project>: <N> open``, or ``  <project>: <problem>``."""
    summary, problem = read_summary(kanban_root, project)
    text = f"{summary.open_count} open" if summary is not None else problem
    return f"{_ENTRY_INDENT}{project}: {text}"


def count_lines(kanban_root: object, projects: Iterable[str]) -> list[str]:
    """Return one :func:`count_line` per project, in the order given.

    No project gives the single line ``  (no projects registered)``.
    """
    lines = [count_line(kanban_root, project) for project in projects]
    return lines or [f"{_ENTRY_INDENT}(no projects registered)"]


def day_report_text(day_text: str, inbox_open: int | None) -> str | None:
    """Return the per-day rollup *day_text* with :data:`DAY_KEY` set to *inbox_open*.

    Written as the aggregator writes a rollup (two-space indent, sorted keys,
    one trailing newline), so the output differs from the stored file only
    by the added key. Returns ``None`` when *day_text* is not a JSON object;
    the caller then prints the file as stored.
    """
    try:
        document = json.loads(day_text)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    document[DAY_KEY] = inbox_open
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _print_day(kanban_root: str, project: str, day_file: str) -> int:
    path = pathlib.Path(day_file)
    try:
        day_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read the day rollup {path}: {exc}", file=sys.stderr)
        print(f"  Check: ls -l {path}", file=sys.stderr)
        return 2
    text = day_report_text(day_text, open_count(kanban_root, project))
    if text is None:
        print(
            f"[metrics-report] WARNING: {path} is not a JSON object; printed as stored, " f'without "{DAY_KEY}".',
            file=sys.stderr,
        )
        text = day_text
    sys.stdout.write(text)
    return 0


def main(argv: list | None = None) -> int:
    """Run the inbox view CLI.

    ``counts`` prints one ``  <project>: <N> open`` line per named project.
    ``day`` prints a per-day rollup file with ``"inbox_open"`` added (the
    file as stored, and a warning on stderr, when it is not a JSON object).
    Exit codes: 0 printed; 2 the day file could not be read, or a usage
    error.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.metrics.inbox_view",
        description=(
            "Print operator-inbox figures for metrics-report.sh: each project's open "
            "count ('counts'), or a per-day rollup carrying the project's open count "
            "('day')."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    counts = subparsers.add_parser(
        "counts",
        help="print each project's open inbox count",
        description="Print one '  <project>: <N> open' line per project.",
    )
    counts.add_argument("projects", nargs="*", metavar="PROJECT", help="project names, e.g. pgai-agent-kanban")
    day = subparsers.add_parser(
        "day",
        help="print a per-day rollup with the project's open inbox count added",
        description=f"Print DAY_FILE's JSON with \"{DAY_KEY}\" set to the project's open inbox count.",
    )
    day.add_argument("day_file", metavar="DAY_FILE", help="the rollup, projects/<name>/metrics/day/<YYYY-MM-DD>.json")
    day.add_argument("--project", required=True, help="project name, e.g. pgai-agent-kanban")
    for sub in (counts, day):
        sub.add_argument(
            "--kanban-root",
            dest="kanban_root",
            required=True,
            help="kanban root holding projects/<name>/operator-inbox.md",
        )
    args = parser.parse_args(argv)
    if args.command == "counts":
        print("\n".join(count_lines(args.kanban_root, args.projects)))
        return 0
    return _print_day(args.kanban_root, args.project, args.day_file)


if __name__ == "__main__":
    sys.exit(main())

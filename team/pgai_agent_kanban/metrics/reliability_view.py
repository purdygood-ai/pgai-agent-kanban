"""
reliability_view.py — the text rendering of a project's reliability
streaks, shared by the rc-metrics pane and ``metrics-report.sh
--reliability`` so the two never disagree.

Both surfaces read only ``<project>/metrics/reliability.json`` (written by
pgai_agent_kanban.metrics.reliability; its module docstring carries the
schema) and render it through :func:`project_entry_lines`:

    pgai-agent-kanban
      Hands-off: 12 RCs / 240h   since 2026-10-08 03:31 (CM release HALT)
      Clean:      2 RCs /   7h   since 2026-10-17 19:55 (Needs Human: provider refusal)

(shown here without the pane's indent). The project name line comes first,
then one line per streak:

  * ``rcs`` and whole ``hours`` (rounded down) are right-aligned within the
    project's two lines, so ``RCs`` and ``h`` line up. ``hours`` is the time
    from ``since`` to the moment of rendering (``now``, default the current
    time), not the figure stored at the file's last refresh, so it keeps
    growing while a quiet project's file is not rewritten. A streak with no
    ``since`` (an empty ledger, or a block still open) shows its stored
    hours.
  * ``since`` is the streak's anchor in UTC: the last block's resolution as
    ``YYYY-MM-DD HH:MM``. For "no block on record" it is the day of the
    first ledger record, ``YYYY-MM-DD``, with ``(no block on record)`` as
    the cause; an empty ledger has no anchor and shows ``(no block on
    record)`` alone.
  * A block still open reads ``0 RCs / 0h   open since <start> (...)``.
  * The parenthesised cause is :func:`cause_text` of the last block. A line
    is at most :data:`PANE_WIDTH` columns: a cause that does not fit is
    shown as its short form (:func:`cause_candidates` — ``Needs Human``,
    ``BLOCKED``, ``HALT``), and only one that still does not fit is cut,
    ending in an ellipsis. ``--format json`` carries the full detail.

A missing ``reliability.json`` renders ``(no reliability data yet)`` under
the project name; one that does not parse, or lacks a field the lines
need, renders ``(reliability.json unreadable)``. Rendering never raises.

Usage (CLI — what ``metrics-report.sh --reliability`` runs):
    python3 -m pgai_agent_kanban.metrics.reliability_view text --kanban-root DIR [--now ISO] PROJECT [PROJECT ...]
    python3 -m pgai_agent_kanban.metrics.reliability_view json --kanban-root DIR PROJECT

Usage (import — what the pane renderer calls):
    from pgai_agent_kanban.metrics.reliability_view import reliability_section_lines
    for line in reliability_section_lines([{"name": "p", "reliability_file": path}]):
        print(line)
"""

from __future__ import annotations

import datetime
import json
import math
import os
import pathlib
import sys
from typing import Any
from collections.abc import Iterable, Iterator

# Run by file path, this module has no parent package and only its own
# directory is on sys.path. Put this file's own package root -- the
# directory holding the pgai_agent_kanban/ this file sits in -- first, so
# the imports below resolve from the same tree, never another. Imported as
# a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.timeutil import try_parse_iso  # noqa: E402
from pgai_agent_kanban.metrics.reliability import (  # noqa: E402
    STATUS_NO_BLOCK,
    STATUS_OPEN,
    STATUS_RESOLVED,
    hours_between,
    reliability_path_for_project,
)

__all__ = [
    "PANE_WIDTH",
    "SECTION_TITLE",
    "cause_candidates",
    "cause_text",
    "load_reliability",
    "project_entry_lines",
    "reliability_section_lines",
    "streak_lines",
]

#: The widest a streak line may be: the rc-metrics left pane's width.
PANE_WIDTH = 70
#: The pane section's title, rendered ``=== Reliability ===``.
SECTION_TITLE = "Reliability"

PLACEHOLDER_MISSING = "(no reliability data yet)"
PLACEHOLDER_UNREADABLE = "(reliability.json unreadable)"
NO_BLOCK_TEXT = "no block on record"

#: (label, document key) for the two streak lines, in display order.
_STREAKS = (("Hands-off:", "hands_off"), ("Clean:", "clean"))
_LABEL_WIDTH = max(len(label) for label, _ in _STREAKS) + 1
_ENTRY_INDENT = "  "
_LINE_INDENT = "    "
_ELLIPSIS = "…"
_KNOWN_STATUSES = frozenset({STATUS_RESOLVED, STATUS_OPEN, STATUS_NO_BLOCK})

#: Reason codes whose words read better than the code with "_" as spaces.
#: Every other code is shown that way (``gate_dirty_worktree`` ->
#: ``gate dirty worktree``); an empty phrase adds nothing after the label.
_REASON_PHRASES = {
    "needs_human": "",
    "agent_unclassified": "",
    "agent_operator_data_action": "operator data action",
    "per_task_timeout": "task timeout",
    "transient_api_error": "transient API error",
    "provider_refusal": "provider refusal",
}

_TASK_SOURCE_PREFIX = "task:"
_HALT_SOURCE_PREFIX = "halt:"


# ---------------------------------------------------------------------------
# Cause text
# ---------------------------------------------------------------------------


def _reason_phrase(reason_code: str | None) -> str:
    code = (reason_code or "").strip().lower()
    if code in _REASON_PHRASES:
        return _REASON_PHRASES[code]
    return " ".join(code.replace("-", " ").replace("_", " ").replace(".", " ").split())


def _halt_causes(block: dict) -> list[str]:
    code = str(block.get("reason_code") or "").strip().lower()
    if str(block.get("cause") or "").strip().lower() == "operator":
        return ["operator HALT"]
    if code.startswith(("cm-", "cm_", "cm.")) or any(
        token in code for token in ("ship-policy", "ship_policy", "ship.policy")
    ):
        return ["CM release HALT", "CM HALT"]
    if code.startswith("overwatch"):
        return ["OVERWATCH HALT"]
    if "zero" in code and "progress" in code:
        return ["zero-progress HALT", "HALT"]
    if "substrate" in code:
        return ["substrate HALT"]
    if code == "halt_global" or (not code and block.get("source") == "halt:global"):
        return ["global HALT"]
    if code in ("", "halt_project"):
        return ["chain HALT"]
    return [f"HALT: {_reason_phrase(code)}", "HALT"]


def cause_candidates(block: dict | None) -> list[str]:
    """The cause for a ``last_block`` object of reliability.json, most
    detailed first, then its short form when it has one; the renderer
    shows the first that fits the line.

    A task block reads ``Needs Human`` when a human was involved — its
    class is "human", it was escalated, or it was raised by a
    ``--needs-human yes`` write — else ``BLOCKED``; the detailed form adds
    ``: `` and the reason in words (``Needs Human: provider refusal``),
    or the task's role (``BLOCKED: CODER task``) when the reason says
    nothing more. A HALT reads by who raised it: ``CM release HALT``,
    ``OVERWATCH HALT``, ``zero-progress HALT``, ``substrate HALT``,
    ``global HALT``, ``operator HALT``, else ``chain HALT``. Any other
    source shows itself, with the reason when there is one.
    """
    if not isinstance(block, dict):
        return [""]
    source = str(block.get("source") or "").strip()
    reason_code = block.get("reason_code")
    if source.startswith(_HALT_SOURCE_PREFIX):
        return _halt_causes(block)
    phrase = _reason_phrase(reason_code if isinstance(reason_code, str) else None)
    if source.startswith(_TASK_SOURCE_PREFIX):
        human = (
            str(block.get("class") or "").strip().lower() == "human"
            or bool(block.get("escalated"))
            or str(reason_code or "").strip().lower() == "needs_human"
        )
        label = "Needs Human" if human else "BLOCKED"
        if not phrase:
            task = str(block.get("task") or source[len(_TASK_SOURCE_PREFIX) :]).strip()
            role = task.split("-", 1)[0] if task else ""
            phrase = f"{role} task" if role else ""
        return [f"{label}: {phrase}", label] if phrase else [label]
    if not source:
        return [phrase or "block"]
    return [f"{source}: {phrase}", source] if phrase else [source]


def cause_text(block: dict | None) -> str:
    """The most detailed cause for a ``last_block`` object (see
    :func:`cause_candidates`)."""
    return cause_candidates(block)[0]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _whole(value: Any) -> int | None:
    """*value* as a non-negative whole number (rounded down), or None when
    it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return max(0, int(math.floor(value)))


def _minute_text(value: Any) -> str | None:
    """An ISO-8601 timestamp as UTC ``YYYY-MM-DD HH:MM``; None when it does
    not parse."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = try_parse_iso(value.strip())
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.UTC)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _now(now: datetime.datetime | None) -> datetime.datetime:
    """*now* as an aware UTC datetime; the current time when None."""
    if now is None:
        return datetime.datetime.now(datetime.UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=datetime.UTC)
    return now


def _elapsed_hours(since: Any, now: datetime.datetime | None) -> int | None:
    """Whole hours from the ISO-8601 *since* to *now*; None when *since*
    does not parse."""
    if not isinstance(since, str) or not since.strip():
        return None
    parsed = try_parse_iso(since.strip())
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return _whole(hours_between(parsed, _now(now)))


def _streak_parts(
    streak: Any,
    now: datetime.datetime | None = None,
) -> tuple[int, int, str, list[str]] | None:
    """``(rcs, hours, prefix, causes)`` for one streak object, or None when
    a field the line needs is absent or of the wrong shape. *hours* is the
    time from the streak's ``since`` to *now* (default the current time);
    *prefix* is the text after the hours column up to the parenthesised
    cause (empty for an empty ledger); *causes* are the cause's forms, most
    detailed first."""
    if not isinstance(streak, dict):
        return None
    status = streak.get("status")
    rcs = _whole(streak.get("rcs"))
    hours = _whole(streak.get("hours"))
    if status not in _KNOWN_STATUSES or rcs is None or hours is None:
        return None
    if status == STATUS_NO_BLOCK:
        since = streak.get("since")
        if since is None:
            return rcs, hours, "", [NO_BLOCK_TEXT]
        since_text = _minute_text(since)
        if since_text is None:
            return None
        # The ledger's first record, to the day: what counts is how long the
        # project has gone without a block, not the minute the ledger began.
        return rcs, _elapsed_hours(since, now), f"since {since_text.split(' ', 1)[0]}", [NO_BLOCK_TEXT]
    block = streak.get("last_block")
    if not isinstance(block, dict):
        return None
    if status == STATUS_OPEN:
        start_text = _minute_text(block.get("time"))
        if start_text is None:
            return None
        return rcs, hours, f"open since {start_text}", cause_candidates(block)
    since_text = _minute_text(streak.get("since"))
    if since_text is None:
        return None
    return rcs, _elapsed_hours(streak.get("since"), now), f"since {since_text}", cause_candidates(block)


def load_reliability(path: str | None) -> tuple[dict | None, str]:
    """Read one reliability.json.

    Returns ``(document, "")`` when the file holds a JSON object both
    streak lines can be rendered from; otherwise ``(None, placeholder)``
    — :data:`PLACEHOLDER_MISSING` for an empty path or an absent file,
    :data:`PLACEHOLDER_UNREADABLE` for anything else. Never raises.
    """
    if not path or not os.path.isfile(path):
        return None, PLACEHOLDER_MISSING
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None, PLACEHOLDER_UNREADABLE
    if not isinstance(document, dict):
        return None, PLACEHOLDER_UNREADABLE
    if any(_streak_parts(document.get(key)) is None for _, key in _STREAKS):
        return None, PLACEHOLDER_UNREADABLE
    return document, ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fit(head: str, prefix: str, causes: list[str], width: int) -> str:
    """``head``, *prefix* and the parenthesised cause, at most *width*
    columns: the first of *causes* that fits; else the last one, cut to
    fit and ending in an ellipsis; else the whole line cut."""
    lead = f"{head}{prefix} " if prefix else head
    for cause in causes:
        line = f"{lead}({cause})"
        if len(line) <= width:
            return line
    room = width - len(lead) - len("()")
    if room >= 2:
        return f"{lead}({causes[-1][:room - 1]}{_ELLIPSIS})"
    line = f"{lead}({causes[-1]})"
    return line[: max(0, width - 1)] + _ELLIPSIS


def streak_lines(
    document: dict,
    width: int = PANE_WIDTH,
    now: datetime.datetime | None = None,
) -> list[str]:
    """The two streak lines (Hands-off, then Clean) for a document
    :func:`load_reliability` accepted, each indented four spaces and at
    most *width* columns, hours counted to *now* (default the current
    time). Raises ValueError for a document it did not accept."""
    parts = []
    for label, key in _STREAKS:
        part = _streak_parts(document.get(key), now)
        if part is None:
            raise ValueError(f"reliability document has no renderable {key!r} streak")
        parts.append((label, *part))
    rcs_width = max(len(str(part[1])) for part in parts)
    hours_width = max(len(str(part[2])) for part in parts)
    lines = []
    for label, rcs, hours, prefix, causes in parts:
        head = f"{_LINE_INDENT}{label:<{_LABEL_WIDTH}}{rcs:>{rcs_width}} RCs / " f"{hours:>{hours_width}}h   "
        lines.append(_fit(head, prefix, causes, width))
    return lines


def project_entry_lines(
    name: str,
    path: str | None,
    *,
    bold: str = "",
    dim: str = "",
    reset: str = "",
    width: int = PANE_WIDTH,
    now: datetime.datetime | None = None,
) -> list[str]:
    """One project's entry: the name line (two-space indent, in *bold*),
    then its two streak lines (hours to *now*, default the current time),
    or one placeholder line (in *dim*) when *path* is missing or
    unreadable. The escapes are empty strings when colour is off."""
    lines = [f"{_ENTRY_INDENT}{bold}{name}{reset}"]
    document, placeholder = load_reliability(path)
    if document is None:
        lines.append(f"{_LINE_INDENT}{dim}{placeholder}{reset}")
    else:
        lines.extend(streak_lines(document, width, now))
    return lines


def reliability_section_lines(
    projects: Iterable[dict],
    *,
    reset: str = "",
    bold: str = "",
    dim: str = "",
    cyan: str = "",
    width: int = PANE_WIDTH,
    now: datetime.datetime | None = None,
) -> Iterator[str]:
    """The pane's ``=== Reliability ===`` section: the header, a blank
    line, then each project's entry followed by a blank line. *projects*
    are the pane's descriptors; each one's ``reliability_file`` is the path
    read (absent or empty: the missing placeholder). Hours are counted to
    *now* (default the current time). No projects shows ``(no projects
    registered)``, as the pane's other sections do."""
    projects = list(projects)
    yield f"{cyan}{bold}=== {SECTION_TITLE} ==={reset}"
    yield ""
    if not projects:
        yield f"{_ENTRY_INDENT}{dim}(no projects registered){reset}"
        return
    for project in projects:
        yield from project_entry_lines(
            str(project.get("name", "unknown")),
            project.get("reliability_file") or "",
            bold=bold,
            dim=dim,
            reset=reset,
            width=width,
            now=now,
        )
        yield ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_text(kanban_root: str, projects: list[str], now: datetime.datetime | None = None) -> int:
    if not projects:
        print(f"{_ENTRY_INDENT}(no projects registered)")
        return 0
    entries = [project_entry_lines(name, reliability_path_for_project(kanban_root, name), now=now) for name in projects]
    print("\n\n".join("\n".join(entry) for entry in entries))
    return 0


def _print_json(kanban_root: str, project: str) -> int:
    path = reliability_path_for_project(kanban_root, project)
    document, placeholder = load_reliability(path)
    if document is None:
        what = "no reliability.json" if placeholder == PLACEHOLDER_MISSING else "unreadable reliability.json"
        print(f"ERROR: {what} for project '{project}'", file=sys.stderr)
        print(f"  Expected: {path}", file=sys.stderr)
        print(
            f"  Run: python3 -m pgai_agent_kanban.metrics.reliability refresh --project {project}"
            f" --kanban-root {kanban_root}",
            file=sys.stderr,
        )
        return 2
    with open(path, encoding="utf-8") as handle:
        sys.stdout.write(handle.read())
    return 0


def _iso_time(value: str) -> datetime.datetime:
    """argparse type for ``--now``: an ISO-8601 time, a usage error otherwise."""
    import argparse

    parsed = try_parse_iso(value.strip())
    if parsed is None:
        raise argparse.ArgumentTypeError(f"not an ISO-8601 time: {value!r}")
    return _now(parsed)


def main(argv: list | None = None) -> int:
    """Run the reliability view CLI.

    ``text`` prints each named project's entry (name line and two streak
    lines, exactly as the pane prints them, without colour), entries
    separated by a blank line; no project prints ``(no projects
    registered)``. ``json`` prints one project's reliability.json as
    stored. Exit codes: 0 printed; 2 ``json`` found no readable file
    (the cause and the refresh command on stderr), or a usage error.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.metrics.reliability_view",
        description=(
            "Print a project's reliability streaks (Hands-off and Clean) from "
            "its metrics/reliability.json, in the rc-metrics pane's text shape "
            "('text') or as the stored JSON ('json')."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    text = subparsers.add_parser(
        "text",
        help="print each project's name line and two streak lines",
        description="Print each project's name line and two streak lines, as the pane does.",
    )
    text.add_argument("projects", nargs="*", metavar="PROJECT", help="project names, e.g. pgai-agent-kanban")
    text.add_argument(
        "--now",
        type=_iso_time,
        default=None,
        metavar="ISO",
        help="count each streak's hours to this time, e.g. 2026-10-18T12:00:00+00:00 (default: now)",
    )
    as_json = subparsers.add_parser(
        "json",
        help="print one project's reliability.json",
        description="Print one project's metrics/reliability.json as stored.",
    )
    as_json.add_argument("project", metavar="PROJECT", help="project name, e.g. pgai-agent-kanban")
    for sub in (text, as_json):
        sub.add_argument(
            "--kanban-root",
            dest="kanban_root",
            required=True,
            help="kanban root holding projects/<name>/metrics/reliability.json",
        )
    args = parser.parse_args(argv)
    if args.command == "text":
        return _print_text(args.kanban_root, list(args.projects), args.now)
    return _print_json(args.kanban_root, args.project)


if __name__ == "__main__":
    sys.exit(main())

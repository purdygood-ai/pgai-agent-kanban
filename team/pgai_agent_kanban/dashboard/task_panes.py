#!/usr/bin/env python3
"""
task_panes.py — task-folder scans behind three dashboard panes.

Each renderer scans one project's ``tasks/`` directory, reads every task's
``status.md`` sections through ``pgai_agent_kanban.task_status``, and yields
the pane's lines.  The bash pane passes the ANSI escapes it computed with
``ansi_code()`` (empty strings when colour is off), so the escape table is
never copied here.

  * ``status_window_blocked_lines`` — the ATTENTION list in
    ``team/scripts/dashboard/show-status-window.sh``: each BLOCKED task's ID
    and a one-line reason truncated to 60 characters.
  * ``attention_blocked_lines`` — ``team/scripts/dashboard/show-attention.sh``:
    each BLOCKED task with its blocked-since time, up to three reason lines
    and up to three recommended-next-step lines.
  * ``document_pipeline_lines`` — the Document Pipeline section of
    ``team/scripts/dashboard/show-document-drill.sh``: per-role
    done/total counts for the writer, tester and cm roles.

The two blocked-task scans skip the ``archive``, ``queues`` and ``plans``
folders, any entry that is not a directory, and any folder without a
readable ``status.md``.  In all three renderers -- the two blocked-task
scans and the document-pipeline counter -- a missing tasks directory renders
the pane's empty state, and so does an empty or blank tasks path: it names
no directory, so nothing is read (never the current directory, which
``pathlib.Path("")`` would name).

The output is a contract: the panes print these lines verbatim.

Usage (CLI):
    python3 -m pgai_agent_kanban.dashboard.task_panes status-window-blocked \\
        <tasks_dir> [--reset ESC] [--red ESC] [--dim ESC] [--bold ESC]

    python3 -m pgai_agent_kanban.dashboard.task_panes attention-blocked \\
        <tasks_dir> [--use-color true|false] [--reset ESC] [--red ESC]
        [--bold ESC] [--dim ESC] [--cyan ESC] [--yellow ESC]

    python3 -m pgai_agent_kanban.dashboard.task_panes document-pipeline \\
        <tasks_dir> [--use-color true|false] [--label ESC] [--done ESC]
        [--working ESC] [--blocked ESC] [--dim ESC] [--reset ESC]

    Every escape defaults to "" (no colour).  ``--use-color`` is compared
    case-insensitively with "true" and defaults to false.  Each verb prints
    its lines and exits 0; ``--help`` prints usage and exits 0.

Public API:
    status_window_blocked_lines(tasks_root, reset, red, dim, bold)
    attention_blocked_lines(tasks_root, use_color, reset, red, bold, dim,
                            cyan, yellow, now=None)
    document_pipeline_lines(tasks_dir, use_color, label, done, working,
                            blocked, dim, reset)
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from datetime import datetime
from typing import Iterator, Optional

# Run by file path (``python3 .../task_panes.py``) or loaded from a file
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

from pgai_agent_kanban.task_status import section_lines, section_value  # noqa: E402

__all__ = [
    "attention_blocked_lines",
    "document_pipeline_lines",
    "main",
    "status_window_blocked_lines",
]

# Folders under tasks/ that hold no task.
_NON_TASK_DIRS = frozenset({"archive", "queues", "plans"})

# The roles the document pipeline shows, in display order. PM and CODER plan
# and build; they do not author document content.
DOC_PIPELINE_ROLES = ["writer", "tester", "cm"]

_PIPELINE_STATES = {"WORKING", "BLOCKED", "WAITING", "DONE", "WONT-DO", "BACKLOG"}

# Visible width the document-pipeline line pads its percentage out to.
_PIPELINE_PANE_WIDTH = 36


def _first_line(text: str, heading: str) -> str:
    """Return the first non-blank line of the ``## heading`` section, stripped."""
    lines = section_lines(text, heading, max_lines=1)
    return lines[0] if lines else ""


def _blocked_status_files(tasks_path: str) -> Iterator[tuple[str, pathlib.Path, str]]:
    """Yield ``(task_id, status_file, status_text)`` for each BLOCKED task.

    Task folders are visited in sorted name order.  The State is the first
    line of ``## State``, compared case-insensitively.  An empty or blank
    *tasks_path* yields nothing.
    """
    if not tasks_path.strip():
        return
    tasks_root = pathlib.Path(tasks_path)
    if not tasks_root.is_dir():
        return
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if task_id in _NON_TASK_DIRS:
            continue
        status_file = task_dir / "status.md"
        if not status_file.is_file():
            continue
        try:
            status_text = status_file.read_text(errors="replace")
        except OSError:
            continue
        if _first_line(status_text, "State").upper() != "BLOCKED":
            continue
        yield task_id, status_file, status_text


def status_window_blocked_lines(
    tasks_root: str,
    reset: str = "",
    red: str = "",
    dim: str = "",
    bold: str = "",
) -> Iterator[str]:
    """Yield the status window's ATTENTION list, one line per item.

    Each BLOCKED task renders three lines: ``"  <red><bold>ID<reset>"``,
    ``"  <dim>Reason: REASON<reset>"`` and a blank line.  REASON is the first
    line of ``## Blocked Reason``, else of ``## Blockers``, else
    ``"(see status.md)"``; past 60 characters it is cut to 57 plus ``"..."``.
    With no BLOCKED task the one line is ``"  <dim>(none)<reset>"``.

    Args:
        tasks_root: The project's tasks directory.
        reset, red, dim, bold: ANSI escapes, or "" for no colour.
    """
    found_any = False
    for task_id, _status_file, status_text in _blocked_status_files(tasks_root):
        found_any = True
        reason = (
            _first_line(status_text, "Blocked Reason")
            or _first_line(status_text, "Blockers")
            or "(see status.md)"
        )
        if len(reason) > 60:
            reason = reason[:57] + "..."
        yield f"  {red}{bold}{task_id}{reset}"
        yield f"  {dim}Reason: {reason}{reset}"
        yield ""
    if not found_any:
        yield f"  {dim}(none){reset}"


def _blocked_elapsed(secs: int) -> str:
    """Format the seconds since a task blocked: ``42s``, ``5m ago``, ``2h 5m ago``."""
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h {(secs % 3600) // 60}m ago"


def attention_blocked_lines(
    tasks_root: str,
    use_color: bool,
    reset: str = "",
    red: str = "",
    bold: str = "",
    dim: str = "",
    cyan: str = "",
    yellow: str = "",
    now: Optional[int] = None,
) -> Iterator[str]:
    """Yield the Attention pane's blocked-task blocks, one line per item.

    Each BLOCKED task renders:

    * ``"ARROW <red><bold>ID<reset>"`` — ARROW is ``▶`` with *use_color*,
      ``>`` without;
    * ``"  Blocked since: <dim>HH:MM:SS (ELAPSED)<reset>"`` from the
      status.md mtime in local time, omitted when it cannot be read;
    * ``"  <yellow>Reason:<reset>"`` then each of up to three lines of
      ``## Blocked Reason`` (else ``## Blockers``) indented four spaces,
      when there are any;
    * ``"  <cyan>Recommended next step:<reset>"`` then up to three lines of
      ``## Next Recommended Step``, likewise;
    * a blank line.

    With no BLOCKED task it yields
    ``"  <dim>(no blocked tasks — system running normally)<reset>"`` and a
    blank line.

    Args:
        tasks_root: The project's tasks directory.
        use_color:  Chooses the arrow glyph; the escapes carry the colour.
        reset, red, bold, dim, cyan, yellow: ANSI escapes, or "".
        now:        Epoch seconds the elapsed time counts to; the current
                    time when None.
    """
    arrow = "▶" if use_color else ">"
    if now is None:
        now = int(time.time())
    found_any = False
    for task_id, status_file, status_text in _blocked_status_files(tasks_root):
        found_any = True
        try:
            mtime = int(status_file.stat().st_mtime)
            blocked_since = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
            elapsed_str = _blocked_elapsed(now - mtime)
        except (OSError, OverflowError):
            blocked_since = "unknown"
            elapsed_str = ""

        reason_lines = section_lines(status_text, "Blocked Reason", max_lines=3)
        if not reason_lines:
            reason_lines = section_lines(status_text, "Blockers", max_lines=3)
        next_step_lines = section_lines(status_text, "Next Recommended Step", max_lines=3)

        yield f"{arrow} {red}{bold}{task_id}{reset}"
        if blocked_since != "unknown":
            yield f"  Blocked since: {dim}{blocked_since} ({elapsed_str}){reset}"
        if reason_lines:
            yield f"  {yellow}Reason:{reset}"
            for line in reason_lines:
                yield f"    {line}"
        if next_step_lines:
            yield f"  {cyan}Recommended next step:{reset}"
            for line in next_step_lines:
                yield f"    {line}"
        yield ""
    if not found_any:
        yield f"  {dim}(no blocked tasks — system running normally){reset}"
        yield ""


def _pipeline_role(role_raw: str) -> Optional[str]:
    """Return the pipeline role a README ``## Role`` names, or None.

    The value is lower-cased and cut at the first ``|``.
    """
    role = role_raw.strip().lower().split("|")[0].strip()
    return role if role in DOC_PIPELINE_ROLES else None


def _pipeline_state(state_raw: str) -> str:
    """Return the counted state: DONE for DONE or WONT-DO, BACKLOG for anything unknown."""
    state = state_raw.strip().upper()
    if state in ("DONE", "WONT-DO"):
        return "DONE"
    if state in _PIPELINE_STATES:
        return state
    return "BACKLOG"


def _pipeline_counts(tasks_path: str) -> dict[str, dict[str, int]]:
    """Count document-pipeline tasks by role and state.

    A task counts when its folder holds both ``status.md`` and a
    ``README.md`` whose ``## Role`` is a pipeline role.  Folders named
    ``archive``, ``queues`` or ``plans``, or starting ``TASK-``, are skipped.
    WAITING and BACKLOG both count as waiting.  An empty or blank
    *tasks_path* counts nothing.
    """
    counts = {
        role: {"total": 0, "done": 0, "working": 0, "blocked": 0, "waiting": 0}
        for role in DOC_PIPELINE_ROLES
    }
    if not tasks_path.strip():
        return counts
    tasks_dir = pathlib.Path(tasks_path)
    if not tasks_dir.is_dir():
        return counts
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if task_id in _NON_TASK_DIRS or task_id.startswith("TASK-"):
            continue
        status_file = task_dir / "status.md"
        readme_file = task_dir / "README.md"
        if not status_file.is_file() or not readme_file.is_file():
            continue
        try:
            status_text = status_file.read_text(errors="replace")
            readme_text = readme_file.read_text(errors="replace")
        except OSError:
            continue
        role = _pipeline_role(section_value(readme_text, "Role"))
        if role is None:
            continue
        state = _pipeline_state(section_value(status_text, "State"))
        role_counts = counts[role]
        role_counts["total"] += 1
        if state == "DONE":
            role_counts["done"] += 1
        elif state == "WORKING":
            role_counts["working"] += 1
        elif state == "BLOCKED":
            role_counts["blocked"] += 1
        elif state in ("WAITING", "BACKLOG"):
            role_counts["waiting"] += 1
    return counts


def document_pipeline_lines(
    tasks_dir: str,
    use_color: bool,
    label: str = "",
    done: str = "",
    working: str = "",
    blocked: str = "",
    dim: str = "",
    reset: str = "",
) -> Iterator[str]:
    """Yield one Document Pipeline line per role: writer, tester, cm.

    A role with no task renders ``"<label>ROLE:   <reset><dim>0/0<reset>"``
    (the ``ROLE:`` label left-justified to 8).  Otherwise the line is the
    label, ``<done>DONE/TOTAL<reset>``, two spaces, the state codes
    (``NW`` working, ``NB`` blocked, ``NWa`` waiting, space-separated, each
    in its own colour with *use_color*) and the done percentage, padded so
    the visible text reaches 36 columns with at least one space.

    Args:
        tasks_dir: The project's tasks directory; an empty or blank path
            names no directory and renders every role at ``0/0``.
        use_color: False blanks every escape, whatever was passed.
        label, done, working, blocked, dim, reset: ANSI escapes, or "".
    """
    if not use_color:
        label = done = working = blocked = dim = reset = ""
    counts = _pipeline_counts(tasks_dir)
    for role in DOC_PIPELINE_ROLES:
        c = counts[role]
        total = c["total"]
        padded_label = f"{role + ':':<8}"
        if total == 0:
            yield f"{label}{padded_label}{reset}{dim}0/0{reset}"
            continue

        done_total = f"{c['done']}/{total}"
        pct_str = f"{c['done'] * 100 // total}%"

        codes = ""
        if c["working"] > 0:
            codes += f"{c['working']}W "
        if c["blocked"] > 0:
            codes += f"{c['blocked']}B "
        if c["waiting"] > 0:
            codes += f"{c['waiting']}Wa "
        codes = codes.rstrip()

        fixed_vlen = len(padded_label) + len(done_total) + 2 + len(codes)
        padding = " " * max(1, _PIPELINE_PANE_WIDTH - fixed_vlen - len(pct_str))

        codes_colored = codes
        if use_color and codes:
            codes_colored = ""
            if c["working"] > 0:
                codes_colored += f"{working}{c['working']}W{reset} "
            if c["blocked"] > 0:
                codes_colored += f"{blocked}{c['blocked']}B{reset} "
            if c["waiting"] > 0:
                codes_colored += f"{dim}{c['waiting']}Wa{reset} "
            codes_colored = codes_colored.rstrip()

        yield f"{label}{padded_label}{reset}{done}{done_total}{reset}  {codes_colored}{padding}{pct_str}"


def _is_true(value: str) -> bool:
    """Read a ``--use-color`` value: "true" in any letter case."""
    return value.lower() == "true"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.dashboard.task_panes",
        description="Render a dashboard pane from a project's task folders.",
    )
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    def escape(sub: argparse.ArgumentParser, name: str, what: str) -> None:
        sub.add_argument(f"--{name}", default="", metavar="ESC",
                         help=f"ANSI escape for {what}; default '' (no colour).")

    def common(sub: argparse.ArgumentParser, use_color: bool) -> None:
        sub.add_argument("tasks_dir", help="The project's tasks directory, e.g. "
                         "~/pgai_agent_kanban/projects/pgai-agent-kanban/tasks.")
        if use_color:
            sub.add_argument("--use-color", default="false", metavar="true|false",
                             help="'true' (any case) when the pane renders in colour; default false.")

    window = verbs.add_parser(
        "status-window-blocked",
        help="The status window's ATTENTION list of BLOCKED tasks.",
        description="Print each BLOCKED task's ID and a reason cut to 60 characters, or '(none)'.",
    )
    common(window, use_color=False)
    escape(window, "reset", "resetting attributes")
    escape(window, "red", "the task ID")
    escape(window, "dim", "the reason line")
    escape(window, "bold", "the task ID")
    window.set_defaults(func=lambda a: status_window_blocked_lines(
        a.tasks_dir, a.reset, a.red, a.dim, a.bold))

    attention = verbs.add_parser(
        "attention-blocked",
        help="The Attention pane's BLOCKED task blocks.",
        description="Print each BLOCKED task with its blocked-since time, reason and next step.",
    )
    common(attention, use_color=True)
    escape(attention, "reset", "resetting attributes")
    escape(attention, "red", "the task ID")
    escape(attention, "bold", "the task ID")
    escape(attention, "dim", "the blocked-since time and the empty state")
    escape(attention, "cyan", "the next-step label")
    escape(attention, "yellow", "the reason label")
    attention.set_defaults(func=lambda a: attention_blocked_lines(
        a.tasks_dir, _is_true(a.use_color), a.reset, a.red, a.bold, a.dim, a.cyan, a.yellow))

    pipeline = verbs.add_parser(
        "document-pipeline",
        help="The document drill's per-role pipeline counts.",
        description="Print one done/total line for each of writer, tester and cm.",
    )
    common(pipeline, use_color=True)
    escape(pipeline, "label", "the role label")
    escape(pipeline, "done", "the done/total count")
    escape(pipeline, "working", "the working count")
    escape(pipeline, "blocked", "the blocked count")
    escape(pipeline, "dim", "the waiting count and 0/0")
    escape(pipeline, "reset", "resetting attributes")
    pipeline.set_defaults(func=lambda a: document_pipeline_lines(
        a.tasks_dir, _is_true(a.use_color), a.label, a.done, a.working, a.blocked, a.dim, a.reset))

    return parser


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = _build_parser().parse_args(argv)
    for line in args.func(args):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

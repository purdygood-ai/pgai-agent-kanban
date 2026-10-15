#!/usr/bin/env python3
"""
queue_details.py — the single resolver behind the "show-queues --details"
per-agent, per-state task listing.

This is the shared resolver behind ``pgai_agent_kanban.ops.read.get_queues``,
which team/scripts/dashboard/show-queues.sh --details (rendered by
``dashboard.queue_pane``) and ``GET /show-queues?details=true``
(team/pgai_agent_kanban/api/reads.py) both call -- one resolver, two
renderers (ANSI/plain text for the CLI, JSON for the REST twin).
Neither renderer re-scans tasks/queues on its own; both pass this module's
return value straight to their own presentation layer.

WHAT IT COMPUTES
-----------------
For every non-DONE task directory under *tasks_root*, resolves:
  - the task's own ``## State`` (from status.md), normalized to one of
    BACKLOG / WAITING / WORKING / BLOCKED (DONE and WONT-DO tasks are
    dropped -- this is a "what still needs attention" view);
  - the task's owning agent (from its README.md's ``## Role`` field);
  - whether the state agrees with the task's own queue-backlog marker
    (see ``QUEUE_MARKER_VALID_STATES``) -- a mismatch, a missing queue
    entry, or a queue entry with no task folder each surface as a named
    warning string rather than silently dropping the discrepancy.

Returns a dict keyed by canonical agent name (``AGENT_ORDER``), each value
a dict keyed by canonical state (``STATE_ORDER``), each value a list of
``{"task_id": str, "warning": str | None}`` entries, sorted by task_id.
Every agent/state key is always present (possibly with an empty list) so
a caller (JSON consumer or text renderer) never needs to guess whether an
absent key means "no tasks" or "not scanned".

Warning values (``None`` means no discrepancy):
  - ``"folder_missing"``   -- queue entry exists, no task folder for it.
  - ``"no_queue_entry"``   -- active task has no matching queue-backlog line.
  - ``"marker_mismatch"``  -- queue marker and status.md state disagree.
  - ``"unknown_state:<STATE>"`` -- status.md's ``## State`` value is not
    one of the six canonical states.

Usage (import)::

    from pgai_agent_kanban.dashboard.queue_details import resolve_queue_details
    grouped = resolve_queue_details(tasks_root, queues_dir)

Usage (CLI, consumed by show-queues.sh via team/scripts/lib/pp_run_ops.sh's
PYTHONPATH convention)::

    python3 -m pgai_agent_kanban.dashboard.queue_details <tasks_root> [<queues_dir>]

Prints the resolved structure as JSON to stdout.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

# Run by file path (``python3 .../queue_details.py``) or loaded from a file
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

from pgai_agent_kanban.task_status import section_value  # noqa: E402

__all__ = [
    "AGENT_ORDER",
    "STATE_ORDER",
    "empty_queue_details",
    "resolve_queue_details",
]

# Canonical agent order: the order the --details listing renders agents in.
AGENT_ORDER = ["pm", "coder", "writer", "tester", "cm", "po", "human", "unknown"]

# State display order within each agent section.
STATE_ORDER = ["WORKING", "BLOCKED", "WAITING", "BACKLOG"]

_SKIP_DIRS = {"archive", "queues", "plans"}
_DONE_STATES = {"DONE", "WONT-DO"}
_VALID_STATES = {"WORKING", "BLOCKED", "WAITING", "DONE", "WONT-DO", "BACKLOG"}

# Queue marker -> set of valid canonical states.
_QUEUE_MARKER_VALID_STATES = {
    " ": {"BACKLOG", "WAITING", "WORKING"},
    "W": {"WAITING"},
    "A": {"WORKING"},
    "B": {"BLOCKED"},
}
# Markers to skip: done or under review.
_QUEUE_SKIP_MARKERS = {"x", "R"}

_ROLE_MAP = {
    "pm": "pm",
    "coder": "coder",
    "writer": "writer",
    "tester": "tester",
    "cm": "cm",
    "po": "po",
    "human": "human",
}

_QUEUE_FILE_AGENT_MAP = {
    "pm": "pm",
    "coder": "coder",
    "writer": "writer",
    "tester": "tester",
    "cm": "cm",
    "po": "po",
    "human": "human",
    "bug": "unknown",
}

# Pattern: "- [X] TASK-ID" where X is a single marker character. Liberal on
# the non-date portion (roles/CODER.md "Parsers are liberal in what they
# accept") so a new agent name introduced after this module was written
# still matches.
_QUEUE_LINE_RE = re.compile(
    r"^\s*-\s+\[(.)\]\s+"
    r"([A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)?"
    r"-(?:\d{8}-\d+-\S+|v\d+[.\d]*-\d+))"
    r"\s*$"
)


def _read_field(text: str, heading: str) -> str:
    """Return the value of the '## heading' section (task_status.section_value)."""
    return section_value(text, heading)


def _normalize_role(role_raw: str) -> str:
    r = role_raw.strip().lower().split("|")[0].strip()
    return _ROLE_MAP.get(r, "unknown")


def _normalize_state(state_raw: str) -> str:
    """Uppercase canonical state, DONE for done-family, or raw value for
    unknown (never guessed into a canonical bucket)."""
    s = state_raw.strip().upper()
    if s in ("DONE", "WONT-DO"):
        return "DONE"
    if s in _VALID_STATES:
        return s
    if not s:
        return "BACKLOG"
    return s


def _queue_file_to_agent(stem: str) -> str:
    parts = stem.split("_")
    candidate = parts[0].lower() if parts else ""
    return _QUEUE_FILE_AGENT_MAP.get(candidate, "unknown")


def _build_queue_marker_map(queues_dir: "pathlib.Path | None") -> dict[str, str]:
    queue_marker_map: dict[str, str] = {}
    if queues_dir is None or not queues_dir.is_dir():
        return queue_marker_map
    for queue_file in sorted(queues_dir.glob("*_backlog.md")):
        try:
            text = queue_file.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _QUEUE_LINE_RE.match(line)
            if not m:
                continue
            marker, task_id = m.group(1), m.group(2)
            if task_id not in queue_marker_map:
                queue_marker_map[task_id] = marker
    return queue_marker_map


def empty_queue_details() -> dict[str, dict[str, list]]:
    """Return the listing of a project with no active task.

    The shape :func:`resolve_queue_details` returns -- every ``AGENT_ORDER``
    x ``STATE_ORDER`` key present -- with every list empty; what it returns
    for a tasks directory that does not exist.
    """
    return {agent: {state: [] for state in STATE_ORDER} for agent in AGENT_ORDER}


def resolve_queue_details(
    tasks_root: "pathlib.Path | str",
    queues_dir: "pathlib.Path | str | None" = None,
) -> dict[str, dict[str, list[dict[str, "str | None"]]]]:
    """Resolve the per-agent, per-state active-task listing for one project.

    Args:
        tasks_root: The project's ``tasks/`` directory (as
            ``pp_tasks_dir``/``ctx.tasks_dir`` resolves it).
        queues_dir: The project's ``tasks/queues/`` directory (as
            ``pp_queue_path``'s parent resolves it). ``None`` or a
            non-existent directory means "no queue reconciliation data" --
            every active task then reports ``"no_queue_entry"``, matching
            the CLI's own fallback when queue context is unavailable.

    Returns:
        ``{agent: {state: [{"task_id": str, "warning": str | None}, ...]}}``
        with every ``AGENT_ORDER`` x ``STATE_ORDER`` key present (lists may
        be empty), entries sorted by task_id within each state.
    """
    tasks_root = pathlib.Path(tasks_root)
    queues_dir = pathlib.Path(queues_dir) if queues_dir else None

    queue_marker_map = _build_queue_marker_map(queues_dir)

    grouped: dict[str, dict[str, list[tuple[str, "str | None"]]]] = {
        agent: {state: [] for state in STATE_ORDER} for agent in AGENT_ORDER
    }

    seen_task_ids: set[str] = set()
    done_task_ids: set[str] = set()

    if tasks_root.is_dir():
        for task_dir in sorted(tasks_root.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            if task_id in _SKIP_DIRS or task_id.startswith("TASK-"):
                continue
            status_file = task_dir / "status.md"
            if not status_file.is_file():
                continue
            readme_file = task_dir / "README.md"

            try:
                status_text = status_file.read_text(errors="replace")
            except OSError:
                continue

            state_raw = _read_field(status_text, "State")
            state = _normalize_state(state_raw)

            if state == "DONE":
                done_task_ids.add(task_id)
                continue

            agent = "unknown"
            if readme_file.is_file():
                try:
                    readme_text = readme_file.read_text(errors="replace")
                    role_raw = _read_field(readme_text, "Role")
                    agent = _normalize_role(role_raw)
                except OSError:
                    pass

            if state not in STATE_ORDER:
                warn_type: "str | None" = f"unknown_state:{state}"
                grouped[agent]["BACKLOG"].append((task_id, warn_type))
                seen_task_ids.add(task_id)
                continue

            warn_type = None
            if task_id in queue_marker_map:
                marker = queue_marker_map[task_id]
                if marker in _QUEUE_SKIP_MARKERS:
                    warn_type = "marker_mismatch"
                else:
                    valid_states = _QUEUE_MARKER_VALID_STATES.get(marker, {"BACKLOG"})
                    if state not in valid_states:
                        warn_type = "marker_mismatch"
            else:
                warn_type = "no_queue_entry"

            grouped[agent][state].append((task_id, warn_type))
            seen_task_ids.add(task_id)

    # Queue entries with no task folder at all -- surfaced under the queue
    # file's own agent, using the marker's first valid state.
    if queues_dir is not None and queues_dir.is_dir():
        for queue_file in sorted(queues_dir.glob("*_backlog.md")):
            agent = _queue_file_to_agent(queue_file.stem)
            try:
                text = queue_file.read_text(errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                m = _QUEUE_LINE_RE.match(line)
                if not m:
                    continue
                marker, task_id = m.group(1), m.group(2)
                if marker in _QUEUE_SKIP_MARKERS:
                    continue
                if task_id in seen_task_ids or task_id in done_task_ids:
                    continue
                valid = _QUEUE_MARKER_VALID_STATES.get(marker, {"BACKLOG"})
                state = "BACKLOG" if "BACKLOG" in valid else next(iter(valid))
                already_present = any(
                    tid == task_id
                    for ag in AGENT_ORDER
                    for st in STATE_ORDER
                    for tid, _ in grouped[ag][st]
                )
                if not already_present:
                    grouped[agent][state].append((task_id, "folder_missing"))
                    seen_task_ids.add(task_id)

    result: dict[str, dict[str, list[dict[str, "str | None"]]]] = {
        agent: {
            state: [
                {"task_id": tid, "warning": warn}
                for tid, warn in sorted(grouped[agent][state], key=lambda x: x[0])
            ]
            for state in STATE_ORDER
        }
        for agent in AGENT_ORDER
    }
    return result


def main() -> int:
    """Print one project's per-agent, per-state active-task listing as JSON.

    ``python3 -m pgai_agent_kanban.dashboard.queue_details TASKS_ROOT
    [QUEUES_DIR]`` prints :func:`resolve_queue_details`'s result on one line.
    Exit codes: 0 printed; 1 wrong number of arguments (usage on stderr).
    """
    if len(sys.argv) not in (2, 3):
        print(
            "usage: python3 -m pgai_agent_kanban.dashboard.queue_details "
            "<tasks_root> [<queues_dir>]",
            file=sys.stderr,
        )
        return 1
    tasks_root = sys.argv[1]
    queues_dir = sys.argv[2] if len(sys.argv) == 3 and sys.argv[2] else None
    result = resolve_queue_details(tasks_root, queues_dir)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - trivial CLI wiring
    sys.exit(main())

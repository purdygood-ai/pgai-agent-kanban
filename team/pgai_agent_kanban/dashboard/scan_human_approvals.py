#!/usr/bin/env python3
"""
scan_human_approvals.py — Scanner for pending HUMAN-APPROVE gate tasks.

Scans all registered projects for tasks whose task ID starts with
"HUMAN-APPROVE" and whose status is WAITING or BACKLOG (pending approval),
then prints a formatted listing suitable for dashboard window 14.

Each row includes:
  - project name
  - target RC / item awaiting approval (from task README ## Release Version)
  - age (elapsed time since status.md was last modified)
  - show content (the task's ## Goal section from README.md)
  - review commands: ordered list of copy-paste-ready command strings:
      scripts/show.sh --project <proj> --key <task-id>  (always present)
      scripts/show-test-report.sh --project <proj> --key <rc-version>
        (present only when the RC target version is known, i.e. starts with "v")
  - approve command: scripts/close.sh --project <proj> --key <task-id>
  - reject command:  scripts/wontdo.sh --project <proj> --key <task-id>

Empty state: one line — "no approvals pending."

Usage (CLI):
    python3 team/pgai_agent_kanban/dashboard/scan_human_approvals.py \\
        <kanban_root> [--color | --no-color]

Usage (import — window 14 renderer):
    from pgai_agent_kanban.dashboard.scan_human_approvals import (
        scan_pending_approvals,
    )
    scan_pending_approvals(kanban_root="/path/to/kanban", use_color=True)

Usage (import — structured data):
    from pgai_agent_kanban.dashboard.scan_human_approvals import (
        collect_pending_approvals,
    )
    records = collect_pending_approvals(kanban_root="/path/to/kanban")
    # returns list[dict] — see collect_pending_approvals docstring for field spec.

Exit codes:
    0 — always (errors in individual task reads are silently skipped)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import time


# ---------------------------------------------------------------------------
# Field reader helpers (mirrors pattern in scan_attention.py)
# ---------------------------------------------------------------------------

def _read_field(text: str, heading: str) -> str:
    """Return first non-blank content line after '## heading'."""
    pat = re.compile(r'^\s*##\s+' + re.escape(heading) + r'\s*$', re.M | re.I)
    m = pat.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped:
            return stripped
    return ""


def _read_section(text: str, heading: str, max_lines: int = 5) -> list[str]:
    """Return up to max_lines non-blank content lines from a ## section."""
    pat = re.compile(r'^\s*##\s+' + re.escape(heading) + r'\s*$', re.M | re.I)
    m = pat.search(text)
    if not m:
        return []
    rest = text[m.end():]
    lines = []
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped:
            lines.append(stripped)
            if len(lines) >= max_lines:
                break
    return lines


def _format_age(secs: int) -> str:
    """Format elapsed seconds as human-readable age string."""
    secs = max(0, secs)
    if secs < 60:
        return f"{secs}s"
    elif secs < 3600:
        return f"{secs // 60}m"
    elif secs < 86400:
        hrs = secs // 3600
        mins = (secs % 3600) // 60
        return f"{hrs}h {mins}m"
    else:
        days = secs // 86400
        hrs = (secs % 86400) // 3600
        return f"{days}d {hrs}h"


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------

def _iter_projects(kanban_root: pathlib.Path):
    """Yield (project_name, project_tasks_path) for each registered project.

    Prefers multi-project layout (kanban_root/projects/<name>/tasks/).
    Falls back to single-project layout (kanban_root/tasks/) when the
    projects/ directory does not exist.
    """
    projects_dir = kanban_root / "projects"
    if projects_dir.is_dir():
        for entry in sorted(projects_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            tasks_dir = entry / "tasks"
            if tasks_dir.is_dir():
                yield (entry.name, tasks_dir)
    else:
        # Single-project fallback
        tasks_dir = kanban_root / "tasks"
        if tasks_dir.is_dir():
            yield ("pgai-agent-kanban", tasks_dir)


# ---------------------------------------------------------------------------
# Pending approval scanner — internal helper
# ---------------------------------------------------------------------------

_PENDING_STATES = {"WAITING", "BACKLOG"}
_SKIP_DIRS = {"archive", "queues", "plans"}


def _collect_pending_approvals(kanban_root: pathlib.Path) -> list[dict]:
    """Return a list of pending HUMAN-APPROVE task dicts sorted by project + task_id.

    Each dict contains:
      project      — project name
      task_id      — task ID (folder name, always starts with "HUMAN-APPROVE")
      release_ver  — ## Release Version from README.md (or task_id if absent)
      goal_lines   — list of strings from ## Goal section of README.md (show content)
      age_str      — human-readable age of the task's status.md mtime
      state        — WAITING or BACKLOG

    This private helper is used internally by scan_pending_approvals (the window-14
    renderer) and by collect_pending_approvals (the public data API).
    """
    now = int(time.time())
    results = []

    for proj_name, tasks_dir in _iter_projects(kanban_root):
        if not tasks_dir.is_dir():
            continue
        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            if task_id in _SKIP_DIRS:
                continue
            if not task_id.startswith("HUMAN-APPROVE"):
                continue

            status_file = task_dir / "status.md"
            readme_file = task_dir / "README.md"

            if not status_file.is_file():
                continue

            try:
                status_text = status_file.read_text(errors="replace")
            except OSError:
                continue

            state = _read_field(status_text, "State").upper()
            if state not in _PENDING_STATES:
                continue

            # Age from status.md mtime
            try:
                mtime = int(status_file.stat().st_mtime)
                age_secs = now - mtime
                age_str = _format_age(age_secs)
            except OSError:
                age_str = "unknown"

            # Read README for release version and goal (show content)
            release_ver = task_id  # fallback
            goal_lines: list[str] = []
            if readme_file.is_file():
                try:
                    readme_text = readme_file.read_text(errors="replace")
                    rv = _read_field(readme_text, "Release Version")
                    if rv:
                        release_ver = rv
                    goal_lines = _read_section(readme_text, "Goal", max_lines=4)
                except OSError:
                    pass

            results.append({
                "project": proj_name,
                "task_id": task_id,
                "release_ver": release_ver,
                "goal_lines": goal_lines,
                "age_str": age_str,
                "state": state,
            })

    # Sort: project name ascending, then task_id ascending
    results.sort(key=lambda r: (r["project"], r["task_id"]))
    return results


# ---------------------------------------------------------------------------
# Public pure function — structured records for API and other consumers
# ---------------------------------------------------------------------------


def _build_review_cmds(proj: str, task_id: str, rc: str) -> list[str]:
    """Return the ordered list of copy-paste-ready review command strings.

    Always includes the show.sh command for the gate task itself.  Includes
    the show-test-report.sh command only when the RC target version is known
    (identified by starting with "v") — never guesses or invents a key.

    Args:
        proj:    Project name.
        task_id: Approval task ID (e.g. "HUMAN-APPROVE-v1.10.0-099").
        rc:      RC version string from ## Release Version in README.md.
                 When absent, the fallback value is the task_id itself, which
                 will not start with "v" and so the test-report line is omitted.

    Returns:
        Ordered list of verbatim command strings; always at least one entry.
    """
    cmds: list[str] = [
        f"scripts/show.sh --project {proj} --key {task_id}",
    ]
    # Include the test-report line only when the version is recognisable
    # (starts with "v").  The fallback value for ## Release Version is the
    # task_id itself, which starts with "HUMAN-APPROVE" — never "v".
    if rc.startswith("v"):
        cmds.append(
            f"scripts/show-test-report.sh --project {proj} --key {rc}"
        )
    return cmds


def collect_pending_approvals(
    kanban_root: str,
    project: str | None = None,
) -> list[dict]:
    """Return pending HUMAN-APPROVE records as structured data.

    Pure function: no I/O side effects, no print output.  Both the window-14
    renderer (scan_pending_approvals) and the GET /approvals endpoint consume
    this function — one implementation, two surfaces.

    Args:
        kanban_root: Path to the kanban root directory (string or path-like).
        project:     When supplied, return only records for this project name.
                     When None, return records for ALL projects (aggregation view).

    Returns:
        List of dicts, one per pending HUMAN-APPROVE task, sorted by project
        name then task_id.  Each dict has the following keys:

          task_id        — task folder name (e.g. "HUMAN-APPROVE-v1.10.0-099")
          project        — project name (e.g. "pgai-agent-kanban")
          state          — "WAITING" or "BACKLOG"
          rc             — RC version string from ## Release Version in README.md
          target_version — same as rc (alias; both are included for API consumers)
          age            — human-readable age string (e.g. "2h 5m")
          review         — show content from ## Goal in README.md (lines joined with " | ")
          review_cmds    — ordered list of verbatim operator review command strings:
                           [0] scripts/show.sh --project <p> --key <task-id> (always)
                           [1] scripts/show-test-report.sh --project <p> --key <rc>
                               (present only when target version is known)
          approve_cmd    — verbatim operator approve command string
          reject_cmd     — verbatim operator reject command string

        Returns an empty list when no approvals are pending (clean system).

    Raises:
        No exceptions.  Unreadable task folders are silently skipped.
    """
    root = pathlib.Path(kanban_root)
    raw_records = _collect_pending_approvals(root)

    records = []
    for r in raw_records:
        proj = r["project"]
        task_id = r["task_id"]
        rc = r["release_ver"]
        review = " | ".join(r["goal_lines"]) if r["goal_lines"] else ""
        review_cmds = _build_review_cmds(proj, task_id, rc)
        approve_cmd = f"scripts/close.sh --project {proj} --key {task_id}"
        reject_cmd = f"scripts/wontdo.sh --project {proj} --key {task_id}"

        records.append({
            "task_id": task_id,
            "project": proj,
            "state": r["state"],
            "rc": rc,
            "target_version": rc,
            "age": r["age_str"],
            "review": review,
            "review_cmds": review_cmds,
            "approve_cmd": approve_cmd,
            "reject_cmd": reject_cmd,
        })

    if project is not None:
        records = [rec for rec in records if rec["project"] == project]

    return records


# ---------------------------------------------------------------------------
# Window-14 renderer — consumes collect_pending_approvals via _collect_pending_approvals
# ---------------------------------------------------------------------------


def scan_pending_approvals(kanban_root: str, use_color: bool = True) -> None:
    """Print the pending-approvals listing for dashboard window 14.

    Empty state: prints exactly one line — "no approvals pending."

    Each entry renders (in order):
      - Header: task-id and project label
      - RC/target, age, state
      - Show content (goal lines)
      - Review: block with copy-paste-ready review commands (show.sh always;
        show-test-report.sh when the RC version is known) — ABOVE Approve/Reject
      - Approve: verbatim approve command
      - Reject:  verbatim reject command

    Consumes _collect_pending_approvals() for the underlying data; the same
    data source backs the GET /approvals endpoint via collect_pending_approvals().
    """
    root = pathlib.Path(kanban_root)

    RESET  = "\033[0m"           if use_color else ""
    C_BOLD = "\033[1m"           if use_color else ""
    C_DIM  = "\033[2m"           if use_color else ""
    C_RED  = "\033[0;31m"        if use_color else ""
    C_YEL  = "\033[0;33m"        if use_color else ""
    C_CYAN = "\033[0;36m"        if use_color else ""
    C_GRN  = "\033[0;32m"        if use_color else ""
    C_BLU  = "\033[0;34m"        if use_color else ""
    HAND   = "✋"            if use_color else "!"  # ✋

    pending = _collect_pending_approvals(root)

    if not pending:
        print(f"  {C_DIM}no approvals pending.{RESET}")
        return

    for entry in pending:
        proj    = entry["project"]
        task_id = entry["task_id"]
        ver     = entry["release_ver"]
        age     = entry["age_str"]
        state   = entry["state"]
        goals   = entry["goal_lines"]
        review_cmds = _build_review_cmds(proj, task_id, ver)

        # Header row: ✋ task-id [project]
        print(
            f"{HAND} {C_BOLD}{C_RED}{task_id}{RESET}"
            f"  {C_DIM}[{proj}]{RESET}"
        )
        print(
            f"  RC/target:  {C_CYAN}{ver}{RESET}"
            f"   age: {C_DIM}{age}{RESET}"
            f"   state: {C_DIM}{state}{RESET}"
        )

        # Show content (goal lines from README)
        if goals:
            for line in goals:
                print(f"  {line}")

        # Review block — copy-paste-ready commands ABOVE Approve/Reject
        print(f"  {C_BLU}Review:{RESET}")
        for cmd in review_cmds:
            print(f"    {cmd}")

        # Approve command
        print(
            f"  {C_GRN}Approve:{RESET}  "
            f"scripts/close.sh --project {proj} --key {task_id}"
        )
        # Reject command
        print(
            f"  {C_RED}Reject: {RESET}  "
            f"scripts/wontdo.sh --project {proj} --key {task_id}"
        )
        print("")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan for pending HUMAN-APPROVE gate tasks (dashboard window 14).",
    )
    parser.add_argument(
        "kanban_root",
        help="Path to the kanban root directory.",
    )
    parser.add_argument(
        "--color",
        dest="color",
        action="store_true",
        default=True,
        help="Enable ANSI color codes (default).",
    )
    parser.add_argument(
        "--no-color",
        dest="color",
        action="store_false",
        help="Disable ANSI color codes.",
    )
    args = parser.parse_args()

    use_color = args.color
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        use_color = False

    scan_pending_approvals(args.kanban_root, use_color=use_color)


if __name__ == "__main__":
    _main()

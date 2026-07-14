#!/usr/bin/env python3
"""
scan_attention.py — Standalone attention scanners for the pgai kanban dashboard.

This module is an as-is extraction of two inline Python heredoc blocks
previously embedded in team/scripts/dashboard/attention.sh:

  1. Blocked-tasks scanner (lines 214–341): scans task directories for
     tasks whose status.md contains ``state: BLOCKED`` and prints a
     formatted summary.

  2. Quarantine alerts scanner (lines 353–494): scans project directories
     for files approaching or already in quarantine (.rejected/).

The scanning logic is preserved exactly — do NOT change output format,
ANSI codes, Unicode characters, indentation, or spacing without filing a
separate bug.  Any quirks in the original are intentionally kept.

Additionally:

  3. Rejected-files scanner: walks each project's ``rejected/`` directory,
     reads the companion ``.reason`` sidecar for each quarantined file, and
     prints a ``QUARANTINED FILES`` section listing project, type, filename,
     reason, and retry_count.  The section is suppressed when no files exist.

Usage (CLI):
    python3 team/pgai_agent_kanban/dashboard/scan_attention.py \\
        blocked-tasks <tasks_root> [--color | --no-color]

    python3 team/pgai_agent_kanban/dashboard/scan_attention.py \\
        quarantine <kanban_root> [--color | --no-color] [--threshold N]

    python3 team/pgai_agent_kanban/dashboard/scan_attention.py \\
        rejected-files <kanban_root> [--color | --no-color]

    python3 team/pgai_agent_kanban/dashboard/scan_attention.py \\
        stale-working-tasks <tasks_root> [--color | --no-color]
            [--max-task-seconds N]

    python3 team/pgai_agent_kanban/dashboard/scan_attention.py \\
        transient-tasks <tasks_root> [--color | --no-color]

    python3 team/pgai_agent_kanban/dashboard/scan_attention.py \\
        overwatch-section <kanban_root> [--color | --no-color] [--max-entries N]

    python3 team/pgai_agent_kanban/dashboard/scan_attention.py \\
        pending-approvals <kanban_root> [--color | --no-color]

Usage (import):
    from pgai_agent_kanban.dashboard.scan_attention import (
        scan_blocked_tasks,
        scan_quarantine,
        scan_rejected,
        scan_stale_working_tasks,
        scan_transient_tasks,
        scan_overwatch_section,
        scan_pending_approvals_attention,
    )
    scan_blocked_tasks(tasks_root="/path/to/tasks", use_color=True)
    scan_quarantine(kanban_root="/path/to/kanban", use_color=True, threshold=3)
    scan_rejected(kanban_root="/path/to/kanban", use_color=True)
    scan_stale_working_tasks(tasks_root="/path/to/tasks", use_color=True,
                             max_task_seconds=5400)
    scan_transient_tasks(tasks_root="/path/to/tasks", use_color=True)
    scan_overwatch_section(kanban_root="/path/to/kanban", use_color=True)
    scan_pending_approvals_attention(kanban_root="/path/to/kanban", use_color=True)

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
from datetime import datetime

# Sidecar parser from ticket 1 (CODER-20260528-001-rejected-sidecar-parser).
# Import is deferred inside scan_rejected so the module remains importable even
# when the kanban package root is not on sys.path at import time; the function
# itself adds the necessary path before use.
_REJECTED_MODULE = None


def _get_read_reason_sidecar():
    """Return read_reason_sidecar, importing _rejected lazily on first call."""
    global _REJECTED_MODULE
    if _REJECTED_MODULE is None:
        # Ensure the package root (team/) is importable.
        _this_dir = pathlib.Path(__file__).parent  # .../dashboard/
        _pkg_root = _this_dir.parent.parent         # .../team/
        _pkg_root_str = str(_pkg_root)
        if _pkg_root_str not in sys.path:
            sys.path.insert(0, _pkg_root_str)
        from pgai_agent_kanban.dashboard._rejected import read_reason_sidecar
        _REJECTED_MODULE = read_reason_sidecar
    return _REJECTED_MODULE


# ---------------------------------------------------------------------------
# Blocked-tasks scanner
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


def _format_elapsed(secs: int) -> str:
    """Format elapsed seconds as human-readable string."""
    secs = max(0, secs)
    if secs < 60:
        return f"{secs}s ago"
    elif secs < 3600:
        return f"{secs // 60}m ago"
    else:
        hrs = secs // 3600
        mins = (secs % 3600) // 60
        return f"{hrs}h {mins}m ago"


def _is_transient_blocked(status_text: str) -> bool:
    """Return True when a BLOCKED task was caused by a transient API error.

    A task qualifies as transient when ALL of the following are true:
      1. ## State is BLOCKED
      2. ## Needs Human is "no" (or absent)
      3. ## Blocked Reason starts with "TRANSIENT API ERROR"

    This mirrors the wake scripts' transient-error detection logic so that the
    attention window renders transient blocks in a separate section instead
    of alongside real Needs-Human=yes blocks.
    """
    needs_human = _read_field(status_text, "Needs Human").lower()
    if needs_human == "yes":
        return False
    blocked_reason = _read_field(status_text, "Blocked Reason")
    return blocked_reason.upper().startswith("TRANSIENT API ERROR")


def scan_blocked_tasks(tasks_root: str, use_color: bool) -> None:
    """Scan task directories for BLOCKED state and print formatted output.

    Produces identical output to the ``PYEOF`` heredoc block in
    team/scripts/dashboard/attention.sh (lines 214–341).

    Transient API error blocks are rendered in their own section
    by scan_transient_tasks; this function excludes them so they do not appear
    in both sections simultaneously.

    Args:
        tasks_root:  Path to the tasks directory to scan.
        use_color:   When True, emit ANSI color codes and Unicode symbols.
    """
    tasks_root_path = pathlib.Path(tasks_root)

    # ANSI codes
    RESET  = "\033[0m"    if use_color else ""
    C_RED  = "\033[0;31m" if use_color else ""
    C_BOLD = "\033[1m"    if use_color else ""
    C_DIM  = "\033[2m"    if use_color else ""
    C_CYAN = "\033[0;36m" if use_color else ""
    C_YEL  = "\033[0;33m" if use_color else ""
    ARROW  = "▶" if use_color else ">"

    found_any = False
    now = int(time.time())

    if tasks_root_path.is_dir():
        for task_dir in sorted(tasks_root_path.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            # Skip special subdirectories
            if task_id in {"archive", "queues", "plans"}:
                continue
            status_file = task_dir / "status.md"
            if not status_file.is_file():
                continue
            try:
                status_text = status_file.read_text(errors="replace")
            except OSError:
                continue

            state = _read_field(status_text, "State").upper()
            if state != "BLOCKED":
                continue

            # Exclude transient blocks — they appear in the TRANSIENT section.
            if _is_transient_blocked(status_text):
                continue

            found_any = True

            # Get mtime for "blocked since"
            try:
                mtime = int(status_file.stat().st_mtime)
                elapsed_secs = now - mtime
                blocked_since = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
                elapsed_str = _format_elapsed(elapsed_secs)
            except (OSError, OverflowError, ValueError):
                blocked_since = "unknown"
                elapsed_str = ""

            # Extract reason and next step
            reason_lines = _read_section(status_text, "Blocked Reason", max_lines=5)
            if not reason_lines:
                reason_lines = _read_section(status_text, "Blockers", max_lines=5)
            next_step_lines = _read_section(status_text, "Next Recommended Step", max_lines=5)

            # Render the blocked task block
            print(f"\n{ARROW} {C_RED}{C_BOLD}{task_id}{RESET}")
            if blocked_since != "unknown":
                print(f"  Blocked since: {C_DIM}{blocked_since} ({elapsed_str}){RESET}")
            if reason_lines:
                print(f"  {C_YEL}Reason:{RESET}")
                for line in reason_lines:
                    print(f"    {line}")
            if next_step_lines:
                print(f"\n  {C_CYAN}Recommended next step:{RESET}")
                for line in next_step_lines:
                    print(f"    {line}")
            print("")

    if not found_any:
        print(f"\n  {C_DIM}(no blocked tasks — system running normally){RESET}")
        print("")


def scan_transient_tasks(tasks_root: str, use_color: bool) -> None:
    """Scan task directories for BLOCKED transient API error tasks.

    Renders tasks whose Blocked Reason begins with "TRANSIENT API ERROR" and
    whose Needs Human=no under a visually distinct TRANSIENT / RETRYABLE section
    in the attention window.  These tasks are excluded from scan_blocked_tasks so
    they do not appear in both sections.

    Args:
        tasks_root:  Path to the tasks directory to scan.
        use_color:   When True, emit ANSI color codes and Unicode symbols.
    """
    tasks_root_path = pathlib.Path(tasks_root)

    # ANSI codes — cyan/green palette to signal "retryable, not alarming"
    RESET  = "\033[0m"     if use_color else ""
    C_CYAN = "\033[0;36m"  if use_color else ""
    C_BOLD = "\033[1m"     if use_color else ""
    C_DIM  = "\033[2m"     if use_color else ""
    C_YEL  = "\033[0;33m"  if use_color else ""
    ARROW  = "⟳" if use_color else ">"

    found_any = False
    now = int(time.time())

    if tasks_root_path.is_dir():
        for task_dir in sorted(tasks_root_path.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            if task_id in {"archive", "queues", "plans"}:
                continue
            status_file = task_dir / "status.md"
            if not status_file.is_file():
                continue
            try:
                status_text = status_file.read_text(errors="replace")
            except OSError:
                continue

            state = _read_field(status_text, "State").upper()
            if state != "BLOCKED":
                continue

            if not _is_transient_blocked(status_text):
                continue

            found_any = True

            # Get mtime for "blocked since"
            try:
                mtime = int(status_file.stat().st_mtime)
                elapsed_secs = now - mtime
                blocked_since = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
                elapsed_str = _format_elapsed(elapsed_secs)
            except (OSError, OverflowError, ValueError):
                blocked_since = "unknown"
                elapsed_str = ""

            # Extract reason
            reason_lines = _read_section(status_text, "Blocked Reason", max_lines=3)
            if not reason_lines:
                reason_lines = _read_section(status_text, "Blockers", max_lines=3)

            # Render the transient task block
            print(f"\n{ARROW} {C_CYAN}{C_BOLD}{task_id}{RESET}")
            if blocked_since != "unknown":
                print(f"  Transient since: {C_DIM}{blocked_since} ({elapsed_str}){RESET}")
            if reason_lines:
                print(f"  {C_YEL}Reason:{RESET}")
                for line in reason_lines:
                    print(f"    {line}")
            print(f"  {C_DIM}(will auto-retry on next wake){RESET}")
            print("")

    if not found_any:
        print(f"\n  {C_DIM}(no transient API errors pending retry){RESET}")
        print("")


# ---------------------------------------------------------------------------
# Quarantine alerts scanner
# ---------------------------------------------------------------------------

def _iter_projects(kanban_root: pathlib.Path):
    """Yield (project_name, project_root) for all registered projects.

    Supports the v0.18+ projects/ layout.  Falls back to treating the kanban
    root itself as the single project root when projects/ does not exist.
    """
    projects_dir = kanban_root / "projects"
    if projects_dir.is_dir():
        for entry in sorted(projects_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                yield entry.name, entry
    else:
        # Legacy single-project layout
        yield "pgai-agent-kanban", kanban_root


def _scan_state_file(state_file: pathlib.Path, threshold: int) -> list[tuple[str, int, str]]:
    """Return list of (filename, count, reason) for pending rejections (count < threshold).

    Reads the 4-column TSV format: filename<TAB>count<TAB>timestamp<TAB>reason
    Legacy 3-column rows (no reason) are accepted; reason defaults to ''.
    Only rows where count < threshold are included — files already quarantined
    have had their row removed from the state file by _disc_maybe_quarantine.
    """
    results: list[tuple[str, int, str]] = []
    if not state_file.is_file():
        return results
    try:
        for raw_line in state_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            fname = parts[0]
            try:
                count = int(parts[1])
            except ValueError:
                continue
            reason = parts[3] if len(parts) >= 4 else ""
            if 1 <= count < threshold:
                results.append((fname, count, reason))
    except OSError:
        pass
    return results


def _scan_rejected_dir(rejected_dir: pathlib.Path) -> list[str]:
    """Return sorted list of filenames already in .rejected/."""
    if not rejected_dir.is_dir():
        return []
    try:
        return sorted(
            e.name for e in rejected_dir.iterdir()
            if e.is_file() and not e.name.startswith(".")
        )
    except OSError:
        return []


def scan_quarantine(kanban_root: str, use_color: bool, threshold: int) -> None:
    """Scan project directories for quarantine alerts and print formatted output.

    Produces identical output to the ``QPYEOF`` heredoc block in
    team/scripts/dashboard/attention.sh (lines 353–494).

    Args:
        kanban_root:  Path to the kanban root directory.
        use_color:    When True, emit ANSI color codes and Unicode symbols.
        threshold:    Rejection count at which a file is considered quarantined.
    """
    kanban_root_path = pathlib.Path(kanban_root)

    # ANSI codes
    RESET  = "\033[0m"    if use_color else ""
    C_RED  = "\033[0;31m" if use_color else ""
    C_BOLD = "\033[1m"    if use_color else ""
    C_DIM  = "\033[2m"    if use_color else ""
    C_YEL  = "\033[0;33m" if use_color else ""
    WARN   = "⚠"  if use_color else "!"
    TERM_  = "\U0001f534" if use_color else "X"

    found_any = False

    for proj_name, proj_root in _iter_projects(kanban_root_path):
        # Both priority/ and bugs/ use the same .discovery-state/rejected-counts.tsv
        # at the project root.  Filenames self-identify their intake directory
        # (PRIORITY-* or BUG-*), so we read the state file once per project and
        # bucket entries by prefix.
        state_file = proj_root / ".discovery-state" / "rejected-counts.tsv"

        # Read pending warnings from the shared state file.
        all_warnings = _scan_state_file(state_file, threshold)

        # Bucket warnings by intake directory based on filename prefix.
        warn_by_dir: dict = {"priority": [], "bugs": []}
        for fname, count, reason in all_warnings:
            if fname.upper().startswith("PRIORITY-"):
                warn_by_dir["priority"].append((fname, count, reason))
            elif fname.upper().startswith("BUG-"):
                warn_by_dir["bugs"].append((fname, count, reason))
            # Unknown prefix: silently skip (forward compat).

        # Read terminal (already quarantined) files from each .rejected/ dir.
        for subdir_name in ("priority", "bugs"):
            subdir = proj_root / subdir_name
            rejected_dir = subdir / ".rejected"

            warnings = warn_by_dir[subdir_name]
            terminals = _scan_rejected_dir(rejected_dir)

            if not warnings and not terminals:
                continue

            found_any = True

            if warnings:
                print(
                    f"\n  {C_YEL}{WARN} {proj_name} [{subdir_name}]: "
                    f"{len(warnings)} file(s) approaching quarantine{RESET}"
                )
                for fname, count, reason in warnings:
                    reason_str = f"  {reason}" if reason else ""
                    print(
                        f"      {fname}  "
                        f"({count}/{threshold}){reason_str}"
                    )

            if terminals:
                print(
                    f"\n  {C_RED}{TERM_} {proj_name} [{subdir_name}]: "
                    f"{len(terminals)} file(s) quarantined{RESET}"
                )
                for fname in terminals:
                    print(f"      {fname}")
                print(
                    f"      {C_DIM}Recover: "
                    f"scripts/recover-rejected.sh {proj_name} <filename>{RESET}"
                )

    if not found_any:
        print(f"\n  {C_DIM}(no quarantine alerts){RESET}")
    print("")


# ---------------------------------------------------------------------------
# Quarantined-files section scanner
# ---------------------------------------------------------------------------

_REASON_TRUNCATE = 60  # max chars for reason column before truncating


def scan_rejected(kanban_root: str, use_color: bool) -> None:
    """Scan each project's rejected/ directory and print a QUARANTINED FILES section.

    Walks ``kanban_root/projects/<name>/rejected/`` for every registered project.
    For each file found, reads the companion ``<filename>.reason`` sidecar (parsed
    with the _rejected module).  Skips files whose name ends in ``.reason`` (those
    are the sidecars themselves, not quarantined items).

    When at least one quarantined file exists across all projects, emits:

        QUARANTINED FILES
        ─────…
          <project>  <type>  <filename>  <reason (truncated)>  retry:<N>

    When zero quarantined files exist, emits nothing (no header, no body).

    Args:
        kanban_root: Path to the kanban root directory.
        use_color:   When True, emit ANSI color codes.
    """
    kanban_root_path = pathlib.Path(kanban_root)
    read_reason_sidecar = _get_read_reason_sidecar()

    # ANSI codes — reuse the same C_* palette as scan_blocked_tasks /
    # scan_quarantine so the visual language is consistent.
    RESET  = "\033[0m"    if use_color else ""
    C_RED  = "\033[0;31m" if use_color else ""
    C_BOLD = "\033[1m"    if use_color else ""
    C_DIM  = "\033[2m"    if use_color else ""
    C_CYAN = "\033[0;36m" if use_color else ""
    C_YEL  = "\033[0;33m" if use_color else ""

    # Collect all rows first so we can suppress the section entirely when empty.
    rows: list[tuple[str, str, str, str, str]] = []  # (project, type, filename, reason, retry)

    for proj_name, proj_root in _iter_projects(kanban_root_path):
        rejected_dir = proj_root / "rejected"
        if not rejected_dir.is_dir():
            continue
        try:
            entries = sorted(
                e for e in rejected_dir.iterdir()
                if e.is_file() and not e.name.startswith(".")
            )
        except OSError:
            continue

        for entry in entries:
            # Skip sidecar files — they are metadata, not quarantined items.
            if entry.name.endswith(".reason"):
                continue

            sidecar = rejected_dir / (entry.name + ".reason")
            if sidecar.is_file():
                info = read_reason_sidecar(sidecar)
                orig_type = info.get("original_type") or "(unknown)"
                reason_raw = info.get("reason") or "(no reason)"
                retry_raw = info.get("retry_count")
                retry_str = retry_raw if retry_raw is not None else "?"
            else:
                orig_type = "(unknown)"
                reason_raw = "(no sidecar)"
                retry_str = "?"

            # Truncate reason to a sensible display width.
            if len(reason_raw) > _REASON_TRUNCATE:
                reason_display = reason_raw[:_REASON_TRUNCATE - 1] + "…"
            else:
                reason_display = reason_raw

            rows.append((proj_name, orig_type, entry.name, reason_display, retry_str))

    if not rows:
        # Zero quarantined files — emit nothing (suppress section entirely).
        return

    # Render section header.
    print(f"\n{C_RED}{C_BOLD}QUARANTINED FILES{RESET}")

    # One row per item.
    for proj_name, orig_type, fname, reason_display, retry_str in rows:
        print(
            f"  {C_YEL}{proj_name}{RESET}"
            f"  {C_DIM}{orig_type}{RESET}"
            f"  {fname}"
            f"  {C_CYAN}{reason_display}{RESET}"
            f"  {C_DIM}retry:{retry_str}{RESET}"
        )
    print("")


# ---------------------------------------------------------------------------
# Stale WORKING tasks scanner (D4, v0.64.0)
# ---------------------------------------------------------------------------

def scan_stale_working_tasks(
    tasks_root: str,
    use_color: bool,
    max_task_seconds: int,
) -> None:
    """Scan task directories for WORKING tasks whose age exceeds max_task_seconds.

    A task is "stale" when ``now - status_file_mtime > max_task_seconds``.
    The mtime of status.md is used as a proxy for when the task entered
    WORKING state (the wake script writes state=WORKING just before invoking
    the agent).

    When ``max_task_seconds <= 0`` the check is disabled and the function
    emits nothing.

    Designed to surface long-running tasks BEFORE the watchdog kill so the
    operator can notice and intervene.  Called from dashboard-attention.sh
    (window 2 attention surface) and rendered in window-0 right column.

    Args:
        tasks_root:        Path to the tasks directory to scan.
        use_color:         When True, emit ANSI color codes and Unicode symbols.
        max_task_seconds:  Age threshold in seconds.  Tasks older than this
                           are highlighted as attention-worthy.
    """
    if max_task_seconds <= 0:
        return

    tasks_root_path = pathlib.Path(tasks_root)

    # ANSI codes — same palette as scan_blocked_tasks for visual consistency.
    RESET  = "\033[0m"    if use_color else ""
    C_YEL  = "\033[0;33m" if use_color else ""
    C_BOLD = "\033[1m"    if use_color else ""
    C_DIM  = "\033[2m"    if use_color else ""
    C_CYAN = "\033[0;36m" if use_color else ""
    CLOCK  = "⏰" if use_color else "~"  # alarm clock symbol

    found_any = False
    now = int(time.time())

    if tasks_root_path.is_dir():
        for task_dir in sorted(tasks_root_path.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            if task_id in {"archive", "queues", "plans"}:
                continue
            status_file = task_dir / "status.md"
            if not status_file.is_file():
                continue
            try:
                status_text = status_file.read_text(errors="replace")
            except OSError:
                continue

            state = _read_field(status_text, "State").upper()
            if state != "WORKING":
                continue

            # Age check using status.md mtime.
            try:
                mtime = int(status_file.stat().st_mtime)
                age_secs = now - mtime
            except (OSError, OverflowError, ValueError):
                continue

            if age_secs <= max_task_seconds:
                continue

            found_any = True
            elapsed_str = _format_elapsed(age_secs)
            started_at = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

            # Extract summary and role for context.
            summary_lines = _read_section(status_text, "Summary", max_lines=2)
            role = _read_field(status_text, "Role") or ""

            role_str = f" [{role}]" if role else ""
            print(
                f"\n{CLOCK} {C_YEL}{C_BOLD}{task_id}{RESET}"
                f"{C_DIM}{role_str}{RESET}"
            )
            print(
                f"  Still WORKING since: {C_DIM}{started_at} ({elapsed_str})"
                f" — age {age_secs}s > threshold {max_task_seconds}s{RESET}"
            )
            if summary_lines:
                print(f"  {C_CYAN}Summary:{RESET}")
                for line in summary_lines:
                    print(f"    {line}")
            print("")

    if not found_any:
        print(
            f"\n  {C_DIM}(no stale WORKING tasks — "
            f"all active tasks within {max_task_seconds}s threshold){RESET}"
        )
        print("")


# ---------------------------------------------------------------------------
# Pending-approvals attention stratum
# ---------------------------------------------------------------------------

_APPROVAL_PENDING_STATES = frozenset({"WAITING", "BACKLOG"})
_APPROVAL_SKIP_DIRS = frozenset({"archive", "queues", "plans"})


def scan_pending_approvals_attention(kanban_root: str, use_color: bool) -> None:
    """Scan all registered projects for pending HUMAN-APPROVE tasks and render them.

    Renders each pending HUMAN-APPROVE gate task (state WAITING or BACKLOG) as a
    needs-human row in the attention pane, using the raised-hand (✋) class and a
    project label.  Designed to appear above the OVERWATCH ledger in attention.sh.

    Each pending gate is identified by a task ID that starts with "HUMAN-APPROVE"
    under any registered project's tasks/ directory.

    Output format per gate (with color):
        ✋  HUMAN-APPROVE-v1.10.0-001  [proj-name]
           RC: v1.10.0
           Approve: scripts/close.sh --project proj-name --key HUMAN-APPROVE-v1.10.0-001
           Reject:  scripts/wontdo.sh --project proj-name --key HUMAN-APPROVE-v1.10.0-001

    When no pending gates exist, emits a "(no pending approvals)" message so the
    section is always present in the pane.

    Args:
        kanban_root: Path to the kanban root directory.
        use_color:   When True, emit ANSI color codes and Unicode symbols.
    """
    kanban_root_path = pathlib.Path(kanban_root)

    # ANSI codes — same palette as scan_blocked_tasks for visual consistency.
    RESET  = "\033[0m"    if use_color else ""
    C_RED  = "\033[0;31m" if use_color else ""
    C_BOLD = "\033[1m"    if use_color else ""
    C_DIM  = "\033[2m"    if use_color else ""
    C_CYAN = "\033[0;36m" if use_color else ""
    C_YEL  = "\033[0;33m" if use_color else ""
    C_GRN  = "\033[0;32m" if use_color else ""
    HAND   = "✋" if use_color else "!"  # ✋ raised hand

    found_any = False

    for proj_name, proj_root in _iter_projects(kanban_root_path):
        tasks_dir = proj_root / "tasks"
        if not tasks_dir.is_dir():
            continue

        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            if task_id in _APPROVAL_SKIP_DIRS:
                continue
            if not task_id.startswith("HUMAN-APPROVE"):
                continue

            status_file = task_dir / "status.md"
            if not status_file.is_file():
                continue

            try:
                status_text = status_file.read_text(errors="replace")
            except OSError:
                continue

            state = _read_field(status_text, "State").upper()
            if state not in _APPROVAL_PENDING_STATES:
                continue

            found_any = True

            # Read release version from README for context label.
            readme_file = task_dir / "README.md"
            release_ver = task_id  # fallback
            if readme_file.is_file():
                try:
                    readme_text = readme_file.read_text(errors="replace")
                    rv = _read_field(readme_text, "Release Version")
                    if rv:
                        release_ver = rv
                except OSError:
                    pass

            # Header row: ✋ task-id [project]
            print(
                f"\n{HAND} {C_RED}{C_BOLD}{task_id}{RESET}"
                f"  {C_DIM}[{proj_name}]{RESET}"
            )
            print(
                f"   RC: {C_CYAN}{release_ver}{RESET}"
                f"   state: {C_DIM}{state}{RESET}"
            )
            # Approve / reject commands
            print(
                f"   {C_GRN}Approve:{RESET}  "
                f"scripts/close.sh --project {proj_name} --key {task_id}"
            )
            print(
                f"   {C_RED}Reject: {RESET}  "
                f"scripts/wontdo.sh --project {proj_name} --key {task_id}"
            )
            print("")

    if not found_any:
        print(f"\n  {C_DIM}(no pending approvals){RESET}")
    print("")


# ---------------------------------------------------------------------------
# OVERWATCH section scanner
# ---------------------------------------------------------------------------

# Action names from check-transient-api-error.sh that map to TRANSIENT type.
_OVERWATCH_TRANSIENT_ACTIONS = frozenset({
    "transient-auto-requeued",
    "dry-run-transient-would-requeue",
})

# Action names that map to needs-human type (ceiling reached, bug filed, etc.)
_OVERWATCH_NEEDS_HUMAN_ACTIONS = frozenset({
    "transient-ceiling-bug-filed",
    "dry-run-transient-ceiling-would-bug-file",
    "worktree-carries-commits-bug-filed",
    "bug-filed",
    "auto-fix-aborted",
})


def _parse_overwatch_action_log(
    log_path: pathlib.Path,
    max_entries: int,
) -> list[dict]:
    """Parse the OVERWATCH action log and return a list of structured entry dicts.

    Each entry dict contains:
        timestamp (str):  ISO 8601 timestamp as recorded
        name      (str):  detection script name (e.g. "check-transient-api-error")
        target    (str):  affected resource (task ID, path, or project name)
        action    (str):  what was done (e.g. "transient-auto-requeued")
        backup    (str):  backup path or "none"
        reason    (str):  human-readable explanation
        type      (str):  "TRANSIENT", "needs-human", or "info"

    Records are returned in reverse-chronological order (newest first),
    capped at max_entries.  Malformed lines (< 6 tab-separated fields) are
    silently skipped.

    Args:
        log_path:    Path to the actions.log file.
        max_entries: Maximum number of entries to return (0 = unlimited).
    """
    if not log_path.is_file():
        return []

    entries: list[dict] = []
    try:
        raw_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for line in reversed(raw_lines):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        ts, name, target, action, backup, reason = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        )

        # Determine type from action name.
        if action in _OVERWATCH_TRANSIENT_ACTIONS:
            item_type = "TRANSIENT"
        elif action in _OVERWATCH_NEEDS_HUMAN_ACTIONS:
            item_type = "needs-human"
        else:
            item_type = "info"

        entries.append({
            "timestamp": ts,
            "name": name,
            "target": target,
            "action": action,
            "backup": backup,
            "reason": reason,
            "type": item_type,
        })

        if max_entries > 0 and len(entries) >= max_entries:
            break

    return entries


def scan_overwatch_section(
    kanban_root: str,
    use_color: bool,
    max_entries: int = 50,
) -> None:
    """Scan per-project OVERWATCH action logs and print an OVERWATCH section.

    Reads ``kanban_root/projects/<name>/overwatch/actions.log`` for each
    registered project.  Renders the most recent OVERWATCH actions grouped by
    project, with TRANSIENT items (type="TRANSIENT") visually distinct from
    needs-human items (type="needs-human").

    The ``type`` field in the structured data is the machine-readable
    discriminator: "TRANSIENT" for tasks auto-requeued after a transient API
    error, "needs-human" for anomalies that require operator attention (ceiling
    reached, bug filed, etc.), and "info" for routine sweep events.

    Only TRANSIENT and needs-human entries are rendered; info entries
    (sweep-start, sweep-end, check-ok) are suppressed in the display.

    When no OVERWATCH data exists for any project, emits a "(no recent
    OVERWATCH activity)" message so the section is always present.

    Args:
        kanban_root:  Path to the kanban root directory.
        use_color:    When True, emit ANSI color codes and Unicode symbols.
        max_entries:  Maximum action log entries to read per project (default 50).
    """
    kanban_root_path = pathlib.Path(kanban_root)

    # ANSI codes — cyan for TRANSIENT (retryable), red for needs-human (alarm).
    RESET  = "\033[0m"    if use_color else ""
    C_RED  = "\033[0;31m" if use_color else ""
    C_CYAN = "\033[0;36m" if use_color else ""
    C_BOLD = "\033[1m"    if use_color else ""
    C_DIM  = "\033[2m"    if use_color else ""
    C_YEL  = "\033[0;33m" if use_color else ""
    SHIELD = "\U0001f6e1" if use_color else "W"  # shield symbol for OVERWATCH

    found_any = False

    for proj_name, proj_root in _iter_projects(kanban_root_path):
        log_path = proj_root / "overwatch" / "actions.log"
        entries = _parse_overwatch_action_log(log_path, max_entries)

        # Keep only TRANSIENT and needs-human entries for display.
        visible = [e for e in entries if e["type"] in ("TRANSIENT", "needs-human")]
        if not visible:
            continue

        found_any = True

        proj_upper = proj_name.upper().replace("-", "_")
        print(f"\n  {C_BOLD}{proj_upper}{RESET}")

        for entry in visible:
            ts       = entry["timestamp"]
            target   = entry["target"]
            action   = entry["action"]
            reason   = entry["reason"]
            etype    = entry["type"]

            if etype == "TRANSIENT":
                # Cyan palette: retryable, not alarming.
                label_color = C_CYAN
                label       = "TRANSIENT"
                arrow       = "⟳" if use_color else ">"
            else:
                # Red palette: needs operator attention.
                label_color = C_RED
                label       = "needs-human"
                arrow       = "⚠" if use_color else "!"

            print(
                f"\n  {arrow} {label_color}{C_BOLD}{label}{RESET}"
                f"  {C_DIM}{ts}{RESET}"
            )
            print(f"    target: {target}")
            print(f"    action: {action}")
            if reason and reason != "none":
                # Truncate long reason lines for display.
                reason_display = reason[:100] + "…" if len(reason) > 100 else reason
                print(f"    {C_YEL}reason:{RESET} {reason_display}")
            print("")

    if not found_any:
        print(f"\n  {C_DIM}(no recent OVERWATCH activity){RESET}")
    print("")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point: select blocked-tasks, quarantine, or rejected-files scanner."""
    parser = argparse.ArgumentParser(
        description=(
            "Attention scanners for the pgai kanban dashboard. "
            "Scans for BLOCKED tasks, quarantine alerts, or quarantined files."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- blocked-tasks subcommand ---
    p_blocked = subparsers.add_parser(
        "blocked-tasks",
        help="Scan task directories for BLOCKED state.",
    )
    p_blocked.add_argument(
        "tasks_root",
        help="Path to the tasks directory to scan.",
    )
    color_group_b = p_blocked.add_mutually_exclusive_group()
    color_group_b.add_argument(
        "--color",
        dest="use_color",
        action="store_true",
        default=True,
        help="Enable ANSI color output (default).",
    )
    color_group_b.add_argument(
        "--no-color",
        dest="use_color",
        action="store_false",
        help="Disable ANSI color output.",
    )

    # --- quarantine subcommand ---
    p_quarantine = subparsers.add_parser(
        "quarantine",
        help="Scan project directories for quarantine alerts.",
    )
    p_quarantine.add_argument(
        "kanban_root",
        help="Path to the kanban root directory.",
    )
    color_group_q = p_quarantine.add_mutually_exclusive_group()
    color_group_q.add_argument(
        "--color",
        dest="use_color",
        action="store_true",
        default=True,
        help="Enable ANSI color output (default).",
    )
    color_group_q.add_argument(
        "--no-color",
        dest="use_color",
        action="store_false",
        help="Disable ANSI color output.",
    )
    p_quarantine.add_argument(
        "--threshold",
        type=int,
        default=3,
        metavar="N",
        help="Rejection count at which a file is quarantined (default: 3).",
    )

    # --- rejected-files subcommand ---
    p_rejected = subparsers.add_parser(
        "rejected-files",
        help="Scan project rejected/ directories and print QUARANTINED FILES section.",
    )
    p_rejected.add_argument(
        "kanban_root",
        help="Path to the kanban root directory.",
    )
    color_group_r = p_rejected.add_mutually_exclusive_group()
    color_group_r.add_argument(
        "--color",
        dest="use_color",
        action="store_true",
        default=True,
        help="Enable ANSI color output (default).",
    )
    color_group_r.add_argument(
        "--no-color",
        dest="use_color",
        action="store_false",
        help="Disable ANSI color output.",
    )

    # --- stale-working-tasks subcommand (D4, v0.64.0) ---
    p_stale = subparsers.add_parser(
        "stale-working-tasks",
        help=(
            "Scan task directories for WORKING tasks whose age exceeds "
            "max-task-seconds.  Shows them as attention-worthy BEFORE the "
            "watchdog kill lands."
        ),
    )
    p_stale.add_argument(
        "tasks_root",
        help="Path to the tasks directory to scan.",
    )
    color_group_s = p_stale.add_mutually_exclusive_group()
    color_group_s.add_argument(
        "--color",
        dest="use_color",
        action="store_true",
        default=True,
        help="Enable ANSI color output (default).",
    )
    color_group_s.add_argument(
        "--no-color",
        dest="use_color",
        action="store_false",
        help="Disable ANSI color output.",
    )
    p_stale.add_argument(
        "--max-task-seconds",
        type=int,
        default=5400,
        metavar="N",
        help=(
            "Age threshold in seconds.  WORKING tasks older than this are "
            "shown as attention-worthy.  0 disables the check (default: 5400)."
        ),
    )

    # --- transient-tasks subcommand ---
    p_transient = subparsers.add_parser(
        "transient-tasks",
        help=(
            "Scan task directories for BLOCKED tasks caused by transient API "
            "errors (e.g. 529 Overloaded).  These tasks have Needs Human=no "
            "and will be auto-retried on the next wake."
        ),
    )
    p_transient.add_argument(
        "tasks_root",
        help="Path to the tasks directory to scan.",
    )
    color_group_t = p_transient.add_mutually_exclusive_group()
    color_group_t.add_argument(
        "--color",
        dest="use_color",
        action="store_true",
        default=True,
        help="Enable ANSI color output (default).",
    )
    color_group_t.add_argument(
        "--no-color",
        dest="use_color",
        action="store_false",
        help="Disable ANSI color output.",
    )

    # --- pending-approvals subcommand ---
    p_pending = subparsers.add_parser(
        "pending-approvals",
        help=(
            "Scan all registered projects for pending HUMAN-APPROVE gate tasks "
            "and render them in the needs-human stratum of the attention pane.  "
            "Each row shows the task ID, project label, RC version, and the "
            "approve/reject commands.  Positioned above the OVERWATCH ledger."
        ),
    )
    p_pending.add_argument(
        "kanban_root",
        help="Path to the kanban root directory.",
    )
    color_group_pa = p_pending.add_mutually_exclusive_group()
    color_group_pa.add_argument(
        "--color",
        dest="use_color",
        action="store_true",
        default=True,
        help="Enable ANSI color output (default).",
    )
    color_group_pa.add_argument(
        "--no-color",
        dest="use_color",
        action="store_false",
        help="Disable ANSI color output.",
    )

    # --- overwatch-section subcommand ---
    p_overwatch = subparsers.add_parser(
        "overwatch-section",
        help=(
            "Scan per-project OVERWATCH action logs and print an OVERWATCH "
            "section.  TRANSIENT items (auto-requeued transient API errors) "
            "are rendered in a distinct style from needs-human items (ceiling "
            "reached, bug filed, etc.)."
        ),
    )
    p_overwatch.add_argument(
        "kanban_root",
        help="Path to the kanban root directory.",
    )
    color_group_ow = p_overwatch.add_mutually_exclusive_group()
    color_group_ow.add_argument(
        "--color",
        dest="use_color",
        action="store_true",
        default=True,
        help="Enable ANSI color output (default).",
    )
    color_group_ow.add_argument(
        "--no-color",
        dest="use_color",
        action="store_false",
        help="Disable ANSI color output.",
    )
    p_overwatch.add_argument(
        "--max-entries",
        type=int,
        default=50,
        metavar="N",
        help=(
            "Maximum action log entries to read per project (default: 50). "
            "0 means unlimited."
        ),
    )

    args = parser.parse_args()

    if args.command == "blocked-tasks":
        scan_blocked_tasks(tasks_root=args.tasks_root, use_color=args.use_color)
    elif args.command == "quarantine":
        scan_quarantine(
            kanban_root=args.kanban_root,
            use_color=args.use_color,
            threshold=args.threshold,
        )
    elif args.command == "rejected-files":
        scan_rejected(kanban_root=args.kanban_root, use_color=args.use_color)
    elif args.command == "stale-working-tasks":
        scan_stale_working_tasks(
            tasks_root=args.tasks_root,
            use_color=args.use_color,
            max_task_seconds=args.max_task_seconds,
        )
    elif args.command == "transient-tasks":
        scan_transient_tasks(tasks_root=args.tasks_root, use_color=args.use_color)
    elif args.command == "pending-approvals":
        scan_pending_approvals_attention(
            kanban_root=args.kanban_root,
            use_color=args.use_color,
        )
    elif args.command == "overwatch-section":
        scan_overwatch_section(
            kanban_root=args.kanban_root,
            use_color=args.use_color,
            max_entries=args.max_entries,
        )


if __name__ == "__main__":
    main()

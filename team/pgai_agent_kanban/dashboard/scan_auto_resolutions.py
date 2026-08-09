#!/usr/bin/env python3
"""
scan_auto_resolutions.py — Read-only renderer for the Auto-Resolutions
section of the human-review tmux window.

Reads the append-only auto-resolutions ledger
(``projects/<name>/auto-resolutions.md``, written by
``pgai_agent_kanban.overwatch.ledger``) for every registered project and
prints a listing of OVERWATCH's autonomous resolution events: RC, attempt
N/M, decision, outcome, and evidence links.

This module is READ-ONLY: it never writes to the ledger, never touches a
HALT file, and offers no approve/reject affordance — the surface informs,
it never mutates state.

Each row includes:
  - project name
  - RC (target version the resolution acted on)
  - attempt N/M — the attempt count at which the block occurred (N).
    over the project's configured retry ceiling (M, resolved the same way
    OVERWATCH's resolution stage resolves it: project.cfg then kanban.cfg
    ``[release] max_rc_retries``). M renders as "?" when the ceiling
    cannot be resolved (missing config) rather than raising — a read-only
    display must degrade gracefully, unlike the resolver itself which
    fails loud (see resolution_matrix.CeilingConfigError).
  - decision — the matrix row matched (e.g. "CANCEL-AND-REBUNDLE")
  - outcome — backfilled terminal state ("SHIPPED attempt N" /
    "CEILING-BLOCKED"), or "pending" while the bundle family has not yet
    reached a terminal state
  - evidence links — the entry's recorded evidence paths (TESTER report,
    ship-policy HALT file, release-state file, ...) plus a pointer to the
    ledger entry itself, never duplicated content (the ledger is the
    index, not the record — same constraint the ledger writer itself
    observes)

Empty state: one line — "no auto-resolutions."

Usage (CLI):
    python3 team/pgai_agent_kanban/dashboard/scan_auto_resolutions.py \\
        <kanban_root> [--color | --no-color]

Usage (import — human-review window renderer):
    from pgai_agent_kanban.dashboard.scan_auto_resolutions import (
        scan_auto_resolutions,
    )
    scan_auto_resolutions(kanban_root="/path/to/kanban", use_color=True)

Usage (import — structured data, for the sibling browser Auto-Resolutions
tab or any other read-only consumer):
    from pgai_agent_kanban.dashboard.scan_auto_resolutions import (
        collect_auto_resolutions,
    )
    records = collect_auto_resolutions(kanban_root="/path/to/kanban")
    # returns list[dict] — see collect_auto_resolutions docstring for field spec.

Exit codes:
    0 — always (errors reading/parsing an individual project's ledger or
        resolving its retry ceiling are contained to that project's rows;
        they never abort the scan for other projects).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

# Make the parent-of-pgai_agent_kanban directory importable regardless of
# how this module is invoked (mirrors the CLI PYTHONPATH the dashboard
# shell wrapper sets; this fallback covers direct `python3 <path>` runs).
_PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from pgai_agent_kanban.overwatch.ledger import (  # noqa: E402
    ledger_path_for_project,
    parse_entries,
)
from pgai_agent_kanban.overwatch.resolution_matrix import (  # noqa: E402
    CeilingConfigError,
    resolve_max_rc_retries,
)


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------


def _iter_project_names(kanban_root: pathlib.Path):
    """Yield project names registered under kanban_root/projects/.

    Mirrors the discovery style in scan_human_approvals._iter_projects,
    but ledger_path_for_project() always assumes the multi-project
    "projects/<name>/auto-resolutions.md" layout (see ledger.py), so there
    is no single-project fallback here — a project with no `projects/`
    directory has no ledger to read.
    """
    projects_dir = kanban_root / "projects"
    if not projects_dir.is_dir():
        return
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        yield entry.name


# ---------------------------------------------------------------------------
# Ceiling resolution — degrade gracefully for a read-only display
# ---------------------------------------------------------------------------


def _resolve_ceiling_display(project_name: str, kanban_root: pathlib.Path) -> str:
    """Return the project's max_rc_retries as a string, or "?" if unresolvable.

    resolve_max_rc_retries() fails loud (CeilingConfigError) by design for
    the resolver, which must never silently substitute a default. This
    display path is different: a missing ceiling config must not blank out
    an otherwise-readable ledger entry, so it falls back to "?" instead of
    propagating the exception.
    """
    try:
        return str(resolve_max_rc_retries(project_name, str(kanban_root)))
    except CeilingConfigError:
        return "?"


# ---------------------------------------------------------------------------
# Data collection — internal helper
# ---------------------------------------------------------------------------


def _collect_auto_resolutions(kanban_root: pathlib.Path) -> list[dict]:
    """Return auto-resolution ledger entries across all registered projects.

    Each dict contains:
      project        — project name
      rc             — RC version the resolution acted on
      attempt_from   — attempt count at which the block occurred (N)
      attempt_to     — attempt count the resolution transitions to (N+1)
      ceiling        — project's configured max_rc_retries (M), or "?"
      attempt_display — "N/M" string (attempt_from over ceiling)
      decision       — matrix row matched (e.g. "CANCEL-AND-REBUNDLE")
      outcome        — backfilled outcome, or "pending"
      trigger        — the block trigger class
      findings       — list of bug-report identifiers
      evidence_paths — list of evidence path strings recorded on the entry
      ledger_path    — path to the project's auto-resolutions.md
      entry_id       — the entry's identifier (for the ledger link)
      family_key     — bundle family key, or None
      timestamp      — ISO-8601 timestamp string recorded on the entry

    Newest-first within each project (ledger append order is chronological
    ascending; this reverses it — same "most recent first" convention used
    by the other dashboard panes, e.g. git-recent-tags.sh, scan_attention.py).
    Projects are ordered alphabetically.

    Malformed or missing ledgers are silently skipped for that project;
    a scan failure in one project's ledger never blanks out another
    project's rows.
    """
    results: list[dict] = []

    for project_name in _iter_project_names(kanban_root):
        ledger_path = ledger_path_for_project(str(kanban_root), project_name)
        try:
            entries = parse_entries(ledger_path)
        except OSError:
            continue
        if not entries:
            continue

        ceiling = _resolve_ceiling_display(project_name, kanban_root)

        for entry in reversed(entries):
            attempt_from = entry.get("attempt_from")
            attempt_display = f"{attempt_from}/{ceiling}" if attempt_from is not None else f"?/{ceiling}"
            results.append({
                "project": project_name,
                "rc": entry.get("rc") or "unknown",
                "attempt_from": attempt_from,
                "attempt_to": entry.get("attempt_to"),
                "ceiling": ceiling,
                "attempt_display": attempt_display,
                "decision": entry.get("matrix_row") or "unknown",
                "outcome": entry.get("outcome") or "pending",
                "trigger": entry.get("trigger") or "unknown",
                "findings": entry.get("findings") or [],
                "evidence_paths": entry.get("evidence_paths") or [],
                "ledger_path": ledger_path,
                "entry_id": entry.get("entry_id") or "unknown",
                "family_key": entry.get("family_key"),
                "timestamp": entry.get("timestamp") or "unknown",
            })

    return results


# ---------------------------------------------------------------------------
# Public pure function — structured records for the browser tab (T9) and
# any other read-only consumer
# ---------------------------------------------------------------------------


def collect_auto_resolutions(
    kanban_root: str,
    project: str | None = None,
) -> list[dict]:
    """Return auto-resolution ledger records as structured data.

    Pure function: no print output. Both the human-review-window renderer
    (scan_auto_resolutions) and any future read-only API/browser-tab
    consumer draw from this one implementation.

    Args:
        kanban_root: Path to the kanban root directory (string or path-like).
        project:     When supplied, return only records for this project
                     name. When None, return records for ALL projects.

    Returns:
        List of dicts — see _collect_auto_resolutions docstring for field
        spec. Empty list when no project has any resolution events yet
        (the good news).
    """
    root = pathlib.Path(kanban_root)
    records = _collect_auto_resolutions(root)
    if project is not None:
        records = [r for r in records if r["project"] == project]
    return records


# ---------------------------------------------------------------------------
# human-review-window renderer
# ---------------------------------------------------------------------------


def scan_auto_resolutions(kanban_root: str, use_color: bool = True) -> None:
    """Print the Auto-Resolutions listing for the human-review tmux window.

    Empty state: prints exactly one line — "no auto-resolutions."

    Each entry renders (in order):
      - Header: RC and project label
      - attempt N/M, decision, outcome
      - Evidence: block of recorded evidence paths (or "none")
      - Ledger: path to the entry's source ledger file + entry id

    Read-only: no action affordances are ever printed (no approve/reject/
    close/wontdo commands) — this surface informs, it never mutates state.
    """
    root = pathlib.Path(kanban_root)

    RESET  = "\033[0m"    if use_color else ""
    C_BOLD = "\033[1m"    if use_color else ""
    C_DIM  = "\033[2m"    if use_color else ""
    C_CYAN = "\033[0;36m" if use_color else ""
    C_YEL  = "\033[0;33m" if use_color else ""
    C_BLU  = "\033[0;34m" if use_color else ""
    C_GRN  = "\033[0;32m" if use_color else ""
    GEAR   = "⚙"     if use_color else "*"  # gear

    records = _collect_auto_resolutions(root)

    if not records:
        print(f"  {C_DIM}no auto-resolutions.{RESET}")
        return

    for r in records:
        proj = r["project"]
        rc = r["rc"]
        outcome = r["outcome"]
        outcome_color = C_DIM if outcome == "pending" else C_GRN

        # Header row: gear RC [project]
        print(
            f"{GEAR} {C_BOLD}{C_CYAN}{rc}{RESET}"
            f"  {C_DIM}[{proj}]{RESET}"
        )
        print(
            f"  attempt: {C_YEL}{r['attempt_display']}{RESET}"
            f"   decision: {C_BLU}{r['decision']}{RESET}"
            f"   outcome: {outcome_color}{outcome}{RESET}"
        )

        # Evidence links
        if r["evidence_paths"]:
            print(f"  {C_DIM}Evidence:{RESET}")
            for path in r["evidence_paths"]:
                print(f"    {path}")
        else:
            print(f"  {C_DIM}Evidence: none{RESET}")

        # Ledger entry pointer (evidence link to the resolution decision itself)
        print(f"  {C_DIM}Ledger:{RESET}  {r['ledger_path']}  (entry {r['entry_id']})")
        print("")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan for OVERWATCH auto-resolution ledger entries (human-review window).",
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

    scan_auto_resolutions(args.kanban_root, use_color=use_color)


if __name__ == "__main__":
    _main()

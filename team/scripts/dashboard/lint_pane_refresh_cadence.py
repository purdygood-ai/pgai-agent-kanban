#!/usr/bin/env python3
"""
lint_pane_refresh_cadence.py
=============================
Census check: every dashboard pane's FIRST command -- the ones
team/scripts/dashboard/create.sh dispatches through
`dashboard_send_armed_keys` (see team/scripts/dashboard/lib/
pane_readiness.sh) -- must be either:

  1. loop-wrapped -- routed through team/scripts/dashboard/cadence-loop.sh,
     the shared visibility-aware refresh cadence every renderer column
     uses (see cadence-loop.sh's own header for the design), or
  2. explicitly registered in create.sh's CENSUS-EXEMPT-PANES data block
     (the DASHBOARD_ONE_SHOT_STATIC_PANES list) as a pane that does not
     need that wrap: a script whose own command already streams forever
     without needing a re-invoking loop (one that `exec`s into a
     follow-mode `tail -F`, e.g. logs.sh, debug-logs.sh, cron-tail.sh), or
     a pane that is legitimately interactive by design (the
     operator/terminal login shells).

It also checks the census itself (team/config/dashboard_windows.census):
every pane must declare, in its C record, that it pauses while hidden --
or be the one always-on status surface in window 0
(pgai_agent_kanban.dashboard.window_census.pane_hidden_declaration_findings).
A pane with no declaration, or any other declaration, is reported.

A pane meeting neither is a BARE RENDERER INVOCATION: create.sh types it
once at pane creation, it prints its output and returns, and the pane's
shell falls through to an interactive prompt underneath -- a shell prompt
and the echoed command sitting in a dashboard pane. This is the exact
shape window 4's five per-agent schedule panes shipped in before this
check existed: dashboard_send_armed_keys wraps the trap/armed-marker
preamble around them correctly, but the render itself was never wrapped
in the refresh loop every other renderer column uses.

This script parses create.sh as TEXT -- it never sources or executes it,
so a pane target built from a runtime variable (e.g.
`operator.${OP_SHELL_PANE}`, or the shared board dispatcher's
`${board_win}.0`) is classified by its literal,
un-expanded source form; the CENSUS-EXEMPT-PANES data block (see below)
registers pane targets the same way, so the two sides compare like for
like without needing a real bash evaluation.

Usage:
    python3 lint_pane_refresh_cadence.py [--create-sh PATH] [--census PATH]

Exit codes:
    0   every pane command is loop-wrapped or explicitly registered, and
        every census pane declares what it does while hidden.
    1   one or more bare renderer invocations or census declaration
        findings (CONVERT -- see the refusal site below for the
        classification rationale).
    2   usage error, or create.sh or the census could not be read/parsed.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Dev-tree root resolution -- each tree finds itself.
# This file lives at team/scripts/dashboard/lint_pane_refresh_cadence.py.
# Three parent levels: dashboard/ -> scripts/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.dashboard.window_census import (  # noqa: E402
    CensusFormatError,
    default_census_path,
    pane_hidden_declaration_findings,
)

_DEFAULT_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"

_LOOP_WRAPPER_MARKER = "cadence-loop.sh"

# Matches a `dashboard_send_armed_keys "<target>" "<cmd>"` call site,
# tolerating the two-line continuation shape (target on one line ending
# in a trailing backslash, the command quoted on the next) that
# debug-logs.0 and training-logs.0 use -- the whitespace/backslash/newline
# between the two quoted strings
# is liberal on purpose (Postel's law: this reads generated bash text, it
# does not produce any).
_SEND_KEYS_RE = re.compile(
    r'dashboard_send_armed_keys\s+"([^"]+)"\s*(?:\\\s*\n\s*)?"([^"]+)"',
    re.MULTILINE,
)

# Matches a single-line `VARNAME="value"` assignment, tolerating leading
# indentation (the per-project drill-window commands are assigned inside
# an indented loop body) -- every *_CMD variable in create.sh is built
# this way (verified: none span multiple lines or embed an escaped double
# quote).
_ASSIGNMENT_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)="([^"\n]*)"\s*$', re.MULTILINE)

# A command argument that is exactly one variable reference and nothing
# else, e.g. "${SCHED_STATUS_CMD}" -- resolved via the assignment table.
# A command argument with any other shape (a literal string, possibly
# itself containing other ${...} interpolations) is used as-is.
_BARE_VAR_REF_RE = re.compile(r'^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$')

# The explicit one-shot-static / self-refreshing registry inside create.sh
# -- a plain-text data block (never executed bash, deliberately, so a
# pane-target expression can name a variable this script assigns further
# down without ever tripping `set -u`), one pane-target string per
# comment line between these two markers.
_ALLOWLIST_BEGIN = "# CENSUS-EXEMPT-PANES:BEGIN"
_ALLOWLIST_END = "# CENSUS-EXEMPT-PANES:END"


class PaneRefreshLintError(Exception):
    """Raised when create.sh cannot be parsed well enough to classify its
    pane commands (a missing allowlist array, or no send-keys call sites
    found at all -- both signal a structural problem, not a compliance
    finding)."""


def _resolve_command(raw_cmd_arg: str, assignments: dict[str, str]) -> str:
    """Return the text a pane command should be classified against: the
    variable's assigned value when raw_cmd_arg is a bare ${VAR} reference,
    else raw_cmd_arg itself (already a literal command string)."""
    m = _BARE_VAR_REF_RE.match(raw_cmd_arg)
    if not m:
        return raw_cmd_arg
    var_name = m.group(1)
    return assignments.get(var_name, raw_cmd_arg)


def _parse_allowlist(create_sh_text: str) -> set[str]:
    """Return the set of pane-target strings named between the
    CENSUS-EXEMPT-PANES markers, one per non-blank comment line, with the
    leading '#' and surrounding whitespace stripped. Raises
    PaneRefreshLintError if the marker pair is missing entirely (a
    structural problem: this lint cannot distinguish a genuinely-exempt
    pane from a bare renderer invocation without it) or malformed (only
    one of the two markers present)."""
    begin_idx = create_sh_text.find(_ALLOWLIST_BEGIN)
    end_idx = create_sh_text.find(_ALLOWLIST_END)
    if begin_idx == -1 and end_idx == -1:
        raise PaneRefreshLintError(
            f"no {_ALLOWLIST_BEGIN!r} / {_ALLOWLIST_END!r} marker pair found "
            "-- every continuously self-refreshing or intentionally static "
            "pane must be registered there, or this lint cannot tell it "
            "apart from a bare renderer invocation"
        )
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        raise PaneRefreshLintError(
            f"found only one of the {_ALLOWLIST_BEGIN!r} / {_ALLOWLIST_END!r} "
            "marker pair (or found them out of order) -- both must be "
            "present, in order, around the exempt-pane list"
        )
    block = create_sh_text[begin_idx + len(_ALLOWLIST_BEGIN) : end_idx]

    allowlist: set[str] = set()
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("#"):
            continue
        entry = line.lstrip("#").strip()
        if entry:
            allowlist.add(entry)
    return allowlist


def find_violations(create_sh_text: str) -> list[tuple[str, str]]:
    """Return a list of (pane_target, resolved_command) pairs for every
    pane whose first command is neither loop-wrapped nor explicitly
    registered as one-shot-static. Raises PaneRefreshLintError if the file
    carries no send-keys call sites or no allowlist array at all (a
    parsing failure, distinct from zero violations)."""
    send_calls = _SEND_KEYS_RE.findall(create_sh_text)
    if not send_calls:
        raise PaneRefreshLintError(
            "no dashboard_send_armed_keys call sites found -- create.sh may "
            "have moved away from this dispatch helper, or the input isn't "
            "create.sh at all"
        )

    allowlist = _parse_allowlist(create_sh_text)

    assignments = dict(_ASSIGNMENT_RE.findall(create_sh_text))

    violations: list[tuple[str, str]] = []
    for pane_target, raw_cmd_arg in send_calls:
        resolved_cmd = _resolve_command(raw_cmd_arg, assignments)
        if _LOOP_WRAPPER_MARKER in resolved_cmd:
            continue
        if pane_target in allowlist:
            continue
        violations.append((pane_target, resolved_cmd))

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_pane_refresh_cadence.py",
        description=(
            "Verify every dashboard pane's first command is either "
            "cadence-loop.sh-wrapped or explicitly registered as "
            "one-shot-static in create.sh."
        ),
    )
    parser.add_argument("--create-sh", metavar="PATH", help="create.sh path.")
    parser.add_argument("--census", metavar="PATH", help="dashboard_windows.census path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the pane refresh-cadence lint over create.sh and the window census.

    Exit codes: 0 every pane command is loop-wrapped or registered and every
    census pane declares its hidden behaviour; 1 one or more findings
    (printed); 2 create.sh or the census could not be read or parsed, or a
    usage error.
    """
    args = _parse_args(argv)
    create_sh_path = pathlib.Path(args.create_sh) if args.create_sh else _DEFAULT_CREATE_SH

    try:
        text = create_sh_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"lint_pane_refresh_cadence: cannot read {create_sh_path}: {exc}", file=sys.stderr)
        return 2

    try:
        violations = find_violations(text)
    except PaneRefreshLintError as exc:
        print(f"lint_pane_refresh_cadence: {exc}", file=sys.stderr)
        return 2

    census_path = pathlib.Path(args.census) if args.census else default_census_path()
    try:
        census_findings = pane_hidden_declaration_findings(census_path)
    except (OSError, CensusFormatError) as exc:
        print(f"lint_pane_refresh_cadence: cannot check the census {census_path}: {exc}", file=sys.stderr)
        return 2

    if not violations and not census_findings:
        print(
            "lint_pane_refresh_cadence: OK -- every pane is loop-wrapped or registered, "
            "and every census pane declares what it does while hidden."
        )
        return 0

    for finding in census_findings:
        print(f"lint_pane_refresh_cadence: CENSUS -- {finding}", file=sys.stderr)

    for pane_target, resolved_cmd in violations:
        print(
            f"lint_pane_refresh_cadence: BARE RENDERER -- pane {pane_target!r} "
            f"is neither cadence-loop.sh-wrapped nor listed in the "
            f"CENSUS-EXEMPT-PANES block: {resolved_cmd}",
            file=sys.stderr,
        )

    # CONVERT: a pane command that is neither loop-wrapped nor registered
    # one-shot-static freezes that ONE pane's display at creation time
    # (a shell prompt and the echoed command sitting in a dashboard pane)
    # -- the rest of the dashboard, and every other window create.sh
    # builds, is unaffected. This refusal only ever stops a quality-class
    # pre-flight lint (a pytest fixture asserting this script's own exit
    # code against both the checked-in tree and a seeded bad fixture --
    # see team/tests/unit/test_dashboard_pane_refresh_cadence.py), never
    # the runner itself; see team/SOP-core.md "Gates and Refusals".
    emit_error_contract(
        "dashboard.pane-refresh-cadence-violation",
        (
            f"{len(violations)} dashboard pane(s) are neither "
            f"cadence-loop.sh-wrapped nor registered in the "
            f"CENSUS-EXEMPT-PANES block"
            + (": " + "; ".join(f"{p!r}" for p, _ in violations) if violations else "")
            + f"; {len(census_findings)} census pane declaration finding(s)"
            + (": " + "; ".join(census_findings) if census_findings else "")
        ),
        "for a census finding, give the pane a C record directly after its P "
        "record in team/config/dashboard_windows.census declaring 'pause' "
        "(see that file's header); for a bare renderer, "
        "wrap the pane's command through cadence-loop.sh (see the "
        "SCHED_STATUS_CMD / SCHED_EVENT_CMD variables in "
        "team/scripts/dashboard/create.sh for the pattern), or, if the "
        "pane is genuinely continuous "
        "(execs into a follow-mode `tail -F`) or intentionally static, "
        "add its exact pane-target string as a new comment line inside the "
        "CENSUS-EXEMPT-PANES:BEGIN / :END block in "
        "team/scripts/dashboard/create.sh.",
    )
    # CENSUS-ANCHOR: lint-pane-refresh-cadence-violation-01
    return 1


if __name__ == "__main__":
    sys.exit(main())

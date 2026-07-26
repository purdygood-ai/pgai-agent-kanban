#!/usr/bin/env python3
"""
lint_upgrade_no_temp_copy_guard.py
===================================
Pre-flight lint that asserts the retired temp-copy guard variables are absent
from executable lines in team/scripts/upgrade.sh.

WHY THIS EXISTS
---------------
The v1.7.0 two-phase upgrade architecture retired the self-overwrite guard
mechanism (_PGAI_UPGRADE_SELF_COPY, _PGAI_UPGRADE_REEXEC, _PGAI_UPGRADE_ORIG_DIR).
an earlier defect showed that a squash-merge conflict at the CM layer can silently resurrect
the retired block — TESTER saw the correct RC tree; CM's squash to ai_main
produced the wrong result for upgrade.sh.

This lint converts the one-time Goal-4 behavior-pattern grep-zero acceptance
criterion into a permanent, gated-suite check.  When a future merge restores the
retired block, this script exits 1 and the suite catches it before it reaches
production — the same role lint_api_parity plays for operator-flag parity.

WHAT IS CHECKED
---------------
The three retired identifiers:

  _PGAI_UPGRADE_SELF_COPY   — the mktemp self-copy path variable
  _PGAI_UPGRADE_REEXEC      — the re-exec flag variable
  _PGAI_UPGRADE_ORIG_DIR    — the original directory fallback variable

The check scans every line in upgrade.sh.  Lines whose first non-whitespace
character is '#' are comment lines — past-tense explanatory comments describing
the retirement are permitted.  Any non-comment line containing one of the three
identifiers is an executable hit and is reported as a violation.

Exit codes
----------
  0   No retired identifiers found as executable code; lint is green.
  1   One or more executable hits found; see stdout for details.
  2   Usage error or the target script was not found.

Usage
-----
  python3 scripts/lint_upgrade_no_temp_copy_guard.py [--upgrade-sh PATH] [--verbose]

  --upgrade-sh PATH  Path to upgrade.sh to scan.  Default: team/scripts/upgrade.sh
                     resolved from this script's own location.
  --verbose, -v      Print the number of lines scanned and which identifiers are
                     being checked.

Examples
--------
  # From repo root:
  python3 team/scripts/lint_upgrade_no_temp_copy_guard.py

  # With explicit path (useful for fixture-based tests):
  python3 team/scripts/lint_upgrade_no_temp_copy_guard.py --upgrade-sh /path/to/upgrade.sh
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# The three retired guard identifiers.  All three must be absent from executable
# lines in upgrade.sh.  Comment-only lines (stripped first char == '#') are
# exempt — they may describe the retirement in past tense.
# ---------------------------------------------------------------------------
RETIRED_IDENTIFIERS: tuple[str, ...] = (
    "_PGAI_UPGRADE_SELF_COPY",
    "_PGAI_UPGRADE_REEXEC",
    "_PGAI_UPGRADE_ORIG_DIR",
)


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------


def find_executable_hits(upgrade_sh_path: Path) -> list[tuple[int, str]]:
    """Scan upgrade.sh for retired guard identifiers on executable lines.

    Returns a list of (line_number, line_content) pairs for each violation.
    Comment-only lines (first non-whitespace character is '#') are skipped
    and never reported as violations.

    Raises FileNotFoundError if upgrade_sh_path does not exist.
    """
    if not upgrade_sh_path.exists():
        raise FileNotFoundError(
            f"upgrade.sh not found at {upgrade_sh_path}"
        )

    hits: list[tuple[int, str]] = []
    text = upgrade_sh_path.read_text(encoding="utf-8", errors="replace")

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            # Comment-only line — past-tense documentation is permitted.
            continue
        for identifier in RETIRED_IDENTIFIERS:
            if identifier in line:
                hits.append((lineno, line.rstrip()))
                break  # one hit per line is enough

    return hits


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_upgrade_no_temp_copy_guard.py",
        description=(
            "Pre-flight lint: asserts the retired temp-copy guard variables are "
            "absent from executable lines in team/scripts/upgrade.sh.  "
            "Exits 1 when any retired identifier appears on a non-comment line."
        ),
    )
    parser.add_argument(
        "--upgrade-sh",
        metavar="PATH",
        help=(
            "Path to the upgrade.sh file to scan.  Default: team/scripts/upgrade.sh "
            "resolved from this script's own directory."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print identifiers being checked and total lines scanned.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Resolve the upgrade.sh path.
    if args.upgrade_sh:
        upgrade_sh = Path(args.upgrade_sh).resolve()
    else:
        # Default: team/scripts/upgrade.sh relative to this script's location.
        script_dir = Path(__file__).resolve().parent   # team/scripts/
        upgrade_sh = script_dir / "upgrade.sh"

    if not upgrade_sh.exists():
        print(
            f"ERROR: upgrade.sh not found at {upgrade_sh}\n"
            "Use --upgrade-sh to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    if args.verbose:
        print(
            f"lint_upgrade_no_temp_copy_guard: scanning {upgrade_sh}",
            flush=True,
        )
        print(
            f"  checking for: {', '.join(RETIRED_IDENTIFIERS)}",
            flush=True,
        )

    try:
        hits = find_executable_hits(upgrade_sh)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if hits:
        print(
            f"\nlint_upgrade_no_temp_copy_guard: FAIL — "
            f"{len(hits)} retired guard identifier(s) found as executable code "
            f"in {upgrade_sh}:\n",
            flush=True,
        )
        for lineno, line in hits:
            print(f"  line {lineno}: {line}", flush=True)
        print(
            "\nThe two-phase upgrade architecture retired these identifiers.\n"
            "Their reappearance indicates a squash-merge conflict restored the "
            "pre-retirement block.  Remove the listed lines and re-run.",
            flush=True,
        )
        return 1

    total_lines = len(upgrade_sh.read_text(encoding="utf-8").splitlines())
    print(
        f"lint_upgrade_no_temp_copy_guard: ok — "
        f"{total_lines} lines scanned, no retired guard identifiers on executable lines.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

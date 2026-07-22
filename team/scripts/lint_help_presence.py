#!/usr/bin/env python3
"""
lint_help_presence.py
=====================
Lint gate: every operator-facing shell script under team/scripts/ must
implement a ``--help`` or ``-h`` flag that prints usage text.

Scripts that are exempt (dashboard-internal pane scripts, process-internal
helpers invoked by other scripts rather than directly by operators) are
listed in an exempt-list data file read at runtime.

Scope
-----
The scanner operates on two directories by default (non-recursive in each):

  1. ``team/scripts/`` — top-level operator-facing scripts (``*.sh``).
  2. ``team/scripts/cm/`` — CM subsystem operator entry points
     (``cm/release.sh``, ``cm/open-rc.sh``, ``cm/finalize.sh``, etc.).

Other subdirectories (``lib/``, ``dashboard/``, ``wake/``, ``migrate/``,
``cleanup/``, ``harness/``) remain excluded: those contain library helpers
and process-internal scripts invoked programmatically, not by operators.

When ``--scripts-dir`` is supplied the scanner operates on that single
directory only (non-recursive), matching the pre-cm-extension behavior.

Detection
---------
A script is considered help-aware when any of the following patterns appear
in its source text (any line):

  1. A ``--help`` or ``-h`` token inside a ``case`` branch:
     ``--help)``, ``-h)``, ``--help|-h)``, ``-h|--help)``

  2. An ``argparse_has`` call for help (operator_args.sh framework):
     ``argparse_has help``, ``argparse_has "help"``, ``argparse_has h``,
     ``argparse_has "h"``

  3. A ``"--help"`` or ``"-h"`` comparison on a line (scripts that use
     ``$arg == "--help"`` or similar patterns)

  4. An ``exec`` line that passes through all arguments (``"$@"`` or
     ``$@``) — these scripts forward ``--help`` to a delegate that
     handles it; the script itself is a thin dispatcher

  5. A ``--help`` string in a heredoc block: ``--help,``, ``--help|-h``,
     or ``--help`` followed by whitespace/punctuation (documentation)

These patterns match the conventions used across the team/scripts/ codebase.
A script that passes none of these checks is flagged as missing help.

Exempt list
-----------
A plain-text data file (one entry per line, ``#`` comments allowed, blank
lines ignored) lists scripts that are exempt from the check.  Entries may
be bare basenames (``wake-batch.sh``) for top-level scripts, or
directory-relative names (``cm/open-doc.sh``) for scripts in scanned
subdirectories.  The default path is ``team/scripts/help_presence_exempt.txt``
relative to the script's inferred repo root.  Override via ``--exempt-list``.

Exit codes
----------
  0   No findings; all non-exempt scripts implement --help.
  1   One or more scripts are missing --help (see stdout for the list).
  2   Usage error or internal failure (missing directory, missing exempt list).

Usage
-----
  python3 scripts/lint_help_presence.py [--scripts-dir PATH]
                                        [--exempt-list PATH]
                                        [--verbose]

  --scripts-dir PATH   Single directory to scan (overrides the default two-
                       directory scan of team/scripts/ and team/scripts/cm/).
  --exempt-list PATH   Exempt-list data file (default: team/scripts/
                       help_presence_exempt.txt relative to repo root).
  --verbose, -v        Print each file name as it is scanned.

Examples
--------
  # From repo root (scans team/scripts/ AND team/scripts/cm/ by default):
  python3 team/scripts/lint_help_presence.py

  # With explicit single directory:
  python3 team/scripts/lint_help_presence.py \\
      --scripts-dir /path/to/team/scripts

  # With explicit paths:
  python3 team/scripts/lint_help_presence.py \\
      --scripts-dir /path/to/team/scripts \\
      --exempt-list /path/to/team/scripts/help_presence_exempt.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Help-detection patterns
# ---------------------------------------------------------------------------

# Pattern 1: case-branch ``--help)`` / ``-h)`` / combined forms.
# Captures: --help) or -h) or --help|-h) or -h|--help) and variants.
_CASE_HELP: re.Pattern[str] = re.compile(
    r"""(?:--help|-h)\s*[|)]"""
)

# Pattern 2: operator_args.sh framework: argparse_has (with or without quotes).
# Forms: argparse_has help  argparse_has "help"  argparse_has 'h'  argparse_has h
_ARGPARSE_HELP: re.Pattern[str] = re.compile(
    r"""argparse_has\s+['"']?(help|h)['"']?\b"""
)

# Pattern 3: string comparison against "--help" or "-h" (scripts that use
# $arg == "--help" or [[ "$1" == "--help" ]] patterns).
_ARG_CMP_HELP: re.Pattern[str] = re.compile(
    r"""["'](?:--help|-h)["']\s*"""
)

# Pattern 4: exec/delegate with "$@" passthrough (thin dispatcher scripts
# that forward all flags including --help to a subcommand).
_EXEC_PASSTHROUGH: re.Pattern[str] = re.compile(
    r"""\bexec\b.*\"\$@\""""
)

# Pattern 5: --help in a heredoc documentation block (indented --help entry).
# Matches lines like "  --help, -h   Show help" inside a cat <<EOF block.
_HEREDOC_HELP: re.Pattern[str] = re.compile(
    r"""^\s{2,}--help\b"""
)

# Combined list: a script is help-aware if any line matches any pattern.
_HELP_PATTERNS: list[re.Pattern[str]] = [
    _CASE_HELP,
    _ARGPARSE_HELP,
    _ARG_CMP_HELP,
    _EXEC_PASSTHROUGH,
    _HEREDOC_HELP,
]


def _has_help_handler(path: Path) -> bool:
    """Return True if *path* contains any help-presence detection pattern."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    for line in text.splitlines():
        for pat in _HELP_PATTERNS:
            if pat.search(line):
                return True
    return False


# ---------------------------------------------------------------------------
# Exempt list reader
# ---------------------------------------------------------------------------


def _load_exempt_list(exempt_list_path: Path) -> frozenset[str]:
    """Load the exempt list and return a frozenset of entry strings.

    File format: one entry per line, ``#`` comments, blank lines ignored.
    Entries may be bare basenames (``wake-batch.sh``) for top-level scripts
    or directory-relative names (``cm/open-doc.sh``) for subdirectory scripts.
    """
    if not exempt_list_path.is_file():
        print(
            f"ERROR: exempt list not found: {exempt_list_path}\n"
            "Use --exempt-list to specify an alternative path.",
            file=sys.stderr,
        )
        sys.exit(2)

    names: set[str] = set()
    for raw_line in exempt_list_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return frozenset(names)


# ---------------------------------------------------------------------------
# Per-directory scan
# ---------------------------------------------------------------------------


def _scan_directory(
    scripts_dir: Path,
    *,
    label_prefix: str,
    exempt_names: frozenset[str],
    verbose: bool,
) -> tuple[list[str], int, int]:
    """Scan *scripts_dir* non-recursively for .sh files missing --help.

    Returns ``(offenders, exempt_count, checked_count)`` where each offender
    entry is a label string of the form ``<label_prefix><basename>`` (e.g.
    ``cm/release.sh`` or ``release.sh``).

    *exempt_names* is matched against the label form (``cm/name.sh`` or bare
    ``name.sh``), allowing directory-specific exemptions.
    """
    shell_scripts: list[Path] = sorted(
        p for p in scripts_dir.glob("*.sh") if p.is_file()
    )

    offenders: list[str] = []
    exempt_count = 0
    checked_count = 0

    for script in shell_scripts:
        label = label_prefix + script.name

        if label in exempt_names or script.name in exempt_names:
            if verbose:
                print(f"  exempt: {label}", flush=True)
            exempt_count += 1
            continue

        if verbose:
            print(f"  scanning: {label}", flush=True)

        checked_count += 1
        if not _has_help_handler(script):
            offenders.append(label)

    return offenders, exempt_count, checked_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_help_presence.py",
        description=(
            "Check that every operator-facing shell script in team/scripts/ "
            "and team/scripts/cm/ implements a --help or -h flag that prints "
            "usage text.  Scripts listed in the exempt list are skipped.  "
            "Exits 0 when all non-exempt scripts pass; exits 1 when any are "
            "missing help.  Supply --scripts-dir to scan a single directory "
            "instead of the default two-directory scan."
        ),
    )
    parser.add_argument(
        "--scripts-dir",
        metavar="PATH",
        help=(
            "Single directory to scan for operator-facing shell scripts "
            "(overrides the default two-directory scan of team/scripts/ and "
            "team/scripts/cm/).  Default: scan both team/scripts/ and "
            "team/scripts/cm/ relative to this script's repo root."
        ),
    )
    parser.add_argument(
        "--exempt-list",
        metavar="PATH",
        help=(
            "Path to the exempt-list data file (one entry per line; bare "
            "basenames for top-level scripts, 'cm/<name>.sh' for cm/ scripts; "
            "# comments allowed).  Default: team/scripts/help_presence_exempt.txt "
            "relative to repo root."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each file name as it is scanned.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Infer the repo root: team/scripts/lint_help_presence.py → repo root.
    script_dir = Path(__file__).resolve().parent  # team/scripts/
    repo_root = script_dir.parent.parent           # repo root

    # Resolve the exempt list path.
    if args.exempt_list:
        exempt_list_path = Path(args.exempt_list).resolve()
    else:
        exempt_list_path = script_dir / "help_presence_exempt.txt"

    exempt_names = _load_exempt_list(exempt_list_path)

    # ------------------------------------------------------------------
    # Determine which directories to scan.
    #
    # --scripts-dir supplied: single-directory mode (backward-compatible).
    # No --scripts-dir: default two-directory scan (team/scripts/ + cm/).
    # ------------------------------------------------------------------
    if args.scripts_dir:
        # Single-directory override: scan exactly this directory, no cm/ addition.
        single_dir = Path(args.scripts_dir).resolve()
        if not single_dir.is_dir():
            print(
                f"ERROR: scripts directory not found: {single_dir}\n"
                "Use --scripts-dir to specify an alternative path.",
                file=sys.stderr,
            )
            return 2

        shell_scripts: list[Path] = sorted(
            p for p in single_dir.glob("*.sh") if p.is_file()
        )
        print(
            f"lint_help_presence: scanning {len(shell_scripts)} scripts in {single_dir}",
            flush=True,
        )

        offenders: list[str] = []
        exempt_count = 0
        checked_count = 0

        for script in shell_scripts:
            name = script.name
            if name in exempt_names:
                if args.verbose:
                    print(f"  exempt: {name}", flush=True)
                exempt_count += 1
                continue
            if args.verbose:
                print(f"  scanning: {name}", flush=True)
            checked_count += 1
            if not _has_help_handler(script):
                offenders.append(name)

    else:
        # Default: scan team/scripts/ (top-level) AND team/scripts/cm/.
        top_dir = script_dir          # team/scripts/
        cm_dir = script_dir / "cm"   # team/scripts/cm/

        if not top_dir.is_dir():
            print(
                f"ERROR: scripts directory not found: {top_dir}\n"
                "Use --scripts-dir to specify an alternative path.",
                file=sys.stderr,
            )
            return 2

        # Collect counts for the summary line.
        top_scripts = sorted(p for p in top_dir.glob("*.sh") if p.is_file())
        cm_scripts: list[Path] = []
        if cm_dir.is_dir():
            cm_scripts = sorted(p for p in cm_dir.glob("*.sh") if p.is_file())

        total = len(top_scripts) + len(cm_scripts)
        print(
            f"lint_help_presence: scanning {total} scripts in "
            f"{top_dir} and {cm_dir}",
            flush=True,
        )

        offenders = []
        exempt_count = 0
        checked_count = 0

        # Scan top-level scripts (bare basename label).
        top_offenders, top_exempt, top_checked = _scan_directory(
            top_dir,
            label_prefix="",
            exempt_names=exempt_names,
            verbose=args.verbose,
        )
        offenders.extend(top_offenders)
        exempt_count += top_exempt
        checked_count += top_checked

        # Scan cm/ scripts (directory-relative label: "cm/<name>.sh").
        if cm_dir.is_dir():
            cm_offenders, cm_exempt, cm_checked = _scan_directory(
                cm_dir,
                label_prefix="cm/",
                exempt_names=exempt_names,
                verbose=args.verbose,
            )
            offenders.extend(cm_offenders)
            exempt_count += cm_exempt
            checked_count += cm_checked

    if offenders:
        print(
            f"\nlint_help_presence: {len(offenders)} script(s) missing --help:\n",
            flush=True,
        )
        for label in offenders:
            print(f"  {label}", flush=True)
        print(
            f"\nlint_help_presence: FAIL — {len(offenders)} script(s) do not implement "
            "--help or -h.  Add a --help handler to each script, or add the basename "
            "to the exempt list if the script is an internal helper.",
            flush=True,
        )
        return 1

    print(
        f"lint_help_presence: OK — {checked_count} script(s) checked, "
        f"{exempt_count} exempt, 0 offenders.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

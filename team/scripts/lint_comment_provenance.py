#!/usr/bin/env python3
"""
lint_comment_provenance.py
==========================
Lint gate: comments, docstrings, help text, log lines, and output strings in
production code must describe behavior — not process history.

Specifically, this linter flags lines in code files that contain internal
provenance references:

  - Bug IDs:       ``BUG-NNNN`` (e.g. BUG-0042, BUG-0362)
  - Task IDs:      ``CODER-YYYYMMDD-NNN``, ``WRITER-YYYYMMDD-NNN``, etc.
                   (six-role agent prefixes followed by a date and sequence)
  - Requirement versions cited as provenance:
                   ``v1.23.4-priority-bundle`` or ``v1.23.4-bug-bundle``
                   (version-bundle references that name an internal RC artifact)
  - Priority IDs:  ``PRIORITY-NNNN`` (e.g. PRIORITY-0004)

These references encode *why* the code was written rather than *what* it does.
They belong in the git commit message and the kanban task folder, not in the
source code that downstream readers and operators will see long after the
context has been squashed away.

Allowed exceptions (per coding-standards.md Directive 8 and CODER.md):
  1. **Format or usage examples** — lines that contain the provenance token
     only inside a documented example string, such as
     ``--key BUG-0042`` in a ``--help`` block where BUG-NNNN is the
     *format* of a key, not a reference to a specific bug.
     Signal: the token appears alongside "e.g.", "e.g,", "example:", "e.g.:",
     "Examples", or is inside a usage-block format string (``BUG-NNNN``
     with four literal N's).
  2. **Skip annotations citing an OPEN follow-up bug** — pytest ``skip()``
     calls or ``SKIP:`` comments that reference a real BUG-NNNN.  These are
     verified by the separate lint_skip_bug_gate.sh; they are exempt here.
     Signal: the line contains ``pytest.mark.skip`` or ``skip(`` or ``SKIP:``.
  3. **External references** — references to upstream issues, CVEs, or RFCs
     where the external identifier is being cited, not an internal kanban ID.
     Signal: the token is preceded or followed by ``CVE-``, ``RFC``, ``GH#``,
     or ``upstream``.

Per-line opt-out: add the comment
  ``# provenance-allowlist: <justification>``
within 5 lines BEFORE the flagged line to suppress that specific finding.

Scope
-----
  team/scripts/      — all .py and .sh files (non-recursively, top-level only)
  team/scripts/lib/  — all .py and .sh files (non-recursively)
  team/scripts/cm/   — all .py and .sh files (non-recursively)
  team/scripts/dashboard/lib/  — all .py and .sh files (non-recursively)
  team/pgai_agent_kanban/  — all .py files (recursively), excluding:
                             test files (*test_*.py, tests/ directories),
                             conftest.py

Override via ``--code-dirs PATH [PATH ...]``.

Test files (team/tests/, fixtures/, conftest.py) are deliberately excluded:
test and fixture code legitimately cites bug IDs in skip annotations and
in docstrings that document what behavior the test guards.  The
lint_skip_bug_gate.sh handles the subset of skip-citation requirements.

Exit codes
----------
  0   No findings.
  1   One or more findings (see stdout).
  2   Usage error or internal failure.

Usage
-----
  python3 scripts/lint_comment_provenance.py [--code-dirs PATH [PATH ...]]
                                             [--verbose]

  --code-dirs PATH ...  Directories to scan (defaults described above).
  --verbose, -v         Print each file name as it is scanned.

Examples
--------
  # From repo root (team/ as cwd):
  python3 scripts/lint_comment_provenance.py

  # With explicit dirs:
  python3 scripts/lint_comment_provenance.py \\
      --code-dirs team/scripts team/pgai_agent_kanban
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Provenance token patterns
# ---------------------------------------------------------------------------

# Matches internal bug IDs: BUG-NNNN where NNNN is one or more digits.
_BUG_ID: re.Pattern[str] = re.compile(r"\bBUG-\d+\b", re.IGNORECASE)

# Matches task IDs: AGENT-YYYYMMDD-NNN-slug forms (six kanban agent prefixes).
# Agent prefixes: CODER, WRITER, TESTER, CM, PM, PO.
_TASK_ID: re.Pattern[str] = re.compile(
    r"\b(?:CODER|WRITER|TESTER|CM|PM|PO)-\d{8}-\d+\b",
    re.IGNORECASE,
)

# Matches PRIORITY-NNNN internal identifiers.
_PRIORITY_ID: re.Pattern[str] = re.compile(r"\bPRIORITY-\d+\b", re.IGNORECASE)

# Matches internal version-bundle references: vX.Y.Z-priority-bundle or
# vX.Y.Z-bug-bundle (internal RC artifact citations as provenance).
_VERSION_BUNDLE: re.Pattern[str] = re.compile(
    r"\bv\d+\.\d+\.\d+-(?:priority|bug)-bundle\b",
    re.IGNORECASE,
)

_PROVENANCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_BUG_ID, "BUG-ID"),
    (_TASK_ID, "task-ID"),
    (_PRIORITY_ID, "PRIORITY-ID"),
    (_VERSION_BUNDLE, "version-bundle"),
]

# ---------------------------------------------------------------------------
# Allowlist / exception patterns
# ---------------------------------------------------------------------------

# Exception 1: format or usage examples.
# A line that contains "e.g.", "example:", "Examples", format placeholders like
# "BUG-NNNN", or is a usage-example block is likely documentation, not live code.
_EXAMPLE_MARKERS: re.Pattern[str] = re.compile(
    r"(?:e\.g\.|e\.g,|example:|Examples?\b|BUG-NNNN|PRIORITY-NNNN|TASK-ID)",
    re.IGNORECASE,
)

# Exception 2: pytest skip / SKIP annotation lines.
_SKIP_ANNOTATION: re.Pattern[str] = re.compile(
    r"""(?:pytest\.mark\.skip|skip\s*\(|SKIP\s*:|\bxfail\b)""",
    re.IGNORECASE,
)

# Exception 3: external references (CVE, RFC, GH, upstream).
_EXTERNAL_REF: re.Pattern[str] = re.compile(
    r"""(?:CVE-\d|RFC\s*\d|GH#\d|upstream|external)""",
    re.IGNORECASE,
)

# Combined exception checker.
_EXCEPTION_PATTERNS: list[re.Pattern[str]] = [
    _EXAMPLE_MARKERS,
    _SKIP_ANNOTATION,
    _EXTERNAL_REF,
]

# Per-line opt-out marker (within 5 lines before the flagged line).
_OPT_OUT: re.Pattern[str] = re.compile(
    r"#\s*provenance-allowlist\s*:", re.IGNORECASE
)
_OPT_OUT_WINDOW = 5

# ---------------------------------------------------------------------------
# File-level whitelists: certain files are excluded entirely from the scan.
# ---------------------------------------------------------------------------

# Filenames whose content is inherently about internal IDs (e.g. the file
# that routes bug promotion, or this lint script's own regex patterns).
_FILE_WHITELIST: frozenset[str] = frozenset({
    "lint_comment_provenance.py",  # this file's own pattern strings
    "promote_bundled_items.py",    # CM tool that reads/routes BUG-NNNN IDs
    "changelog_writer.py",         # reads bug IDs from ledger to build CHANGELOG
})

# Path segment exclusions: any file whose path contains these segments is skipped.
_PATH_EXCLUDE_SEGMENTS: tuple[str, ...] = (
    "tests/",
    "test/",
    "fixtures/",
    "conftest",
    "__pycache__",
)


def _is_excluded_path(path: Path, repo_root: Path) -> bool:
    """Return True if *path* should be excluded from the scan."""
    if path.name in _FILE_WHITELIST:
        return True
    # Build the repo-relative path string for segment matching.
    try:
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        rel = str(path).replace("\\", "/")
    return any(seg in rel for seg in _PATH_EXCLUDE_SEGMENTS)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _has_opt_out(lines: list[str], flagged_lineno: int) -> bool:
    """Return True if an opt-out marker appears within _OPT_OUT_WINDOW lines
    before *flagged_lineno* (1-based line number).
    """
    start = max(0, flagged_lineno - 1 - _OPT_OUT_WINDOW)
    end = flagged_lineno - 1
    for line in lines[start:end]:
        if _OPT_OUT.search(line):
            return True
    return False


def _is_exception(line: str) -> bool:
    """Return True if *line* matches any allowed-exception pattern."""
    for pat in _EXCEPTION_PATTERNS:
        if pat.search(line):
            return True
    return False


# ---------------------------------------------------------------------------
# Per-file scanner
# ---------------------------------------------------------------------------


def _scan_file(filepath: Path, verbose: bool = False) -> list[str]:
    """Scan *filepath* and return a list of finding strings."""
    if verbose:
        print(f"  scanning: {filepath}", flush=True)

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"{filepath}: ERROR reading file: {exc}"]

    lines = text.splitlines(keepends=True)
    findings: list[str] = []

    for idx, line in enumerate(lines):
        lineno = idx + 1

        # Skip blank lines.
        stripped = line.strip()
        if not stripped:
            continue

        # Check each provenance pattern.
        for pat, label in _PROVENANCE_PATTERNS:
            if not pat.search(line):
                continue

            # Apply exception filters.
            if _is_exception(line):
                break

            # Apply per-line opt-out.
            if _has_opt_out(lines, lineno):
                break

            match_token = pat.search(line).group(0)  # type: ignore[union-attr]
            findings.append(
                f"{filepath}:{lineno}: [{label}] "
                f"provenance reference '{match_token}' in code — "
                "comments and output must describe behavior, not internal IDs. "
                "Move to commit message / task status, or add "
                "'# provenance-allowlist: <justification>' within 5 lines before "
                "this line to opt out."
            )
            break  # one finding per line is sufficient

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _collect_files(
    scan_dirs: list[Path],
    repo_root: Path,
) -> list[Path]:
    """Collect all scannable files from *scan_dirs*, applying exclusions."""
    collected: list[Path] = []
    seen: set[Path] = set()

    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        # Recurse to find .py and .sh files.
        for suffix in (".py", ".sh"):
            for p in scan_dir.rglob(f"*{suffix}"):
                if p in seen:
                    continue
                seen.add(p)
                if not p.is_file():
                    continue
                if _is_excluded_path(p, repo_root):
                    continue
                collected.append(p)

    return sorted(collected)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_comment_provenance.py",
        description=(
            "Enforce the comment-provenance coding standard: code files must not "
            "cite internal bug IDs, task IDs, priority IDs, or version-bundle names. "
            "These identifiers belong in commit messages and task state, not in "
            "source code. "
            "Exits 0 when no violations are found; exits 1 when violations are found."
        ),
    )
    parser.add_argument(
        "--code-dirs",
        metavar="PATH",
        nargs="*",
        default=None,
        help=(
            "Directories to scan for provenance violations. "
            "Default: team/scripts/, team/scripts/lib/, team/scripts/cm/, "
            "team/pgai_agent_kanban/ (non-test Python files) relative to the "
            "repo root inferred from this script's location."
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

    # Infer the repo root: team/scripts/lint_comment_provenance.py → repo root.
    script_dir = Path(__file__).resolve().parent  # team/scripts/
    repo_root = script_dir.parent.parent           # repo root

    # Resolve code directories to scan.
    if args.code_dirs is not None:
        scan_dirs = [Path(d).resolve() for d in args.code_dirs if d]
    else:
        scan_dirs = [
            script_dir,                          # team/scripts/ (non-recursive via rglob but excludes tests)
            script_dir / "lib",                  # team/scripts/lib/
            script_dir / "cm",                   # team/scripts/cm/
            repo_root / "team" / "pgai_agent_kanban",  # package (rglob, non-test)
        ]

    files = _collect_files(scan_dirs, repo_root)

    print(
        f"lint_comment_provenance: scanning {len(files)} file(s) in "
        + ", ".join(str(d) for d in scan_dirs if d.is_dir()),
        flush=True,
    )

    all_findings: list[str] = []
    for filepath in files:
        all_findings.extend(_scan_file(filepath, verbose=args.verbose))

    if all_findings:
        print(
            f"\nlint_comment_provenance: {len(all_findings)} finding(s):\n",
            flush=True,
        )
        for finding in all_findings:
            print(finding, flush=True)
        print(
            f"\nlint_comment_provenance: FAIL — {len(all_findings)} finding(s). "
            "Remove internal ID references from code, move them to commit messages "
            "or task status, or add a per-line opt-out comment.",
            flush=True,
        )
        return 1

    print(
        f"lint_comment_provenance: OK — 0 findings across {len(files)} file(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

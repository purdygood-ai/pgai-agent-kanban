#!/usr/bin/env python3
"""
lint_halt_scope_writers.py
===========================
A lint/grep gate that reports every HALT-file *creation* site whose target
resolves to the fleet-wide root sentinel ($KANBAN_ROOT/HALT) but whose
enclosing function/script is not itself named to say so.

WHY THIS EXISTS
---------------
The kanban root's HALT file stops every registered project at once; a
project's own projects/<name>/HALT stops that project alone (SOP-core.md,
"HALT Flags" / "Scope rule"). A single-project autonomous repair that
touches the root file parks every sibling project's chain for a fault
none of them has — exactly the defect this lint exists to catch before it
regresses. The codebase's own convention already self-documents intentional
fleet-scope writers by naming them for it (`halt_global`, `unhalt_global`,
`GLOBAL_HALT_FILE`, `halt-global.sh`) — this lint enforces that naming
convention mechanically: a write that lands on the root sentinel from a
function or script NOT named with "global" is flagged.

SCOPE
-----
Writers only. A line that merely checks for the file's existence
(`[[ -f ... ]]`, `.exists()`) or reads its contents is never a violation —
only a *creation* call (`touch`, a `>` redirect that creates the file, a
Python `.touch()`, `.write_text()`, or `open(..., "w"...)`) is in scope.
Removal (`rm`, `.unlink()`) is intentionally out of scope for this lint —
see "Removal is out of scope" below.

Scanned:
  - team/scripts/**/*.sh  (excluding any path containing a "tests" or
    "overwatch-checks/test-*" segment — those are test fixtures, not
    shipped writers, and routinely plant synthetic HALT files at names the
    fixture itself controls)
  - team/pgai_agent_kanban/**/*.py (excluding any "tests" directory)

DETECTION (bash)
-----------------
A simple two-pass per-file scan, adequate for this codebase's consistent
style (`name() {` opens a function, a bare `}` at column 0 closes it):

  1. Track the "current scope name" — the innermost function name, or the
     file's own basename at top level.
  2. Track variable assignments of the shape
     `var="${KANBAN_ROOT}/HALT"` (or unbraced `$KANBAN_ROOT`) within the
     current scope — these name a root-scoped HALT path. The variable name
     may be preceded by a bash declaration keyword (`local`, `declare`,
     `typeset`, `export`, `readonly`, optionally with flags such as
     `declare -g`) — the keyword changes the variable's storage class, not
     what the assignment does, so it is stripped before matching rather
     than enumerated as a separate assignment shape.
  3. Flag a `touch <path>` or a `>` redirect creating `<path>` when
     `<path>` is either the literal `.../$KANBAN_ROOT.../HALT` shape, or a
     variable tracked as root-scoped by step 2 — UNLESS the current scope
     name contains "global" (case-insensitive, underscore/hyphen-bounded).

DETECTION (python)
--------------------
The same shape, adapted to Python: a `def name(...):` opens a scope; a
`kanban_root`-derived variable assigned to a path ending in `"HALT"` is
tracked; a `.touch(`, `.write_text(`, or `open(<var>, "w"...)` call on that
variable is flagged unless the enclosing function name contains "global".

ALLOWLIST ESCAPE HATCH
-----------------------
A comment `# halt-scope-allowlist: <justification>` directly above a
flagged line suppresses that one finding — an in-file marker convention
(distinct from `lint_hygiene.py`'s line-level sidecar suppression). Use it
only for a genuinely fleet-scoped write whose enclosing name cannot say so
(rare — prefer naming the scope instead).

REMOVAL IS OUT OF SCOPE
-------------------------
This lint does not flag `rm`/`.unlink()` against the root HALT file. The
kanban's existing invariant — an autonomous path never removes a HALT it
did not create — is enforced by the halt-first wrapper's own
pre-existing/created-by-us bookkeeping (team/scripts/lib/overwatch_protocol.sh,
`overwatch_halt_first_fix`), not by a static removal scan; a removal-scope
scan would need call-graph reachability this lint does not attempt to
avoid a wall of undecidable false positives.

CLASS: CONVERT (docs/blocking-site-census.md classification vocabulary).
This is a quality-class finding — a scope-naming regression is a
maintainability defect, not a destructive or unusable-build failure — so it
warns, is recorded as a finding by the gated-runner quality-lint wrapper
(team/scripts/lib/gated_runner_lint.sh), and never blocks the loop. Direct
invocation (this file run standalone, as documented below) still exits
non-zero on a finding — that direct-invocation exit is the CONVERT-class
site itself; the gated wrapper is what coerces it to 0 for the suite run.

EXIT CODES
----------
  0   No violations found (lint is green).
  1   One or more violations found; see stdout for details.
  2   Usage error or the specified directory was not found.

USAGE
-----
  python3 team/scripts/lint_halt_scope_writers.py [OPTIONS]

  --team-dir PATH   Root of the team/ directory (default: parent of this
                     script's directory, i.e. team/ when the script lives
                     at team/scripts/).
  --verbose, -v      Print each file as it is checked.

EXAMPLES
--------
  # From repo root:
  python3 team/scripts/lint_halt_scope_writers.py

  # With explicit team dir:
  python3 team/scripts/lint_halt_scope_writers.py --team-dir /path/to/team
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWLIST_MARKER_RE = re.compile(r"#\s*halt-scope-allowlist\s*:", re.IGNORECASE)

# A scope (function or file) is self-declared fleet-scoped when its name
# contains "global" as an underscore/hyphen-bounded word.
_GLOBAL_NAME_RE = re.compile(r"(?:^|[_-])global(?:[_-]|$)", re.IGNORECASE)

# Path segments that mark a file as test scaffolding rather than a shipped
# writer — synthetic fixtures routinely plant HALT files at names the
# fixture itself controls, which is not production code this lint audits.
_TEST_PATH_SEGMENTS = ("tests/", "/test-", "/test_")


class Violation(NamedTuple):
    """A single lint violation."""

    path: Path
    line: int
    scope: str
    snippet: str


# ---------------------------------------------------------------------------
# Bash detection
# ---------------------------------------------------------------------------

_BASH_FUNC_OPEN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{\s*$")
_BASH_FUNC_CLOSE_RE = re.compile(r"^\}\s*$")

# var="${KANBAN_ROOT}/HALT"  or  var="$KANBAN_ROOT/HALT"  (root-scoped,
# excludes HALT-AFTER / HALT_OVERWATCH because those do not end the value
# immediately after "HALT").
# "/HALT" not immediately followed by "-" (excludes HALT-AFTER); the
# trailing \b already excludes HALT_OVERWATCH since "_" is a word character
# contiguous with "HALT" (no boundary exists there).
_HALT_ROOT_SUFFIX = r'/HALT(?!-)\b'

# The gap between the KANBAN_ROOT reference and the /HALT suffix that
# immediately follows it. Only quoting/brace noise is tolerated here —
# "${KANBAN_ROOT}", "${KANBAN_ROOT}"/HALT's closing quote, or
# ${KANBAN_ROOT:?} — never a real intervening path segment such as
# "/projects/${name}". Excluding letters, digits, and "/" from the gap is
# what makes that structural rather than enumerated: any real path segment
# necessarily contains a letter/digit (a project name, "projects" itself)
# or another "/", both of which are barred, so a project-scoped
# $KANBAN_ROOT/projects/<name>/HALT can never be misread as the fleet-wide
# sentinel merely because the '.*' used to reach past it.
_KANBAN_ROOT_HALT_GAP = r'[^A-Za-z0-9/]*'

# A declaration keyword may precede the variable name (`local x=...`,
# `declare -g x=...`) without changing what the assignment does — the
# keyword only sets the variable's storage class (local/exported/readonly),
# which is orthogonal to whether the value it holds is a root-scoped HALT
# path. Modeled as one optional, non-capturing prefix rather than five
# separate assignment patterns so the orthogonality is structural: every
# keyword shares the same "optional, then ordinary var=..." shape.
_BASH_DECL_KEYWORDS = ("local", "declare", "typeset", "export", "readonly")
_BASH_DECL_PREFIX_RE = (
    r'(?:(?:' + "|".join(_BASH_DECL_KEYWORDS) + r')\s+(?:-\S+\s+)*)?'
)

_BASH_ROOT_HALT_ASSIGN_RE = re.compile(
    r'^\s*' + _BASH_DECL_PREFIX_RE + r'([A-Za-z_][A-Za-z0-9_]*)=.*\bKANBAN_ROOT\b'
    + _KANBAN_ROOT_HALT_GAP + _HALT_ROOT_SUFFIX
)

# touch <bare-var>   — captures the variable name touched. Deliberately not
# end-anchored: real call sites often trail with "|| { ... }" error handling.
_BASH_TOUCH_VAR_RE = re.compile(r'\btouch\s+"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?')

# touch <literal path containing KANBAN_ROOT and ending in /HALT>
_BASH_TOUCH_LITERAL_RE = re.compile(
    r'\btouch\s+.*\bKANBAN_ROOT\b' + _KANBAN_ROOT_HALT_GAP + _HALT_ROOT_SUFFIX
)

# > <bare-var>  (redirect creating/truncating a file at that variable's path)
_BASH_REDIRECT_VAR_RE = re.compile(r'>\s*"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?')

# > <literal path containing KANBAN_ROOT and ending in /HALT>
_BASH_REDIRECT_LITERAL_RE = re.compile(
    r'>\s*.*\bKANBAN_ROOT\b' + _KANBAN_ROOT_HALT_GAP + _HALT_ROOT_SUFFIX
)


def _is_test_path(rel_path: str) -> bool:
    return any(seg in rel_path for seg in _TEST_PATH_SEGMENTS)


def scan_bash_text(text: str, *, path: Path) -> list[Violation]:
    """Scan bash source *text* for root-scoped HALT creation outside a
    'global'-named scope. *path* is used only for reporting."""
    violations: list[Violation] = []
    lines = text.splitlines()

    current_scope = path.name  # top-level scope name defaults to the file
    scope_vars: dict[str, bool] = {}  # varname -> is_root_halt, per current scope
    prev_line = ""

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()

        if stripped.startswith("#"):
            prev_line = stripped
            continue

        func_open = _BASH_FUNC_OPEN_RE.match(raw_line)
        if func_open:
            current_scope = func_open.group(1)
            scope_vars = {}
            prev_line = stripped
            continue

        if _BASH_FUNC_CLOSE_RE.match(raw_line):
            current_scope = path.name
            scope_vars = {}
            prev_line = stripped
            continue

        assign = _BASH_ROOT_HALT_ASSIGN_RE.match(raw_line)
        if assign:
            scope_vars[assign.group(1)] = True
            prev_line = stripped
            continue

        # Lines that only PRINT instructional text (an operator-facing
        # "to fix, run: touch $FOO" message) are not a mutation — skip
        # write-detection for them so quoted example commands are not
        # misread as the script actually performing the touch/redirect.
        if re.match(r"^\s*(echo|printf)\b", raw_line):
            prev_line = stripped
            continue

        is_root_write = False
        write_var: Optional[str] = None
        if _BASH_TOUCH_LITERAL_RE.search(raw_line) or _BASH_REDIRECT_LITERAL_RE.search(
            raw_line
        ):
            is_root_write = True
        else:
            for pat in (_BASH_TOUCH_VAR_RE, _BASH_REDIRECT_VAR_RE):
                m = pat.search(raw_line)
                if m and scope_vars.get(m.group(1)):
                    is_root_write = True
                    write_var = m.group(1)
                    break

        if is_root_write:
            # Self-documenting scope: either the enclosing function/script
            # name says "global", or the target variable's own name does
            # (the codebase's existing convention, e.g. GLOBAL_HALT_FILE).
            declared_global = bool(_GLOBAL_NAME_RE.search(current_scope)) or (
                write_var is not None and bool(_GLOBAL_NAME_RE.search(write_var))
            )
            allowlisted = bool(_ALLOWLIST_MARKER_RE.search(prev_line))
            if not declared_global and not allowlisted:
                violations.append(
                    Violation(
                        path=path,
                        line=lineno,
                        scope=current_scope,
                        snippet=stripped,
                    )
                )

        prev_line = stripped

    return violations


def scan_bash_file(path: Path) -> list[Violation]:
    """Return the HALT-scope violations in the bash file at *path* (see :func:`scan_bash_text`)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return scan_bash_text(text, path=path)


# ---------------------------------------------------------------------------
# Python detection
# ---------------------------------------------------------------------------

_PY_DEF_RE = re.compile(r"^(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# var = <expr referencing kanban_root> / "HALT"  (root-scoped)
#
# DECISION (declaration-prefix gap, python side): left as-is, deliberately.
# Bash's `local`/`declare`/`typeset`/`export`/`readonly` are storage-class
# keywords that precede an otherwise-ordinary `var=value` statement --
# that is the shape the bash-side fix above widens for. Python has no
# equivalent keyword vocabulary in front of a plain assignment statement
# (`global`/`nonlocal` are re-binding declarations for names assigned
# elsewhere, not prefixes on the assignment itself, and neither one can
# appear in front of `var = kanban_root / "HALT"` the way `local` can in
# bash). There is therefore no declaration-prefixed python shape for this
# ticket to close, and the trailing-`"HALT"`-literal requirement below is
# an unrelated, pre-existing constraint on the assignment's right-hand
# side rather than a keyword-prefix blind spot. Widening this pattern is
# out of scope here.
_PY_ROOT_HALT_ASSIGN_RE = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*.*\bkanban_root\b.*/\s*["\']HALT["\']\s*$'
)

# <var>.touch( | <var>.write_text( | open(<var>, "w"...
_PY_WRITE_CALL_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.(?:touch|write_text)\(|open\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*[\"']w"
)


def scan_python_text(text: str, *, path: Path) -> list[Violation]:
    """Scan python source *text* for root-scoped HALT creation outside a
    'global'-named function. *path* is used only for reporting."""
    violations: list[Violation] = []
    lines = text.splitlines()

    current_scope = path.name
    scope_indent = -1
    scope_vars: dict[str, bool] = {}
    prev_line = ""

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()

        if stripped.startswith("#"):
            prev_line = stripped
            continue

        def_match = _PY_DEF_RE.match(raw_line)
        if def_match:
            current_scope = def_match.group(2)
            scope_indent = len(def_match.group(1))
            scope_vars = {}
            prev_line = stripped
            continue

        # Dedent back to (or past) the def's own indentation ends the scope
        # (module-level code resumes). Blank lines don't count.
        if stripped and scope_indent >= 0:
            this_indent = len(raw_line) - len(raw_line.lstrip(" "))
            if this_indent <= scope_indent and not def_match:
                current_scope = path.name
                scope_indent = -1
                scope_vars = {}

        assign = _PY_ROOT_HALT_ASSIGN_RE.match(raw_line)
        if assign:
            scope_vars[assign.group(1)] = True
            prev_line = stripped
            continue

        m = _PY_WRITE_CALL_RE.search(raw_line)
        if m:
            varname = m.group(1) or m.group(2)
            if scope_vars.get(varname):
                # Self-documenting scope: either the enclosing function name
                # says "global", or the target variable's own name does.
                declared_global = bool(_GLOBAL_NAME_RE.search(current_scope)) or bool(
                    _GLOBAL_NAME_RE.search(varname)
                )
                allowlisted = bool(_ALLOWLIST_MARKER_RE.search(prev_line))
                if not declared_global and not allowlisted:
                    violations.append(
                        Violation(
                            path=path,
                            line=lineno,
                            scope=current_scope,
                            snippet=stripped,
                        )
                    )

        prev_line = stripped

    return violations


def scan_python_file(path: Path) -> list[Violation]:
    """Return the HALT-scope violations in the Python file at *path* (see :func:`scan_python_text`)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return scan_python_text(text, path=path)


# ---------------------------------------------------------------------------
# Tree walk
# ---------------------------------------------------------------------------


def scan_tree(team_dir: Path, *, verbose: bool = False) -> list[Violation]:
    """Scan team/scripts/**/*.sh and team/pgai_agent_kanban/**/*.py for
    root-scoped HALT creation outside a 'global'-named scope."""
    all_violations: list[Violation] = []

    scripts_dir = team_dir / "scripts"
    if scripts_dir.is_dir():
        for sh_path in sorted(scripts_dir.rglob("*.sh")):
            rel = sh_path.relative_to(team_dir).as_posix()
            if _is_test_path(rel):
                continue
            if verbose:
                print(f"  [halt-scope-writers] {sh_path}")
            all_violations.extend(scan_bash_file(sh_path))

    pkg_dir = team_dir / "pgai_agent_kanban"
    if pkg_dir.is_dir():
        for py_path in sorted(pkg_dir.rglob("*.py")):
            rel = py_path.relative_to(team_dir).as_posix()
            if _is_test_path(rel):
                continue
            if verbose:
                print(f"  [halt-scope-writers] {py_path}")
            all_violations.extend(scan_python_file(py_path))

    return all_violations


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _format_remedy_block(violations: list[Violation]) -> str:
    lines = [
        f"lint_halt_scope_writers: FAIL — {len(violations)} violation(s)",
        "",
        "Cause(s):",
    ]
    for v in violations:
        lines.append(
            f"  - {v.path}:{v.line} (scope '{v.scope}'): creates a "
            f"$KANBAN_ROOT/HALT-shaped path without a 'global'-named "
            f"scope — {v.snippet!r}"
        )
    lines.append("")
    lines.append("To fix, for each violation above, either:")
    lines.append(
        "  1. Move the write to the project-scoped HALT "
        "($KANBAN_ROOT/projects/<name>/HALT) instead, if the mutation is "
        "really scoped to one project (the common fix)."
    )
    lines.append(
        "  2. Rename the enclosing function/script to say 'global' if the "
        "fleet-wide stop is genuinely intended, so the scope is "
        "self-documenting."
    )
    lines.append(
        "  3. If neither applies, add '# halt-scope-allowlist: <reason>' "
        "directly above the write, with a one-line justification."
    )
    lines.append("")
    lines.append(
        "Rule: a write that creates the fleet-wide $KANBAN_ROOT/HALT file "
        "must occur inside a function or script named to say so ('global') "
        "— SOP-core.md 'HALT Flags' / 'Scope rule'."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_halt_scope_writers",
        description=(
            "Reports every HALT-file creation site targeting the fleet-wide "
            "$KANBAN_ROOT/HALT sentinel whose enclosing function/script is "
            "not itself named 'global'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  No violations found.\n"
            "  1  One or more violations found.\n"
            "  2  Usage error or directory not found.\n"
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        default=None,
        help=(
            "Root of the team/ directory (default: parent of this script's "
            "directory, i.e. team/ when the script lives at team/scripts/)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Print each file as it is checked.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Run the HALT-scope writers lint and print each violation.

    Exit codes: 0 no violation; 1 one or more (printed); 2 usage error, or the
    directory to scan was not found.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        # This script lives at team/scripts/lint_halt_scope_writers.py.
        team_dir = Path(__file__).resolve().parent.parent

    if not team_dir.is_dir():
        print(f"ERROR: team directory not found: {team_dir}", file=sys.stderr)
        return 2

    if args.verbose:
        print(f"[halt-scope-writers] Scanning {team_dir} ...")

    violations = scan_tree(team_dir, verbose=args.verbose)

    if violations:
        print("\n" + _format_remedy_block(violations), flush=True)
        return 1

    print("lint_halt_scope_writers: OK (no unnamed fleet-scope HALT writers found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

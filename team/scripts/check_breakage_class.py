#!/usr/bin/env python3
"""
check_breakage_class.py
========================
Breakage-class pre-flight gate for the gated test runners
(run-unit-tests.sh, run-integration-tests.sh).

Breakage-class failures are states where running the test suite is
meaningless: a Python file that cannot even be parsed, a shell script that
cannot even be tokenized, or a first-party import path that no longer
resolves to a real module.  This gate is the ONLY fatal pre-flight check in
either runner -- every other pre-flight check (lint, freshness gate,
provenance scan, and so on) is quality-class and never blocks (see
team/scripts/lib/gated_runner_lint.sh).  This split is deliberate: quality
findings get filed and fixed forward, but a tree that cannot even compile
produces a pytest run whose results carry no signal at all.

WHAT IS CHECKED
----------------
Three checks run, in order, over the team/ tree (excluding __pycache__,
.venv, and fixtures/ -- the same exclusion set lint_orphan_tests.py uses,
since fixtures/ intentionally contains broken example files for lint unit
tests and is never collected or executed):

  1. Python compile check -- every *.py file is compiled with py_compile
     (doraise=True).  Catches SyntaxError, IndentationError, and any other
     compile-time failure.

  2. Bash syntax check -- every *.sh file is checked with `bash -n`.
     Catches shell syntax errors.  Many tests invoke shell scripts as
     subprocesses, so a broken script under team/ can break tests far from
     the file that changed; catching it here is cheaper and clearer than
     letting it surface as a confusing subprocess failure deep in a test.

  3. First-party import resolution -- every absolute
     (``pgai_agent_kanban.a.b.c``) and relative (``from . import x``,
     ``from ..a import b``) import that targets the ``pgai_agent_kanban``
     package is resolved statically against the file system: does the
     targeted module file (or package ``__init__.py``) actually exist?
     This is deliberately a MODULE-resolution check, not a NAME-resolution
     check -- it verifies the dotted path being imported FROM exists, not
     that every name imported out of it is defined there (confirming the
     latter would require executing the module, which risks side effects
     and false positives on a fatal gate).  This scope reliably catches
     "the module moved/was renamed but a caller's import wasn't updated"
     without needing to run any project code.

WHY pgai_agent_kanban ONLY
---------------------------
pgai_agent_kanban is the one first-party package with an unambiguous
import root (team/, matching pytest's rootdir when the gated runners `cd`
into team/ before invoking pytest).  team/scripts/ and team/pm-agent/ use
ad hoc sys.path bootstrapping local to each entry point; statically
replicating each one's path setup is fragile and would risk false
positives on a fatal gate, so it is out of scope for this check by design.

EXIT CODES
----------
  0   No breakage-class failures found.
  1   One or more breakage-class failures found; see stdout for the list.
  2   Usage error (e.g., --team-dir does not exist).

USAGE
-----
  python3 scripts/check_breakage_class.py [--team-dir PATH] [--verbose]

  --team-dir PATH   Root of the team/ directory to scan.  Default: parent
                     of this script's directory (team/scripts/ -> team/).
  --verbose, -v     Print each file as it is checked.

EXAMPLES
--------
  # From the repository root:
  python3 team/scripts/check_breakage_class.py

  # With an explicit path (e.g. a synthetic fixture tree):
  python3 team/scripts/check_breakage_class.py --team-dir /path/to/team
"""

from __future__ import annotations

import argparse
import ast
import py_compile
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Exclusions -- matches lint_orphan_tests.py's _EXCLUDED_DIRS so both scripts
# agree on what counts as scannable source under team/.
# ---------------------------------------------------------------------------

_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".venv",
    "fixtures",
})

_FIRST_PARTY_PACKAGE = "pgai_agent_kanban"


@dataclass
class _Finding:
    """One breakage-class failure: a location plus the concrete cause."""

    kind: str  # "python-compile" | "bash-syntax" | "import-resolution"
    path: Path
    line: int | None
    cause: str


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def _is_excluded(path: Path) -> bool:
    return any(part in _EXCLUDED_DIRS for part in path.parts)


def _collect_files(team_dir: Path, pattern: str) -> list[Path]:
    return sorted(p for p in team_dir.rglob(pattern) if not _is_excluded(p))


# ---------------------------------------------------------------------------
# Check 1: Python compile
# ---------------------------------------------------------------------------


def _check_python_compile(py_files: list[Path], verbose: bool) -> list[_Finding]:
    findings: list[_Finding] = []
    for path in py_files:
        if verbose:
            print(f"  compiling: {path}", flush=True)
        try:
            # NOTE: quiet=2 must never be combined with doraise=True -- CPython's
            # py_compile.compile() only raises when quiet < 2 (it silently
            # swallows the exception at quiet=2 regardless of doraise). quiet=1
            # suppresses only the success/progress printout, not the raise.
            py_compile.compile(str(path), doraise=True, quiet=1)
        except py_compile.PyCompileError as exc:
            inner = exc.exc_value
            line = getattr(inner, "lineno", None)
            msg = getattr(inner, "msg", None) or str(inner)
            findings.append(
                _Finding(kind="python-compile", path=path, line=line, cause=msg)
            )
        except (SyntaxError, ValueError) as exc:  # ValueError: null bytes etc.
            line = getattr(exc, "lineno", None)
            findings.append(
                _Finding(kind="python-compile", path=path, line=line, cause=str(exc))
            )
    return findings


# ---------------------------------------------------------------------------
# Check 2: Bash syntax
# ---------------------------------------------------------------------------


def _bash_error_line(stderr: str) -> int | None:
    """Best-effort extraction of a line number from bash -n's stderr.

    bash's diagnostic format is "<path>: line <N>: <message>". Falls back
    to None (no line) when the format doesn't match -- the full stderr is
    still included in the finding's cause either way.
    """
    marker = ": line "
    idx = stderr.find(marker)
    if idx == -1:
        return None
    rest = stderr[idx + len(marker):]
    digits = ""
    for ch in rest:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def _check_bash_syntax(sh_files: list[Path], verbose: bool) -> list[_Finding]:
    findings: list[_Finding] = []
    for path in sh_files:
        if verbose:
            print(f"  checking: {path}", flush=True)
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            line = _bash_error_line(stderr)
            findings.append(
                _Finding(kind="bash-syntax", path=path, line=line, cause=stderr or "bash -n reported a syntax error")
            )
    return findings


# ---------------------------------------------------------------------------
# Check 3: first-party import resolution (pgai_agent_kanban only)
# ---------------------------------------------------------------------------


def _module_to_path(team_dir: Path, dotted: str) -> Path | None:
    """Resolve a dotted module path (rooted at team_dir) to its file.

    Returns the existing package __init__.py or module .py file, or None
    if neither exists.
    """
    parts = dotted.split(".")
    base = team_dir.joinpath(*parts)
    module_file = base.with_suffix(".py")
    if module_file.is_file():
        return module_file
    init_file = base / "__init__.py"
    if init_file.is_file():
        return init_file
    return None


def _relative_target(file_path: Path, team_dir: Path, level: int, module: str | None) -> str | None:
    """Compute the dotted module path a relative import targets.

    file_path is the file containing the import. level is the number of
    leading dots (ast.ImportFrom.level); module is the text after the dots
    (may be None for a bare "from . import x").
    """
    # The package containing file_path: if file_path is a package
    # __init__.py, its own directory is level-0; otherwise its parent dir is.
    if file_path.name == "__init__.py":
        current_pkg_dir = file_path.parent
    else:
        current_pkg_dir = file_path.parent
    # level=1 means "current package" (the dir file_path lives in);
    # each additional level walks up one more directory.
    target_dir = current_pkg_dir
    for _ in range(level - 1):
        target_dir = target_dir.parent
    try:
        rel_parts = target_dir.relative_to(team_dir).parts
    except ValueError:
        return None
    dotted = ".".join(rel_parts)
    if module:
        dotted = f"{dotted}.{module}" if dotted else module
    return dotted or None


def _check_import_resolution(py_files: list[Path], team_dir: Path, verbose: bool) -> list[_Finding]:
    findings: list[_Finding] = []
    package_root = team_dir / _FIRST_PARTY_PACKAGE
    for path in py_files:
        # Only files inside the pgai_agent_kanban package use relative
        # imports that this check can resolve; absolute
        # "pgai_agent_kanban.*" imports can appear anywhere in the tree.
        in_package = package_root in path.parents or path == package_root
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except SyntaxError:
            # Already reported by _check_python_compile; skip to avoid a
            # duplicate finding for the same root cause.
            continue
        if verbose:
            print(f"  resolving imports: {path}", flush=True)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith(f"{_FIRST_PARTY_PACKAGE}."):
                        continue
                    if _module_to_path(team_dir, alias.name) is None:
                        findings.append(_Finding(
                            kind="import-resolution",
                            path=path,
                            line=node.lineno,
                            cause=f"import {alias.name} -- no module or package at that dotted path",
                        ))
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    if not in_package:
                        continue
                    dotted = _relative_target(path, team_dir, node.level, node.module)
                    if dotted is None:
                        continue
                elif node.module and node.module.startswith(f"{_FIRST_PARTY_PACKAGE}."):
                    dotted = node.module
                elif node.module == _FIRST_PARTY_PACKAGE:
                    dotted = node.module
                else:
                    continue
                if _module_to_path(team_dir, dotted) is None:
                    prefix = "." * (node.level or 0)
                    shown = f"{prefix}{node.module or ''}"
                    findings.append(_Finding(
                        kind="import-resolution",
                        path=path,
                        line=node.lineno,
                        cause=f"from {shown} import ... -- no module or package resolves to '{dotted}'",
                    ))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_KIND_LABEL = {
    "python-compile": "Python compile error",
    "bash-syntax": "Bash syntax error",
    "import-resolution": "Unresolved first-party import",
}


def _print_findings(findings: list[_Finding], team_dir: Path) -> None:
    print(
        f"\nFAIL: {len(findings)} breakage-class failure(s) found -- "
        "running tests would be meaningless until these are fixed.\n",
        flush=True,
    )
    print("Cause(s):", flush=True)
    for finding in findings:
        try:
            rel = finding.path.relative_to(team_dir)
        except ValueError:
            rel = finding.path
        loc = f"{rel}:{finding.line}" if finding.line else str(rel)
        print(f"  - {loc} — {_KIND_LABEL[finding.kind]}: {finding.cause}", flush=True)
    print("", flush=True)
    print("To fix:", flush=True)
    print("  1. Open each file named above at the given line and correct the cause shown.", flush=True)
    print("  2. python3 team/scripts/check_breakage_class.py   # re-run this gate to confirm", flush=True)
    print("", flush=True)
    print(
        "Rule: breakage-class failures (syntax errors, compile failures, "
        "unresolved first-party imports) always block before pytest runs -- "
        "unlike quality-class lint findings, which are filed and never "
        "block (see team/scripts/lib/gated_runner_lint.sh).",
        flush=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_breakage_class.py",
        description=(
            "Breakage-class pre-flight gate: Python compile errors, bash "
            "syntax errors, and unresolved pgai_agent_kanban imports. The "
            "only fatal pre-flight check in the gated runners -- see module "
            "docstring for the breakage/quality split."
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        help=(
            "Root of the team/ directory to scan.  Default: parent of this "
            "script's directory (team/scripts/ -> team/)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each file as it is checked.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the breakage-class check over the team tree and print each failure.

    Exit codes: 0 no breakage-class failure; 1 one or more (syntax errors,
    compile failures, unresolved first-party imports), listed on stdout;
    2 usage error, such as a ``--team-dir`` that does not exist.
    """
    args = _parse_args(argv)

    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        script_dir = Path(__file__).resolve().parent  # team/scripts/
        team_dir = script_dir.parent                  # team/

    if not team_dir.is_dir():
        print(
            f"ERROR: team directory not found: {team_dir}\n"
            "Use --team-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    py_files = _collect_files(team_dir, "*.py")
    sh_files = _collect_files(team_dir, "*.sh")

    print(
        f"check_breakage_class: scanning {len(py_files)} python file(s) and "
        f"{len(sh_files)} shell file(s) under {team_dir}",
        flush=True,
    )

    findings: list[_Finding] = []
    findings.extend(_check_python_compile(py_files, args.verbose))
    findings.extend(_check_bash_syntax(sh_files, args.verbose))
    findings.extend(_check_import_resolution(py_files, team_dir, args.verbose))

    findings.sort(key=lambda f: (str(f.path), f.line or 0))

    if findings:
        _print_findings(findings, team_dir)
        return 1

    print(
        f"check_breakage_class: OK — {len(py_files)} python file(s), "
        f"{len(sh_files)} shell file(s) checked, 0 breakage-class failures.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

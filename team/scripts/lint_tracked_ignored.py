#!/usr/bin/env python3
"""
lint_tracked_ignored.py
=======================
Reports tracked files the repository's own ignore rules say should not be
tracked, and tracked files of a generated kind (logs, locks, coverage
output), so a log or coverage file an agent's commit swept in is caught
before verification.

WHAT IS REPORTED
----------------
``tracked-ignored``   every file ``git ls-files -ci --exclude-standard``
                      lists: tracked, yet matched by a ``.gitignore`` rule
                      (an ignore rule added after the file was committed,
                      or a file force-added past one).
``generated-file``    every tracked file whose name marks it as generated
                      output: ``*.log``, ``*.lock``, ``.coverage``,
                      ``.coverage.*``, ``coverage*.json``,
                      ``*-coverage.json``, ``*.orig``, ``nohup.out``, and
                      rotated task artifacts (``*.previous-RUN-*``). With
                      ``--base REF``, only files added since REF
                      (``git diff --diff-filter=A REF...HEAD``) are checked
                      for this kind; without it, every tracked file is.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``SOP-core.md`` "Gates and Refusals" and "Blocking
Severity: only real damage blocks"): a tracked log is untidy, never a
runtime failure. The lint narrates each finding, writes them to
``--findings-file``, and ALWAYS exits 0. The pre-merge check run
(``team/pgai_agent_kanban/testing/pre_merge.py``) runs it beside the
ratchet lints and treats any finding as a failed check there.

OUTPUT
------
One line per finding, then a totals line::

    lint_tracked_ignored: [tracked-ignored] team/x.log is tracked but an ignore rule matches it
    lint_tracked_ignored: totals: <N> tracked file(s); <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (keys
``lint``, ``severity``, ``kind``, ``file``, ``line``, ``summary``,
``remedy``), always, as ``[]`` when there is nothing to report.

USAGE
-----
  python3 team/scripts/lint_tracked_ignored.py [--root PATH] [--base REF] [--findings-file PATH]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

LINT_NAME = "tracked-ignored"

#: File-name patterns that mark a tracked file as generated output.
GENERATED_PATTERNS: tuple[str, ...] = (
    "*.log",
    "*.lock",
    ".coverage",
    ".coverage.*",
    "coverage*.json",
    "*-coverage.json",
    "*.orig",
    "nohup.out",
    "*.previous-RUN-*",
)

class Finding(NamedTuple):
    """One reported file."""

    kind: str
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """The finding in the gated runners' findings-file shape."""
        return {"lint": LINT_NAME, "severity": "quality", **self._asdict()}


def _git(root: Path, *args: str) -> list[str]:
    """Run ``git -C root ARGS`` and return its NUL-separated output entries.

    Raises:
        RuntimeError: git is missing or the command failed (its stderr named).
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=False
        )
    except OSError as exc:
        raise RuntimeError(f"git could not run: {exc}") from exc
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed")
    return [entry for entry in done.stdout.decode("utf-8", "replace").split("\0") if entry]


def is_generated(path: str) -> bool:
    """True when *path*'s file name matches one of :data:`GENERATED_PATTERNS`."""
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in GENERATED_PATTERNS)


def _untrack(path: str) -> str:
    return f"git rm --cached -- {shlex.quote(path)} && python3 team/scripts/lint_tracked_ignored.py"


def collect(root: Path, base: str | None = None) -> tuple[int, list[Finding]]:
    """Return the number of tracked files under *root* and the findings.

    Raises:
        RuntimeError: *root* is not a git work tree, or *base* is not a ref.
    """
    tracked = _git(root, "ls-files", "-z")
    findings: list[Finding] = []
    for path in _git(root, "ls-files", "-z", "-ci", "--exclude-standard"):
        findings.append(Finding(
            "tracked-ignored", path, 0,
            f"{path} is tracked but an ignore rule matches it "
            f"(see: git check-ignore -v --no-index -- {shlex.quote(path)})",
            f"untrack it, or narrow the ignore rule if the file belongs in the repository: {_untrack(path)}",
        ))
    candidates = (
        _git(root, "diff", "-z", "--name-only", "--diff-filter=A", f"{base}...HEAD") if base else tracked
    )
    ignored = {f.file for f in findings}
    for path in candidates:
        if path in ignored or not is_generated(path):
            continue
        findings.append(Finding(
            "generated-file", path, 0,
            f"{path} is a generated file (logs, locks and coverage output are never committed)",
            f"untrack it and keep it out with an ignore rule: {_untrack(path)}",
        ))
    return len(tracked), findings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_tracked_ignored.py",
        description=(
            "Report tracked files an ignore rule matches, and tracked generated files "
            "(logs, locks, coverage output). Quality class: always exits 0."
        ),
    )
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root (default: two levels above this script's directory).")
    parser.add_argument("--base", metavar="REF", default=None,
                        help="Check only files added since REF for generated kinds "
                             "(default: every tracked file).")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                        help="Write findings as a JSON array to PATH (always written).")
    return parser


def _write_findings(path_str: str | None, findings: list[Finding]) -> None:
    if not path_str:
        return
    path = Path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([f.to_dict() for f in findings], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"lint_tracked_ignored: could not write findings file {path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the lint. Always returns 0."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent.parent
    try:
        total, findings = collect(root, args.base)
    except RuntimeError as exc:
        print(f"lint_tracked_ignored: could not list tracked files under {root}: {exc}", file=sys.stderr)
        _write_findings(args.findings_file, [])
        return 0
    _write_findings(args.findings_file, findings)
    for f in findings:
        print(f"lint_tracked_ignored: [{f.kind}] {f.summary}")
    print(f"lint_tracked_ignored: totals: {total} tracked file(s); {len(findings)} finding(s)")
    if findings:
        message = (
            f"{len(findings)} tracked file(s) that an ignore rule matches or that are generated "
            "output; see the lines printed above"
        )
        remedy = findings[0].remedy.split(": ", 1)[-1]
        # CENSUS-ANCHOR: lint-tracked-ignored-01
        # CONVERT: a quality-class finding with zero runtime-failure risk --
        # narrated and filed, never fatal; returns 0 below.
        sys.stdout.flush()
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract("lint.tracked-ignored", message, remedy)
        except ImportError:
            print(f"error:   lint.tracked-ignored\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

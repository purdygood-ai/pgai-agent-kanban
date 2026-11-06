#!/usr/bin/env python3
"""
lint_bash_shared_blocks.py
==========================
Reports a copy of a bash block that has one home in ``team/scripts/lib/``,
pasted back into a script instead of calling its helper.

WHAT IS CHECKED
---------------
Each shared block below is recognised by a signature: a line (or a few
lines) that only the block itself carries. A signature found anywhere
under the scanned directory other than the block's home file is a copy.

  ============================  ============================  ====================================
  block                         home (under team/scripts/)    call instead
  ============================  ============================  ====================================
  help-prelude                  lib/header_help.sh            header_help_exit_if_requested
  overwatch-project             lib/overwatch_config.sh       overwatch_resolve_check_project
  runner-unknown-arg            lib/gated_runner_lint.sh      gated_runner_reject_unknown_arg
  runner-pytest-missing         lib/gated_runner_lint.sh      gated_runner_report_pytest_missing
  runner-workers-refusal        lib/gated_runner_args.sh      gated_runner_parse_args
  verify-harness                lib/verify_harness.sh         verify_harness_init / pass / fail
  repo-root-walk                lib/dev_tree.sh               resolve_script_repo_root
  project-repo-root             lib/project_paths.sh          pp_load_project_repo_root
  wake-argument-loop            lib/wake_entry.sh             the entry body (set WAKE_ENTRY_*,
                                                              then source it)
  status-logger                 lib/script_common.sh          script_log_init (with info / ok /
                                                              warn / err / die)
  ============================  ============================  ====================================

``runner-workers-refusal`` is the gated runners' ``--workers`` refusal, the
marker of their shared flag contract; ``wake-argument-loop`` is a wake entry
point's argument loop (its deprecated-positional-agent warning), the marker
of the shared wake entry body. ``status-logger`` is an ``info`` / ``ok`` /
``warn`` / ``err`` / ``die`` function that echoes a ``C_*`` colour itself
instead of calling the shared logger, whether it is written on one line or
spread over several (the ``echo`` / ``printf`` on any line before the
function's closing ``}`` line).

``help-prelude`` is checked in ``dashboard/*.sh`` only, the scripts whose
help it serves: a ``for __help_arg in "$@"`` scan, or a ``-h|--help)`` case
arm that prints the header itself (``header_help_print``, or a
``mapfile -t __help_lines`` reader). Every other block is checked in every
``*.sh`` under the scanned directory and, when that directory is a
repository's ``team/scripts``, in the shipped repo-root scripts
``install.sh`` and ``docker/entrypoint.sh`` too. Comment lines never match.

OUTPUT AND EXIT CODES
---------------------
One line per copy, ``<file>:<line>: <block> copied; call <helper> from
<home> instead``, then a totals line. ``--findings-file PATH`` also writes
the copies as a JSON array (keys ``lint``, ``severity``, ``kind``,
``file``, ``line``, ``summary``, ``remedy``), ``[]`` when there are none.

  0   no copy found
  1   at least one copy found
  2   the scanned directory does not exist

The unit suite runs this over the tree (``tests/unit/
test_lint_bash_shared_blocks.py``), so a reappearing copy fails it.

USAGE
-----
  python3 team/scripts/lint_bash_shared_blocks.py [--scripts-dir DIR]
                                                  [--findings-file PATH]

  --scripts-dir DIR      The team/scripts directory to scan (default: the
                         directory this script is in). When DIR is
                         <repo>/team/scripts, <repo>/install.sh and
                         <repo>/docker/entrypoint.sh are scanned too.
  --findings-file PATH   Also write the copies as a JSON array to PATH.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

LINT_NAME = "bash_shared_blocks"
RUN_COMMAND = "python3 team/scripts/lint_bash_shared_blocks.py"


class Block(NamedTuple):
    """One shared block: its signature, where it lives and what to call."""

    name: str
    home: str
    helper: str
    signature: re.Pattern[str]
    dashboard_only: bool = False


BLOCKS: tuple[Block, ...] = (
    Block(
        "help-prelude",
        "lib/header_help.sh",
        "header_help_exit_if_requested",
        re.compile(
            r'^[ \t]*for __help_arg in "\$@"'
            r"|^[ \t]*(?:-h\|--help|--help\|-h)\)[ \t]*\n(?:[ \t]*#[^\n]*\n)*"
            r'(?:[ \t]*source [^\n]*header_help\.sh"?[ \t]*\n)?'
            r"[ \t]*(?:header_help_print\b|mapfile -t __help_lines\b)",
            re.M,
        ),
        dashboard_only=True,
    ),
    Block(
        "overwatch-project",
        "lib/overwatch_config.sh",
        "overwatch_resolve_check_project",
        re.compile(r"^[^#\n]*OVERWATCH_PROJECT (?:is )?not set and multiple projects", re.M),
    ),
    Block(
        "runner-unknown-arg",
        "lib/gated_runner_lint.sh",
        "gated_runner_reject_unknown_arg",
        re.compile(r"^[^#\n]*FAIL: unrecognized argument '", re.M),
    ),
    Block(
        "runner-pytest-missing",
        "lib/gated_runner_lint.sh",
        "gated_runner_pytest_available / gated_runner_report_pytest_missing",
        re.compile(r"^[^#\n]*FAIL: pytest is not installed in this Python environment", re.M),
    ),
    Block(
        "runner-workers-refusal",
        "lib/gated_runner_args.sh",
        "gated_runner_parse_args",
        re.compile(r"^[^#\n]*--workers needs a non-negative whole number", re.M),
    ),
    Block(
        "verify-harness",
        "lib/verify_harness.sh",
        "verify_harness_init (with pass / fail / warn)",
        re.compile(r"^[^#\n]*PASS\$\{C_RESET(?::-)?\}: \$\*", re.M),
    ),
    Block(
        "repo-root-walk",
        "lib/dev_tree.sh",
        "resolve_script_repo_root",
        re.compile(
            r'^[^#\n]*while \[\[ "\$\{?\w+\}?" != "/" \]\] && \[\[ ! -d "\$\{?\w+\}?/\.git" \]\]',
            re.M,
        ),
    ),
    Block(
        "project-repo-root",
        "lib/project_paths.sh",
        "pp_load_project_repo_root",
        re.compile(r"^[^#\n]*could not load project\.cfg for project '", re.M),
    ),
    Block(
        "wake-argument-loop",
        "lib/wake_entry.sh",
        "the entry body (set WAKE_ENTRY_*, then source it)",
        re.compile(r"^[^#\n]*positional agent argument is deprecated", re.M),
    ),
    Block(
        "status-logger",
        "lib/script_common.sh",
        "script_log_init (with info / ok / warn / err / die)",
        re.compile(
            # The definition head. Its `{` may sit on the next line once the
            # head is a definition by itself (`name()` or `function name`).
            r"^[ \t]*(?:function[ \t]+(?:info|ok|warn|err|die)[ \t]*(?:\(\)[ \t]*)?(?:\n[ \t]*)?"
            r"|(?:info|ok|warn|err|die)[ \t]*(?:\(\)[ \t]*(?:\n[ \t]*)?)?)\{"
            # Either the body continues on the same line, or the `{` ends its
            # line and any body lines before the closing `}` line come first.
            r"(?:[ \t]*(?:#[^\n]*)?\n(?:(?![ \t]*\})[^\n]*\n)*?)?"
            # The colour-echoing line itself, never a comment.
            r"[ \t]*(?:[^#\s}][^\n]*?)?\b(?:echo|printf)\b[^\n]*\$\{?C_[A-Z_]+",
            re.M,
        ),
    ),
)

# Shipped scripts at the repository root, outside team/scripts, that a
# shared block could be pasted into. Paths are relative to the repo root.
ROOT_SCRIPTS: tuple[str, ...] = ("install.sh", "docker/entrypoint.sh")


class Finding(NamedTuple):
    """One copied block."""

    block: Block
    file: str
    line: int

    def summary(self) -> str:
        """The one-line report for this copy: where it is, which block, and
        the helper to call from the block's home instead."""
        return (
            f"{self.file}:{self.line}: {self.block.name} copied; call "
            f"{self.block.helper} from {self.block.home} instead"
        )

    def remedy(self) -> str:
        """The fix for this copy: replace it with a call to the block's
        helper, then re-run the lint."""
        return (
            f"replace the copy in {self.file} with a call to {self.block.helper} "
            f"(team/scripts/{self.block.home}), then re-run: {RUN_COMMAND}"
        )


def _scan_targets(scripts_dir: Path) -> list[tuple[Path, str | None, str]]:
    """The files to scan, each as (path, path relative to *scripts_dir* or
    None for a repo-root script, path as reported): every ``*.sh`` under
    *scripts_dir* in path order, then each existing ``ROOT_SCRIPTS`` file
    when *scripts_dir* is a repository's ``team/scripts``."""
    targets: list[tuple[Path, str | None, str]] = []
    for path in sorted(scripts_dir.rglob("*.sh")):
        if path.is_file():
            rel = path.relative_to(scripts_dir).as_posix()
            targets.append((path, rel, f"team/scripts/{rel}"))
    if scripts_dir.name == "scripts" and scripts_dir.parent.name == "team":
        repo_root = scripts_dir.parent.parent
        for rel in ROOT_SCRIPTS:
            path = repo_root / rel
            if path.is_file():
                targets.append((path, None, rel))
    return targets


def scan(scripts_dir: Path) -> list[Finding]:
    """Every copy of a shared block under *scripts_dir* and in the shipped
    repo-root scripts beside it, in path order."""
    findings: list[Finding] = []
    for path, rel, shown in _scan_targets(scripts_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # not readable text: nothing a shell block could be copied into
        for block in BLOCKS:
            if rel == block.home:
                continue
            if block.dashboard_only and not (rel and rel.startswith("dashboard/") and rel.count("/") == 1):
                continue
            for match in block.signature.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(Finding(block, shown, line))
    return findings


def _findings_json(findings: list[Finding]) -> list[dict]:
    return [
        {
            "lint": LINT_NAME,
            "severity": "low",
            "kind": f.block.name,
            "file": f.file,
            "line": f.line,
            "summary": f.summary(),
            "remedy": f.remedy(),
        }
        for f in findings
    ]


def main(argv: list[str] | None = None) -> int:
    """Scan --scripts-dir, print one line per copy and a totals line, and
    write --findings-file when given; return 0 with no copy, 1 with at
    least one, 2 when the scanned directory does not exist."""
    parser = argparse.ArgumentParser(
        description="Report bash blocks copied out of their one home in team/scripts/lib/.",
    )
    parser.add_argument(
        "--scripts-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help=(
            "the team/scripts directory to scan (default: this script's directory); "
            "when it is <repo>/team/scripts, <repo>/install.sh and <repo>/docker/entrypoint.sh are scanned too"
        ),
    )
    parser.add_argument(
        "--findings-file",
        type=Path,
        help="also write the copies as a JSON array to this path",
    )
    args = parser.parse_args(argv)

    scripts_dir = args.scripts_dir.resolve()
    if not scripts_dir.is_dir():
        print(
            f"lint_bash_shared_blocks: --scripts-dir {scripts_dir} is not a directory; "
            f"pass the team/scripts directory, e.g. {RUN_COMMAND} --scripts-dir team/scripts",
            file=sys.stderr,
        )
        return 2

    findings = scan(scripts_dir)
    for finding in findings:
        print(f"lint_bash_shared_blocks: {finding.summary()}")
    print(f"lint_bash_shared_blocks: totals: {len(BLOCKS)} block signature(s); {len(findings)} copy(ies)")
    if args.findings_file:
        args.findings_file.write_text(json.dumps(_findings_json(findings), indent=2) + "\n", encoding="utf-8")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

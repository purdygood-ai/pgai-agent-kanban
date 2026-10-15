#!/usr/bin/env python3
"""
lint_block_record_sites.py
==========================
Reports every site in the shipped tree that writes a task's ``## State`` as
``BLOCKED`` without recording why in the task's ``## Block Record``.

Block triage can act only on a block it can classify, and the classification
lives in the ``## Block Record`` the shared helper writes
(``block_record_write`` in ``team/scripts/lib/block_record.sh``; in Python,
``write_block_record`` / ``render_block_record`` from
``pgai_agent_kanban.overwatch.block_record``). A new BLOCKED write that
skips the helper ships a block nobody can triage; this lint names it.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Blocking Severity: only real
damage blocks"). A block without a record is still a block -- the task stops
exactly as before and a human can read ``## Blockers`` -- so a missing record
costs triage, never a running system. The lint narrates each finding, writes
them to ``--findings-file`` for the gated runner's aggregate, and ALWAYS
exits 0.

WHAT IS A BLOCKED WRITE
-----------------------
Shell files (comment lines are skipped):

  * ``set_state <file> BLOCKED`` (the wake's ## State writer);
  * ``transition_task_state <id> <file> BLOCKED`` (the state + queue-marker
    writer), alone or after ``if`` / ``!``;
  * ``set_block <file> State BLOCKED``;
  * a ``render`` / ``set-section`` call whose arguments name ``--section``
    ``State`` with ``--body`` ``BLOCKED`` on the same line.

``BLOCKED`` may be bare or quoted. A state held in a variable
(``transition_task_state "$id" "$f" "$state"``) is not recognised: this is
a textual scan, and those generic call sites forward a state some other
site chose.

Python files: a line of code carrying two string literals whose whole
values are ``State`` and ``BLOCKED``, and no comparison (``==``, ``!=``,
``in``) -- the shape of ``set_section(path, "State", "BLOCKED")`` or
``{"State": "BLOCKED"}``. The file is tokenized, so docstrings, comments and
longer strings that merely mention the words never count.

Not a candidate: ``task-status.sh ... --state BLOCKED`` and
``write_task_status(..., state="BLOCKED")``. That is the status-writer tool,
which records the Block Record itself on every BLOCKED write.

WHAT CALLS THE HELPER
---------------------
A site is compliant when ``block_record_write`` (shell) or
``write_block_record`` / ``render_block_record`` (Python) appears within
``FORWARD_WINDOW`` lines from the site's own line onward, or within
``BACKWARD_WINDOW`` lines above it -- a block site writes its record beside
its BLOCKED transition, usually just after the ``## Blockers`` and
``## Needs Human`` lines.

WHAT IS SCANNED
---------------
Every ``*.sh`` and ``*.py`` file under ``team/`` (relative to ``--root``),
except test code -- any file with a directory named ``tests``, ``test`` or
``fixtures`` between ``team/`` and the file, since test fixtures write
BLOCKED states on purpose -- and the Block Record helper's own two files.
``--paths`` scans an explicit file list instead (used by this lint's tests
against synthetic fixtures).

OUTPUT
------
One line per finding, then a totals line:

    lint_block_record_sites: <file>:<line>: <what was found> -- <fix>
    lint_block_record_sites: totals: <N> file(s) scanned; <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (keys
``lint``, ``severity``, ``kind``, ``file``, ``line``, ``summary``,
``remedy``) in the shape ``team/scripts/lib/gated_runner_lint.sh``'s
``gated_runner_merge_findings_file`` merges; ``[]`` when clean.

USAGE
-----
  python3 team/scripts/lint_block_record_sites.py [--root PATH]
                                                  [--paths FILE [FILE ...]]
                                                  [--findings-file PATH]

  --root PATH           Repository root to scan (default: two levels above
                        this script's directory).
  --paths FILE ...      Scan exactly these files instead of the tree.
  --findings-file PATH  Write findings as a JSON array to PATH.

EXIT CODES
----------
  0   Always, whatever was found (quality class).
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple

LINT_NAME = "block_record_sites"
SCAN_TOP = "team"
TEST_DIR_NAMES = frozenset({"tests", "test", "fixtures"})
HELPER_RELS = frozenset({
    "team/scripts/lib/block_record.sh",
    "team/pgai_agent_kanban/overwatch/block_record.py",
})

#: Lines from the site's own line onward searched for the helper call.
FORWARD_WINDOW = 14
#: Lines above the site searched for the helper call.
BACKWARD_WINDOW = 3

FIX = (
    "record the block beside it: block_record_write \"$status_file\" <reason_code> "
    "\"$(block_record_owner_role \"$status_file\")\" ... || log \"WARNING: Block Record not written\" "
    "(team/scripts/lib/block_record.sh; Python: write_block_record)"
)
REMEDY = "python3 team/scripts/lint_block_record_sites.py"

_BLOCKED = r"""["']?BLOCKED["']?(?![\w-])"""
_ARG = r"""(?:"[^"]*"|'[^']*'|\S+)"""
_CMD_START = r"""^\s*(?:(?:if|!|then|else|do)\s+)*"""

_SHELL_SITE_RES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("set_state", re.compile(_CMD_START + r"set_state\s+" + _ARG + r"\s+" + _BLOCKED)),
    (
        "transition_task_state",
        re.compile(_CMD_START + r"transition_task_state\s+" + _ARG + r"\s+" + _ARG + r"\s+" + _BLOCKED),
    ),
    ("set_block State", re.compile(_CMD_START + r"set_block\s+" + _ARG + r"""\s+["']?State["']?\s+""" + _BLOCKED)),
    (
        "--section State --body BLOCKED",
        re.compile(r"""--section[=\s]+["']?State["']?\s+.*--body[=\s]+""" + _BLOCKED),
    ),
)
_SHELL_HELPER_RE = re.compile(r"\bblock_record_write\b")

# The literal pair a Python BLOCKED write carries, each on its own line so
# this definition is not itself a site.
_STATE_HEADING = "State"
_BLOCKED_STATE = "BLOCKED"
_PY_SITE_LITERALS = frozenset({_STATE_HEADING, _BLOCKED_STATE})
_PY_COMPARISONS = frozenset({"==", "!=", "in"})
_PY_HELPER_RE = re.compile(r"\b(?:write_block_record|render_block_record)\b")


class Finding(NamedTuple):
    """One BLOCKED write with no Block Record helper call beside it."""

    file: str
    line: int
    summary: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity``, ``kind``, ``file``, ``line``, ``summary``, ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": "blocked-write-without-block-record",
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": REMEDY,
        }


def _helper_nearby(lines: list[str], index: int, helper: "re.Pattern[str]") -> bool:
    start = max(0, index - BACKWARD_WINDOW)
    end = min(len(lines), index + FORWARD_WINDOW)
    return any(helper.search(line) for line in lines[start:end])


def scan_shell(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, description)`` for each unrecorded BLOCKED write in shell *text*."""
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for label, pattern in _SHELL_SITE_RES:
            if pattern.search(raw):
                if not _helper_nearby(lines, index, _SHELL_HELPER_RE):
                    hits.append((index + 1, f"{label} writes BLOCKED with no block_record_write call: {stripped[:120]}"))
                break
    return hits


def _python_site_lines(text: str) -> list[int]:
    """0-based indexes of lines whose code writes the State/BLOCKED literal pair.

    Reads the token stream: per physical line, the whole values of its
    string literals and whether it compares. A file that does not tokenize
    yields no sites (the lint is advisory and never guesses).
    """
    literals: dict[int, set[str]] = {}
    compares: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            row = tok.start[0] - 1
            if tok.type == tokenize.STRING:
                try:
                    value = ast.literal_eval(tok.string)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, str):
                    literals.setdefault(row, set()).add(value)
            elif tok.type in (tokenize.OP, tokenize.NAME) and tok.string in _PY_COMPARISONS:
                compares.add(row)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    return sorted(
        row for row, values in literals.items()
        if _PY_SITE_LITERALS <= values and row not in compares
    )


def scan_python(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, description)`` for each unrecorded BLOCKED write in Python *text*."""
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for index in _python_site_lines(text):
        stripped = lines[index].strip() if index < len(lines) else ""
        if not _helper_nearby(lines, index, _PY_HELPER_RE):
            hits.append((index + 1, f"writes State BLOCKED with no write_block_record call: {stripped[:120]}"))
    return hits


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Scan one file by its suffix; an unreadable file yields nothing."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_python(text) if path.suffix == ".py" else scan_shell(text)


def _scanned_files(root: Path) -> list[Path]:
    top = root / SCAN_TOP
    if not top.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(top.rglob("*")):
        if path.suffix not in (".sh", ".py") or not path.is_file():
            continue
        if any(part in TEST_DIR_NAMES for part in path.relative_to(top).parts[:-1]):
            continue
        if path.relative_to(root).as_posix() in HELPER_RELS:
            continue
        files.append(path)
    return files


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def run(root: Path, paths: "list[Path] | None" = None) -> tuple[int, list[Finding]]:
    """Scan *paths* (default: the tree under *root*); return ``(files_scanned, findings)``."""
    files = paths if paths is not None else _scanned_files(root)
    findings: list[Finding] = []
    for path in files:
        rel = _display(path, root)
        findings.extend(Finding(rel, number, desc) for number, desc in scan_file(path))
    return len(files), findings


def main(argv: "list[str] | None" = None) -> int:
    """Run the Block Record sites lint, printing each finding and writing ``--findings-file``.

    Exit codes: 0 always, whatever was found (quality class); 2 usage error (argparse).
    """
    parser = argparse.ArgumentParser(
        prog="lint_block_record_sites.py",
        description=(
            "Report BLOCKED state writes that do not record a ## Block Record "
            "through block_record_write (quality class; always exits 0)."
        ),
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="repository root to scan (default: this checkout)",
    )
    parser.add_argument("--paths", nargs="+", default=None, metavar="FILE",
                        help="scan exactly these files instead of the tree")
    parser.add_argument("--findings-file", default=None, help="write findings as a JSON array here")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    paths = [Path(p) for p in args.paths] if args.paths else None
    scanned, findings = run(root, paths)
    for finding in findings:
        print(f"lint_{LINT_NAME}: {finding.file}:{finding.line}: {finding.summary} -- {FIX}")
    print(f"lint_{LINT_NAME}: totals: {scanned} file(s) scanned; {len(findings)} finding(s)"
          + (" (non-blocking finding)" if findings else ""))

    if args.findings_file:
        out = Path(args.findings_file)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps([f.to_dict() for f in findings], indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"lint_{LINT_NAME}: could not write findings file {out}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

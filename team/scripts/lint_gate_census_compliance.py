#!/usr/bin/env python3
"""
lint_gate_census_compliance.py
================================
Quality-class lint: enforces the new-gate rule (``team/SOP.md``, "Gates and
Refusals" -> "New-gate rule (for every future gate)"). Any gate, guard, or
refusal added to the shipped tree must, at its refusal site:

  1. Declare its census classification in a code comment -- one of
     ``BLOCK-WORTHY``, ``CONVERT``, or ``AGENT-RESOLVABLE`` (the vocabulary
     ``docs/blocking-site-census.md`` defines).
  2. Carry the dual-channel error contract -- call the shared emitter
     (``team/scripts/lib/error_contract.sh`` /
     ``pgai_agent_kanban.lib.error_contract``) so the refusal speaks both a
     machine-greppable key and a stranger-runnable remedy.

This lint is the static, always-run enforcement mechanism the SOP names: it
scans candidate refusal sites (fatal, nonzero process exits) and reports any
site that is missing either signal.

WHAT IS CHECKED
---------------
A candidate refusal site is a line matching a fatal, nonzero, literal exit:

  Bash    ``exit N`` where N is a literal digit 1-9 (``exit 0`` is a normal
          success exit, not a refusal, and is never a candidate).
  Python  ``sys.exit(N)`` or ``raise SystemExit(N)`` where N is a literal
          digit 1-9.

For each candidate site, a fixed-size backward window (the site's own line
plus the several lines immediately above it) is inspected for:

  - a comment containing one of the three census classification tokens
    (``BLOCK-WORTHY``, ``CONVERT``, ``AGENT-RESOLVABLE``), and
  - a call to one of the dual-channel emitter functions (bash:
    ``pgai_format_error_contract``, ``pgai_emit_error_contract``; python:
    ``format_error_contract``, ``emit_error_contract``).

A site missing either signal is a finding naming which signal(s) are absent.
A site carrying both signals produces no finding.

SCOPE DECISION (documented)
----------------------------
This is a textual, line-window scan, not a control-flow or data-flow
analysis -- consistent with the other static lints in this fleet (see
``lint_dual_channel_pair.py``). Within a scanned file it does not
distinguish a refusal that is part of the autonomous loop's gate fleet from
some other fatal exit; both are candidate sites. A false positive here
costs the reader nothing more than a filed, non-blocking finding -- the
lint never stops a build, so precision is traded for simplicity rather than
for correctness under the doctrine.

Which FILES are scanned is a separate, file-level scope decision, and it
is kept in lockstep with ``docs/blocking-site-census.md``: by default this
lint scans exactly the bash and Python files the census's "At-a-glance
rollup" table names as loop-bearing sources (``_CENSUS_BASH_FILES`` /
``_CENSUS_PYTHON_FILES`` below), resolved relative to this script's own
directory (``team/scripts/``). The census's own scope decision (see
"Scope decision (documented)" in the census doc) excludes pure
operator-CLI argument/usage refusals in interactive utilities (``show.sh``,
``delete.sh``, ``close.sh``, ``wontdo.sh``, and the like) because the
autonomous loop never invokes them, so they can never halt it -- this lint
mirrors that exclusion by simply not including those files in its default
scan surface, rather than reproducing an operator-CLI detection heuristic.
Adding a new loop-bearing gate to a file not yet in the two lists means
also adding that file to the list (and, per the New-gate rule, to the
census itself) -- a maintained include-list, not an automatic one. The
``--bash-dirs``/``--python-dir`` flags remain available to scan a broader
or narrower surface deliberately (e.g. a full-repo sweep during a census
refresh).

SITE-TYPE SCOPE (within an in-scope file)
------------------------------------------
The census's "Scope decision (documented)" exclusion for pure operator-CLI
argument/usage refusals is not only a FILE-level decision -- it is a
SITE-TYPE decision, and an in-scope file (e.g. ``cm/release.sh``) can still
contain sites of the excluded type (its own ``--project`` flag parser). A
refusal that can only fire from a human typing a malformed command line at
a terminal can never halt the autonomous loop, because the loop always
invokes these scripts with well-formed arguments -- so it is not a
candidate site at all, in any file. Two textual shapes are recognized and
pre-filtered before the classification/emitter check runs (see
``_is_operator_cli_bash_site`` / ``_bash_positional_case_ranges`` /
``_window_has_usage_guard``):

  1. The exit is lexically inside a ``case`` block whose dispatch
     expression is a positional CLI parameter (``$1``, ``${1}``,
     ``${1:-}``) -- the shell idiom for flag/argument dispatch, whether
     wrapped in a ``while [[ $# -gt 0 ]]; do ... done`` parsing loop or a
     bare one-shot dispatcher (e.g. a test-harness shim that emulates a
     real CLI, such as the fake ``crontab`` seam in the gated runners).
  2. The exit's nearby backward window (the same ``_WINDOW_LINES`` window
     used for the classification/emitter check) names "usage" -- a call to
     a ``*_usage`` helper, or a printed "Usage:" banner / "run with --help"
     pointer -- covering the required-flag validations that sit just after
     the parsing loop rather than inside its ``case`` body.

A false negative here (an excluded site that was in fact loop-reachable)
costs nothing more than a missed advisory finding on a quality-class lint
that never blocks a build; a false positive (flagging a genuine
operator-CLI refusal) is exactly the clean-tree flood this pre-filter
exists to prevent. Both textual shapes above were chosen because they
name the concrete site pattern (an argument dispatcher; a usage-guarded
validation), not because they match specific error wording -- wording is
expected to drift and a phrase-match would silently stop recognizing the
shape.

The window is backward-only (the classification comment and the emitter
call are expected to precede or sit on the same line as the exit they
guard, matching how a gate is normally authored: state the class, emit the
refusal, then exit) and fixed-size (``_WINDOW_LINES``), so a signal placed
far above the exit it belongs to is not credited -- keep the classification
comment and the emitter call close to the exit they describe.

CENSUS-LOCATOR ANCHOR SCOPE
-----------------------------
A third scope decision, alongside the FILE-level and SITE-TYPE exclusions
above: an in-scope candidate exit whose exact ``(file, line)`` matches an
entry in ``docs/blocking-site-census.md``'s "Locator Index (lint-consumed)"
fenced block is treated as already tracked and classified at the
document level, and is exempt from the inline code-comment/emitter-call
check -- the same posture as the operator-CLI SITE-TYPE exclusion, just
anchored by the census document instead of a textual shape. See
``_load_census_locators`` and that section of the census doc for the full
rationale and the documented line-number-sensitivity trade-off. A candidate
exit NOT at one of these exact locators (a genuinely new, undocumented gate)
is still scanned exactly as described above -- this anchor narrows candidacy,
it does not widen the exclusion to "anything in an in-scope file."

QUALITY-CLASS POSTURE
----------------------
This lint NEVER exits non-zero. Findings print in full to stdout in the
three-part cause/fix/rule remedy shape every deterministic gate in this
codebase uses (``docs/coding-standards.md``, "Gates prescribe, not just
diagnose"), and are also emitted as a machine-readable JSON array (to
stdout, and optionally to a file via ``--findings-file``) so a caller --
human or automated -- can act on them without treating a non-zero exit as a
gate failure.

Exit codes
----------
  0   Always. See "Quality-class posture" above.

Usage
-----
  python3 team/scripts/lint_gate_census_compliance.py [--bash-dirs DIR [DIR ...]]
                                                        [--python-dir DIR]
                                                        [--findings-file PATH]
                                                        [--verbose]

  --bash-dirs DIR [DIR ...]  Directories to scan (non-recursively) for
                              ``*.sh`` candidate sites, OVERRIDING the
                              census-aligned default file list below.
  --python-dir DIR           Root directory to scan (recursively) for
                              ``*.py`` candidate sites, OVERRIDING the
                              census-aligned default file list below.
                              (Default without either flag: the exact bash
                              and Python files ``docs/blocking-site-census.md``
                              documents as loop-bearing -- see
                              ``_CENSUS_BASH_FILES`` / ``_CENSUS_PYTHON_FILES``
                              and "SCOPE DECISION" above. Passing either flag
                              switches that surface to a directory scan
                              instead, for an ad-hoc broader/narrower sweep.)
  --findings-file PATH       Write the findings as a JSON array to PATH
                              (parent directories created as needed). When
                              omitted, findings are still printed to stdout
                              as a JSON array; no file is written.
  --verbose, -v               Print each file name as it is scanned.

Examples
--------
  # From repo root, default scan surfaces:
  python3 team/scripts/lint_gate_census_compliance.py

  # Write findings to a file for a downstream pipeline to ingest:
  python3 team/scripts/lint_gate_census_compliance.py \\
      --findings-file "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/gate_census_compliance.json"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

CLASSIFICATION_TOKENS = ("BLOCK-WORTHY", "CONVERT", "AGENT-RESOLVABLE")

_SIGNAL_CENSUS = "census_classification"
_SIGNAL_DUAL_CHANNEL = "dual_channel_contract"
_SIGNAL_NAMES = (_SIGNAL_CENSUS, _SIGNAL_DUAL_CHANNEL)

# How many lines (including the exit line itself) count as "the refusal
# site" when looking for the two required signals.
_WINDOW_LINES = 6

_BASH_EMITTER_NAMES = ("pgai_format_error_contract", "pgai_emit_error_contract")
_PYTHON_EMITTER_NAMES = ("format_error_contract", "emit_error_contract")

_BASH_FATAL_EXIT_RE = re.compile(r"\bexit\s+[1-9]\d*\b")
_PYTHON_FATAL_EXIT_RE = re.compile(r"\b(?:sys\.exit|raise\s+SystemExit)\(\s*[1-9]\d*\b")

_CLASSIFICATION_RE = re.compile(
    r"#.*\b(?:" + "|".join(re.escape(t) for t in CLASSIFICATION_TOKENS) + r")\b"
)
_BASH_EMITTER_RE = re.compile(r"\b(?:" + "|".join(_BASH_EMITTER_NAMES) + r")\b")
_PYTHON_EMITTER_RE = re.compile(r"\b(?:" + "|".join(_PYTHON_EMITTER_NAMES) + r")\b")

# ---------------------------------------------------------------------------
# Operator-CLI SITE-TYPE exclusion (docs/blocking-site-census.md, "Scope
# decision (documented)"; see "SITE-TYPE SCOPE" in the module docstring).
# ---------------------------------------------------------------------------

# A `case` block whose dispatch expression is a positional CLI parameter --
# the shell idiom for flag/argument dispatch ($1, ${1}, ${1:-default}, ...).
_POSITIONAL_CASE_RE = re.compile(r'^\s*case\s+"?\$\{?1\b')

# Bash block openers/closers, tracked as a single aggregate depth counter to
# find a `case` block's matching `esac` -- valid because bash blocks nest
# strictly, so counting any opener/closer regardless of specific keyword
# still lands on the correct matching close for the block that was opened.
_BASH_BLOCK_OPENERS = frozenset({"case", "if", "while", "until", "for", "select"})
_BASH_BLOCK_CLOSERS = frozenset({"esac", "fi", "done"})
_BASH_BLOCK_KEYWORD_RE = re.compile(
    r"(?<![\w-])(" + "|".join(sorted(_BASH_BLOCK_OPENERS | _BASH_BLOCK_CLOSERS)) + r")(?![\w-])"
)

# A block keyword only counts when it sits in COMMAND POSITION: at the
# start of a line (after only whitespace) or immediately after a command
# separator (``;``, ``&``, ``|``, ``(``, ``{``) or a preceding compound-
# statement linking word (``then``, ``do``, ``else``, ``elif``). Without
# this check, prose inside a printed ``--help``/usage heredoc routinely
# contains ordinary English words that collide with these keywords (e.g.
# "...output while running.", "for directives they cover:", "Run with
# --help for usage.") and would desynchronize the block-depth counter,
# corrupting every range computed after the false hit. A *real* embedded
# script written via heredoc (e.g. a test-harness fake-CLI shim built with
# `cat > file <<'EOF' ... EOF`) is unaffected: its own keywords are still
# in command position within that embedded script's own lines.
_BASH_COMMAND_SEPARATOR_CHARS = frozenset(";&|({")
_BASH_COMMAND_PRECEDING_WORDS = frozenset({"then", "do", "else", "elif"})


def _bash_keyword_in_command_position(code: str, match_start: int) -> bool:
    prefix = code[:match_start].rstrip()
    if not prefix:
        return True
    if prefix[-1] in _BASH_COMMAND_SEPARATOR_CHARS:
        return True
    last_word = re.split(r"[^A-Za-z0-9_]+", prefix)[-1]
    return last_word in _BASH_COMMAND_PRECEDING_WORDS


# A "usage" reference in the window: a call to a *_usage helper (commonly
# underscore-prefixed/compound, e.g. `_ship_rc_usage`, so no LEFT word
# boundary is required -- only a following non-letter guards against
# matching a longer word that merely contains "usage" as a substring), a
# printed "Usage:" banner, or a "run with --help" pointer. Excludes the
# function's own DEFINITION line (bash: `name_usage() { ... }`; python:
# `def usage(...`) so a definition sitting inside a later exit's window is
# not mistaken for a guard call.
_USAGE_WORD_RE = re.compile(r"usage(?![a-z])", re.IGNORECASE)
_BASH_USAGE_DEFINITION_RE = re.compile(r"\b\w*usage\w*\s*\(\)", re.IGNORECASE)
_PYTHON_USAGE_DEFINITION_RE = re.compile(r"\bdef\s+\w*usage\w*\s*\(", re.IGNORECASE)

# The emitter's own definition files, and this lint's own file, are excluded
# from every scan surface -- they define or document the vocabulary and
# function names this lint searches for, but are not themselves gate sites.
_EXCLUDED_BASH_BASENAMES = frozenset({"error_contract.sh"})
_EXCLUDED_PYTHON_BASENAMES = frozenset({"error_contract.py", "conftest.py"})

# ---------------------------------------------------------------------------
# Census-aligned default scan scope (docs/blocking-site-census.md)
# ---------------------------------------------------------------------------
# The exact bash and Python files the census's "At-a-glance rollup" table
# names as sources of loop-bearing gate/lint sites (rows 1-52), as paths
# relative to this script's own directory (team/scripts/). This is the
# DEFAULT scan surface -- see "SCOPE DECISION (documented)" above. It is a
# maintained list: a newly-added loop-bearing gate in a file not listed here
# needs both a census row (docs/blocking-site-census.md) and an entry below
# to be picked up by the default scan. Pure operator-CLI utilities the
# census documents as out of scope (show.sh, delete.sh, close.sh, wontdo.sh,
# and the rest of team/scripts/'s interactive command surface) are absent
# from this list by construction -- they were never added, not filtered out.
_CENSUS_BASH_FILES = (
    "wake-batch.sh",  # rows 1-2
    "run-unit-tests.sh",  # rows 2, 26, 28, 51-52
    "run-integration-tests.sh",  # rows 2, 26, 28
    "run-gated-tests.sh",  # rows 26, 28
    "ship-rc.sh",  # rows 2, 24
    "verify-perproject-devtree-gate.sh",  # row 29
    "lint_skip_bug_gate.sh",  # row 44
    "audit_coding_standards.sh",  # row 45
    "coder-stale-literal-check.sh",  # row 46
    "lib/discovery.sh",  # rows 3-5
    "lib/wake_common.sh",  # rows 6-7
    "lib/dev_tree.sh",  # row 27
    "cm/release.sh",  # rows 8-24
    "cm/finalize-release.sh",  # row 16
    "harness/doc-lint.sh",  # rows 47-50
)

_CENSUS_PYTHON_FILES = (
    "check_breakage_class.py",  # row 25
    "lint_changelog_freshness.py",  # row 30
    "lint_comment_provenance.py",  # row 31
    "lint_icd_freshness.py",  # row 32
    "lint_icd_compat.py",  # row 33
    "lint_api_parity.py",  # row 34
    "lint_upgrade_no_temp_copy_guard.py",  # row 35
    "lint_help_presence.py",  # row 36
    "lint_python_import_completeness.py",  # row 37
    "lint_dashboard_network_git_gate.py",  # row 38
    "lint_env_bootstrap.py",  # row 39
    "lint_lib_function_dedupe.py",  # row 40
    "lint_orphan_tests.py",  # row 41
    "lint_test_anti_patterns.py",  # row 42
    "lint_dual_channel_pair.py",  # row 43
)

# ---------------------------------------------------------------------------
# Census locator anchor -- see "CENSUS-LOCATOR ANCHOR SCOPE" above
# ---------------------------------------------------------------------------
# The census document, relative to the repo root, and the fenced-block
# markers that delimit its machine-readable "Locator Index" section.
_CENSUS_DOC_RELATIVE_PATH = "docs/blocking-site-census.md"
_CENSUS_LOCATOR_BEGIN_MARKER = "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->"
_CENSUS_LOCATOR_END_MARKER = "<!-- CENSUS-LOCATOR-INDEX-END -->"

# One locator entry: "<path-relative-to-repo-root>:<line> <CLASS>".
_CENSUS_LOCATOR_LINE_RE = re.compile(
    r"^(?P<path>\S+):(?P<line>\d+)\s+(?P<cls>"
    + "|".join(re.escape(t) for t in CLASSIFICATION_TOKENS)
    + r")\s*$"
)


def _load_census_locators(repo_root: Path) -> dict[Path, set[int]]:
    """Parse the census's "Locator Index" fenced block into a mapping of
    resolved absolute file path -> the set of 1-indexed lines the census
    already tracks as classified refusal sites at that path (see
    "CENSUS-LOCATOR ANCHOR SCOPE" above and the census doc section of the
    same name). Fails soft to an empty mapping -- a missing census file, a
    missing/malformed fenced block, or an unparseable entry costs this run
    a narrower anchor set (candidate sites that would have been exempted
    are scanned normally instead), never a crash; this lint's own posture
    is "never exits non-zero" and a doc-parsing hiccup must not change
    that."""
    census_path = repo_root / _CENSUS_DOC_RELATIVE_PATH
    try:
        text = census_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    if _CENSUS_LOCATOR_BEGIN_MARKER not in text or _CENSUS_LOCATOR_END_MARKER not in text:
        return {}

    block = text.split(_CENSUS_LOCATOR_BEGIN_MARKER, 1)[1]
    block = block.split(_CENSUS_LOCATOR_END_MARKER, 1)[0]

    locators: dict[Path, set[int]] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _CENSUS_LOCATOR_LINE_RE.match(line)
        if not match:
            # An unparseable entry costs a missed anchor, not a crash -- see
            # the fail-soft rationale in this function's docstring.
            continue
        entry_path = (repo_root / match.group("path")).resolve()
        locators.setdefault(entry_path, set()).add(int(match.group("line")))
    return locators


@dataclass
class _Finding:
    """One candidate refusal site missing one or both required signals."""

    path: Path
    line: int
    snippet: str
    missing_signals: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bash scanning
# ---------------------------------------------------------------------------


def _strip_bash_comment(line: str) -> str:
    """Return *line* with any unquoted, unescaped trailing '#...' comment removed."""
    in_single = False
    in_double = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\" and not in_single and i + 1 < n:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if c == "#" and not in_single and not in_double:
            return line[:i]
        i += 1
    return line


def _window_has_classification(window_lines: list[str]) -> bool:
    return any(_CLASSIFICATION_RE.search(line) for line in window_lines)


def _window_has_bash_emitter_call(window_lines: list[str]) -> bool:
    for line in window_lines:
        code = _strip_bash_comment(line)
        if _BASH_EMITTER_RE.search(code):
            return True
    return False


def _window_has_python_emitter_call(window_lines: list[str]) -> bool:
    for line in window_lines:
        code = line.split("#", 1)[0]
        if _PYTHON_EMITTER_RE.search(code):
            return True
    return False


def _bash_positional_case_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Return 0-indexed ``(start, end)`` inclusive line-index ranges for
    every ``case`` block in *lines* whose dispatch expression is a
    positional CLI parameter (``$1``, ``${1}``, ``${1:-}``, ...) -- see
    "SITE-TYPE SCOPE" in the module docstring. A single forward pass tracks
    block nesting with an aggregate depth stack (any opener among
    ``_BASH_BLOCK_OPENERS`` pushes, any closer among ``_BASH_BLOCK_CLOSERS``
    pops); this finds the correct matching ``esac`` for a positional-param
    ``case`` without a real shell parser, because valid bash nests its
    blocks strictly. Keyword matches outside command position (see
    ``_bash_keyword_in_command_position``) are ignored, so prose inside a
    printed help/usage heredoc cannot desynchronize the depth counter --
    while a *real* embedded script written via heredoc (its keywords are
    still in command position within its own lines) is scanned normally."""
    ranges: list[tuple[int, int]] = []
    stack: list[tuple[int, bool]] = []  # (open_line_index, is_positional_case)
    for i, raw_line in enumerate(lines):
        code = _strip_bash_comment(raw_line)
        for match in _BASH_BLOCK_KEYWORD_RE.finditer(code):
            if not _bash_keyword_in_command_position(code, match.start()):
                continue
            keyword = match.group(1)
            if keyword in _BASH_BLOCK_OPENERS:
                is_target = keyword == "case" and bool(_POSITIONAL_CASE_RE.match(code))
                stack.append((i, is_target))
            elif stack:
                start_i, is_target = stack.pop()
                if is_target:
                    ranges.append((start_i, i))
    return ranges


def _line_in_ranges(index: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= index <= end for start, end in ranges)


def _window_has_usage_guard(
    window_lines: list[str], strip_code: Callable[[str], str], definition_re: re.Pattern
) -> bool:
    """True when *window_lines* names "usage" as a guard for the exit under
    inspection (a ``*_usage`` helper call, a "Usage:" banner, or a "run with
    --help" pointer) -- excluding the helper's own DEFINITION line, which is
    not itself a guard for whatever exit happens to fall within its
    window."""
    for line in window_lines:
        code = strip_code(line)
        if definition_re.search(code):
            continue
        if _USAGE_WORD_RE.search(code):
            return True
    return False


def _is_operator_cli_bash_site(
    index: int, case_ranges: list[tuple[int, int]], window_lines: list[str]
) -> bool:
    """True when the candidate exit at 0-indexed *index* is the census's
    documented operator-CLI argument/usage-refusal SITE TYPE (see
    "SITE-TYPE SCOPE" in the module docstring) -- excluded from candidacy
    entirely, the same as an out-of-scope FILE."""
    if _line_in_ranges(index, case_ranges):
        return True
    return _window_has_usage_guard(window_lines, _strip_bash_comment, _BASH_USAGE_DEFINITION_RE)


def _scan_bash_file(
    path: Path, census_locators: Optional[dict[Path, set[int]]] = None
) -> list[_Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    case_dispatch_ranges = _bash_positional_case_ranges(lines)
    anchored_lines = (census_locators or {}).get(path.resolve(), set())
    findings: list[_Finding] = []
    for i, raw_line in enumerate(lines):
        code = _strip_bash_comment(raw_line)
        if not _BASH_FATAL_EXIT_RE.search(code):
            continue
        if (i + 1) in anchored_lines:
            # CENSUS-LOCATOR ANCHOR SCOPE: already tracked and
            # classified in docs/blocking-site-census.md's Locator Index --
            # see the module docstring section of the same name.
            continue
        window_start = max(0, i - _WINDOW_LINES + 1)
        window_lines = lines[window_start : i + 1]
        if _is_operator_cli_bash_site(i, case_dispatch_ranges, window_lines):
            continue
        missing: list[str] = []
        if not _window_has_classification(window_lines):
            missing.append(_SIGNAL_CENSUS)
        if not _window_has_bash_emitter_call(window_lines):
            missing.append(_SIGNAL_DUAL_CHANNEL)
        if missing:
            findings.append(
                _Finding(path=path, line=i + 1, snippet=raw_line.strip(), missing_signals=missing)
            )
    return findings


def _iter_bash_files(dirs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*.sh")):
            if candidate.is_file() and candidate.name not in _EXCLUDED_BASH_BASENAMES:
                files.append(candidate)
    return files


# ---------------------------------------------------------------------------
# Python scanning
# ---------------------------------------------------------------------------


def _scan_python_file(
    path: Path, census_locators: Optional[dict[Path, set[int]]] = None
) -> list[_Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        # A file this lint cannot read is not this lint's failure to report --
        # unreadable/binary files are outside its scope.
        return []
    lines = text.splitlines()
    anchored_lines = (census_locators or {}).get(path.resolve(), set())
    findings: list[_Finding] = []
    for i, raw_line in enumerate(lines):
        code = raw_line.split("#", 1)[0]
        if not _PYTHON_FATAL_EXIT_RE.search(code):
            continue
        if (i + 1) in anchored_lines:
            # CENSUS-LOCATOR ANCHOR SCOPE: already tracked and
            # classified in docs/blocking-site-census.md's Locator Index --
            # see the module docstring section of the same name.
            continue
        window_start = max(0, i - _WINDOW_LINES + 1)
        window_lines = lines[window_start : i + 1]
        if _window_has_usage_guard(
            window_lines, lambda l: l.split("#", 1)[0], _PYTHON_USAGE_DEFINITION_RE
        ):
            continue
        missing = []
        if not _window_has_classification(window_lines):
            missing.append(_SIGNAL_CENSUS)
        if not _window_has_python_emitter_call(window_lines):
            missing.append(_SIGNAL_DUAL_CHANNEL)
        if missing:
            findings.append(
                _Finding(path=path, line=i + 1, snippet=raw_line.strip(), missing_signals=missing)
            )
    return findings


def _iter_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for candidate in sorted(root.rglob("*.py")):
        if not candidate.is_file():
            continue
        if candidate.name in _EXCLUDED_PYTHON_BASENAMES:
            continue
        if candidate.name.startswith("test_"):
            continue
        relative_parts = set(candidate.relative_to(root).parts)
        if "tests" in relative_parts or "fixtures" in relative_parts:
            continue
        files.append(candidate)
    return files


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


def _signal_description(signal: str) -> str:
    if signal == _SIGNAL_CENSUS:
        return (
            "a census classification comment (one of BLOCK-WORTHY, CONVERT, "
            "AGENT-RESOLVABLE)"
        )
    return "a call to the dual-channel error-contract emitter"


def _example_fix_line(finding: _Finding, rel: str) -> str:
    parts = []
    if _SIGNAL_CENSUS in finding.missing_signals:
        parts.append('add a comment such as "# CONVERT: <one-sentence reason>"')
    if _SIGNAL_DUAL_CHANNEL in finding.missing_signals:
        parts.append(
            "call the shared emitter (pgai_emit_error_contract in bash, "
            "emit_error_contract in Python) with the error/message/remedy triple"
        )
    return f"Edit {rel}:{finding.line} -- {'; and '.join(parts)}"


def _format_remedy_block(findings: list[_Finding], repo_root: Path, lint_script: Path) -> str:
    causes: list[str] = []
    commands: list[str] = []
    for finding in findings:
        rel = _relative(finding.path, repo_root)
        signals_str = " and ".join(_signal_description(s) for s in finding.missing_signals)
        causes.append(f"{rel}:{finding.line} -- refusal site is missing {signals_str}")
        commands.append(_example_fix_line(finding, rel))
    commands.append(f'python3 "{lint_script}"')

    lines = [
        f"lint_gate_census_compliance: {len(findings)} refusal site(s) do not "
        "declare a census classification and/or carry the dual-channel error "
        "contract (non-blocking finding)",
        "",
        "Cause(s):",
    ]
    for cause in causes:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append("To fix (adapt the example text below to the site's actual class and refusal):")
    for i, command in enumerate(commands, start=1):
        lines.append(f"  {i}. {command}")
    lines.append("")
    lines.append(
        "Rule: every gate, guard, or refusal must declare its census "
        "classification in a code comment at the refusal site and emit "
        "through the dual-channel error contract (team/SOP.md, \"Gates and "
        "Refusals\" -> \"New-gate rule\")."
    )
    return "\n".join(lines)


def _finding_to_dict(finding: _Finding, repo_root: Path) -> dict:
    rel = _relative(finding.path, repo_root)
    signals_str = " and ".join(_signal_description(s) for s in finding.missing_signals)
    return {
        "lint": "gate_census_compliance",
        "severity": "quality",
        "file": rel,
        "line": finding.line,
        "snippet": finding.snippet,
        "missing_signals": list(finding.missing_signals),
        "cause": f"{rel}:{finding.line} -- refusal site is missing {signals_str}",
        "remedy": _example_fix_line(finding, rel),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _default_bash_files(script_dir: Path) -> list[Path]:
    """Resolve ``_CENSUS_BASH_FILES`` against *script_dir*, skipping any
    entry that no longer exists (a stale list entry costs a silently
    narrower scan, not a crash -- the same fail-soft posture the Python
    counterpart uses for unreadable files)."""
    files = [script_dir / rel for rel in _CENSUS_BASH_FILES]
    return sorted(f for f in files if f.is_file())


def _default_python_files(script_dir: Path) -> list[Path]:
    """Resolve ``_CENSUS_PYTHON_FILES`` against *script_dir* the same way
    ``_default_bash_files`` does."""
    files = [script_dir / rel for rel in _CENSUS_PYTHON_FILES]
    return sorted(f for f in files if f.is_file())


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_gate_census_compliance.py",
        description=(
            "Scan candidate refusal sites (fatal, literal-nonzero process "
            "exits) in bash and Python and report any site that does not "
            "declare a census classification comment and/or does not carry "
            "the dual-channel error contract. Quality-class lint: findings "
            "print in full and are emitted as machine-readable JSON, but "
            "this script never exits non-zero."
        ),
    )
    parser.add_argument(
        "--bash-dirs",
        nargs="+",
        metavar="DIR",
        help=(
            "Directories to scan (non-recursively) for *.sh candidate "
            "sites, OVERRIDING the census-aligned default file list "
            "(_CENSUS_BASH_FILES) with a directory sweep instead."
        ),
    )
    parser.add_argument(
        "--python-dir",
        metavar="DIR",
        help=(
            "Root directory to scan (recursively) for *.py candidate "
            "sites, OVERRIDING the census-aligned default file list "
            "(_CENSUS_PYTHON_FILES) with a directory sweep instead."
        ),
    )
    parser.add_argument(
        "--findings-file",
        metavar="PATH",
        help=(
            "Write the findings as a JSON array to PATH (parent "
            "directories created as needed). Findings are always printed "
            "to stdout as JSON as well; this flag additionally persists "
            "them to disk."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each file name as it is scanned.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    script_dir = Path(__file__).resolve().parent  # team/scripts/
    repo_root = script_dir.parent.parent  # repo root

    # Default scope (no override flags): the exact census-documented file
    # list (_CENSUS_BASH_FILES / _CENSUS_PYTHON_FILES). Passing --bash-dirs
    # and/or --python-dir switches that axis to the older directory-sweep
    # behavior for an ad-hoc broader/narrower scan. See "SCOPE DECISION
    # (documented)" in the module docstring.
    if args.bash_dirs:
        bash_files = _iter_bash_files([Path(d) for d in args.bash_dirs])
    else:
        bash_files = _default_bash_files(script_dir)

    if args.python_dir:
        python_files = _iter_python_files(Path(args.python_dir))
    else:
        python_files = _default_python_files(script_dir)

    # CENSUS-LOCATOR ANCHOR SCOPE: sites already tracked and
    # classified in docs/blocking-site-census.md's Locator Index are exempt
    # from the inline code-comment/emitter-call check. See the module
    # docstring section of the same name and _load_census_locators.
    census_locators = _load_census_locators(repo_root)

    print(
        f"lint_gate_census_compliance: scanning {len(bash_files)} bash file(s) and "
        f"{len(python_files)} python file(s) for candidate refusal sites "
        f"({sum(len(v) for v in census_locators.values())} census-anchored site(s) exempted)",
        flush=True,
    )

    findings: list[_Finding] = []
    for bash_file in bash_files:
        if args.verbose:
            print(f"  scanning: {bash_file}", flush=True)
        findings.extend(_scan_bash_file(bash_file, census_locators))

    for python_file in python_files:
        if args.verbose:
            print(f"  scanning: {python_file}", flush=True)
        findings.extend(_scan_python_file(python_file, census_locators))

    findings.sort(key=lambda f: (str(f.path), f.line))

    checked = len(bash_files) + len(python_files)
    if findings:
        lint_script = Path(__file__).resolve()
        print("\n" + _format_remedy_block(findings, repo_root, lint_script), flush=True)
    else:
        print(
            f"lint_gate_census_compliance: OK -- {checked} file(s) checked, 0 offenders.",
            flush=True,
        )

    finding_dicts = [_finding_to_dict(f, repo_root) for f in findings]
    findings_json = json.dumps(finding_dicts, indent=2, sort_keys=True)

    print("\nFINDINGS_JSON:", flush=True)
    print(findings_json, flush=True)

    if args.findings_file:
        findings_path = Path(args.findings_file)
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_text(findings_json + "\n", encoding="utf-8")

    # Quality-class lint: findings are noted and filed, never a reason to
    # stop a build or a release. Always exit 0.
    return 0


if __name__ == "__main__":
    sys.exit(main())

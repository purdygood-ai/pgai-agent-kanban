#!/usr/bin/env python3
"""
lint_gate_census_compliance.py
================================
Quality-class lint: enforces the new-gate rule (``team/SOP-core.md``,
"Gates and Refusals" -> ``team/roles/CODER.md``, "Authoring a new
gate"). Any gate, guard, or refusal added to the shipped tree must, at
its refusal site:

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

ANCHOR RECONCILIATION (the marker-to-index direction)
------------------------------------------------------
Beyond the missing-signal check above, this lint separately reconciles the
census in the direction "Writing a compliant anchor" (``team/roles/CODER.md``)
and "Gates and Refusals" (``team/SOP-core.md``) both name but that the
CENSUS-LOCATOR ANCHOR SCOPE exemption below never checked on its own: every
``# CENSUS-ANCHOR: <token>`` marker in a scanned file must be named by a
``path:token CLASS`` line in the census's Locator Index, and that line's
class must agree with what the site's own inline comment declares. Two more
finding kinds cover this direction (see ``_scan_anchor_reconciliation``):

  - ``unindexed_anchor`` -- a marker whose token no Locator Index line
    names at all (marker present, index entry absent: a half-authored
    anchor).
  - ``anchor_class_mismatch`` -- a marker whose Locator Index entry names a
    different class than the site's own inline comment declares.

Both are non-blocking, same as the missing-signal finding: they are printed
and filed, never a reason to exit non-zero.

LOCATOR RESOLUTION (key or token, never a line number, in the census doc)
---------------------------------------------------------------------------
A Locator Index entry's non-numeric key resolves against a scanned file two
ways, tried in order (see ``_resolve_anchor_marker_lines`` and
``_resolve_error_key_lines``):

  1. A ``# CENSUS-ANCHOR: <key>`` marker naming it -- the original
     anchor-token mechanism, unchanged.
  2. A call to the dual-channel emitter (``pgai_emit_error_contract`` /
     ``pgai_format_error_contract`` in bash, ``emit_error_contract`` /
     ``format_error_contract`` in Python) whose literal first argument
     equals the key -- so a site that already speaks its own stable
     ``error`` key needs no separate marker comment; the census simply
     names that key directly ("key each site on its dual-channel error
     key where it has one, falling back to a stable anchor token where it
     does not" -- the ticket that retired this document's line-number
     pins). Both mechanisms exempt a generous forward window from the
     resolving line (the key literal, in the second case, commonly
     precedes its refusal by several lines of message/remedy text).

The numeric line-number form (``path:LINE CLASS``) remains parseable for
backward compatibility and for the synthetic fixtures below that predate
the key/token migration, but no entry in the shipped
``docs/blocking-site-census.md`` uses it any longer -- a line insertion
above any of its entries cannot move a pin that is no longer expressed as
a position.

LOCATOR INDEX -> SITE RECONCILIATION (the index-to-marker direction)
-----------------------------------------------------------------------
A third finding kind, ``unresolved_locator``, closes the direction the two
kinds above do not check: every entry the Locator Index declares -- line,
token, or key -- must actually resolve to something in the file it names.
An entry whose line is not a candidate exit, or whose key matches neither
a marker nor a literal dual-channel argument anywhere in the file, is a
stale or mistyped pin -- reported once per unresolved entry, naming the
key/line and the file, non-blocking like every other finding here (see
``_scan_locator_resolution``).

ROLLUP RECONCILIATION (the table-to-summary-line direction)
-----------------------------------------------------------------------
A fifth finding kind, ``rollup_mismatch``, closes a direction none of the
four kinds above check: the census document's own "At-a-glance rollup"
table carries a hand-authored summary line directly beneath it --
``**Rollup: N sites, M unclassified.**`` followed by
``X BLOCK-WORTHY, Y CONVERT, Z AGENT-RESOLVABLE.`` -- and none of the
four kinds above verifies that summary line still agrees with the table
it summarizes. Every prior finding kind reconciles the table/Locator Index
against the *code tree*; this one reconciles the census *document against
itself*, so a gate author who adds a numbered row (per the new-gate rule)
but forgets the rollup line -- a drift where
successive authors updated the table and the Locator Index, the two
halves the new-gate rule names, but never the rollup -- now produces a
finding instead of a silently wrong self-report (see
``_scan_census_rollup``).

DECISION COLUMN (every table row names what the decision wake does)
-----------------------------------------------------------------------
A sixth finding kind, ``decision_invalid``, also reads the census document
alone: every numbered row of the "At-a-glance rollup" table carries a
trailing Decision cell after its Class cell, naming what OVERWATCH's
decision wake does when the site fires. The value comes from a closed set:
``verb(<name>)``, ``inbox``, ``runaway`` or ``none``. A row is reported when
it has no Decision cell (Class is still its last cell), the cell is empty,
the value is outside the set, or ``<name>`` is not one of
``pgai_agent_kanban.overwatch.decide.VERBS``. Verb names are checked only
when that module imports from this script's own tree; otherwise any
lower-case hyphenated name is accepted (see ``_scan_census_decisions``).
Matching is liberal: surrounding backticks, case and spaces inside the
parentheses are ignored.

CONVERTED COLUMN (every CONVERT row's call sites are checked)
-----------------------------------------------------------------------
Two more finding kinds read the table's trailing Converted cell, the cell
after Decision (see ``_scan_census_converted``):

  - ``converted_invalid`` -- a numbered row has no Converted cell, an empty
    one, a value outside ``yes`` / ``no`` / ``n/a``, or one its class does
    not take (a CONVERT row reads ``yes`` or ``no``, every other row
    ``n/a``). Matching ignores backticks and case.
  - ``convert_site_fatal`` -- a CONVERT row's call site still exits
    non-zero, whatever its Converted cell says: one finding per row and
    file, at that file's first fatal line.

A row's call sites are what its Source cell names (``_census_row_call_sites``):
the backticked tokens that are Locator Index keys, each read through its
key's window, or -- when it names no key -- every backticked ``*.sh`` /
``*.py`` path, read whole minus the exits another class claims and the
operator-CLI argument refusals (``_site_fatal_lines_whole_file``). A site
exits non-zero through a literal bash ``exit N``, or a Python
``sys.exit(N)`` / ``raise SystemExit(N)`` / literal non-zero return from
``main`` or a function ``main`` returns through (``_python_fatal_exits``).
A status computed at run time (``return len(findings)``) is not seen, nor is
an abort by ``set -e``; the census vocabulary section states the same rule.

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
import ast
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

# The eight finding kinds this lint produces. "missing_signal" is the
# original (and still most common) kind -- a candidate refusal site missing
# its classification comment and/or emitter call. The next two are the
# marker-to-index reconciliation direction (team/roles/CODER.md, "Writing a
# compliant anchor" -- "Authored together"): a `# CENSUS-ANCHOR: <token>`
# marker in a scanned file whose token no Locator Index line names
# ("unindexed_anchor"), and one whose Locator Index entry disagrees with
# the class the site's own inline comment declares ("anchor_class_mismatch").
# The fourth, "unresolved_locator", is the reverse direction: a Locator
# Index entry (line, token, or dual-channel key) that does not resolve to
# anything in the file it names -- see "LOCATOR INDEX -> SITE
# RECONCILIATION" in the module docstring and `_scan_locator_resolution`.
# The fifth, "rollup_mismatch", is the document-against-itself direction:
# the census's own "**Rollup: N sites...**" summary line
# disagrees with the inventory table it summarizes -- see "ROLLUP
# RECONCILIATION" in the module docstring and `_scan_census_rollup`.
# The sixth, "decision_invalid", is a numbered table row whose Decision cell
# is missing or outside the closed set -- see "DECISION COLUMN" in the
# module docstring and `_scan_census_decisions`.
# The seventh, "converted_invalid", is a numbered table row whose Converted
# cell is missing, outside the closed set, or disagrees with the row's
# class; the eighth, "convert_site_fatal", is a CONVERT row whose call site
# still exits non-zero -- see "CONVERTED COLUMN" in the module docstring
# and `_scan_census_converted`.
_FINDING_KIND_MISSING_SIGNAL = "missing_signal"
_FINDING_KIND_UNINDEXED_ANCHOR = "unindexed_anchor"
_FINDING_KIND_ANCHOR_CLASS_MISMATCH = "anchor_class_mismatch"
_FINDING_KIND_UNRESOLVED_LOCATOR = "unresolved_locator"
_FINDING_KIND_ROLLUP_MISMATCH = "rollup_mismatch"
_FINDING_KIND_DECISION_INVALID = "decision_invalid"
_FINDING_KIND_CONVERTED_INVALID = "converted_invalid"
_FINDING_KIND_CONVERT_SITE_FATAL = "convert_site_fatal"

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
# Same shape as _CLASSIFICATION_RE, but capturing -- used where the specific
# token matters (the anchor reconciliation direction needs to know WHICH
# class a site declares, not just whether it declares one).
_CLASSIFICATION_TOKEN_RE = re.compile(
    r"#.*\b(" + "|".join(re.escape(t) for t in CLASSIFICATION_TOKENS) + r")\b"
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
    "lib/wake_driver_invoke.sh",  # row 56
    "lib/dev_tree.sh",  # row 27
    "lib/dashboard_palette.sh",  # rows 57-58
    "lib/gated_runner_lint.sh",  # row 60
    "lib/config_loader.sh",  # config-loader-{deferred-bugs,deferred-bug-level,defer-piggyback,deep-verify}-01 (Locator Index inventory; no rollup row)
    "lib/defer_posture.sh",  # defer-posture-{invalid-boolean,invalid-level,campaign-unparseable,campaign-malformed-until}-01 (Locator Index inventory; no rollup row)
    "cm/release.sh",  # rows 8-24
    "cm/finalize-release.sh",  # row 16
    "harness/doc-lint.sh",  # rows 47-50
)

# Bash files the default scan takes by glob, relative to team/scripts/: the
# verification scripts under team/verification/ carry census anchors of
# their own, so every one of them is on the default scan surface.
_CENSUS_BASH_GLOBS = (
    "../verification/*.sh",
)

_CENSUS_PYTHON_FILES = (
    "check_breakage_class.py",  # row 25
    "lint_changelog_freshness.py",  # row 30
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
    "lint_engine_size_dupe.py",  # row 53
    "lint_dockerfile_static.py",  # row 54
)

# ---------------------------------------------------------------------------
# Census locator anchor -- see "CENSUS-LOCATOR ANCHOR SCOPE" above
# ---------------------------------------------------------------------------
# The census document, relative to the repo root, and the fenced-block
# markers that delimit its machine-readable "Locator Index" section.
_CENSUS_DOC_RELATIVE_PATH = "docs/blocking-site-census.md"
_CENSUS_LOCATOR_BEGIN_MARKER = "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->"
_CENSUS_LOCATOR_END_MARKER = "<!-- CENSUS-LOCATOR-INDEX-END -->"

# One locator entry: "<path-relative-to-repo-root>:<key> <CLASS>", where
# <key> is either a 1-indexed line number (the majority form) or a token
# naming a `# CENSUS-ANCHOR: <token>` marker in that file (the form
# team/SOP-core.md "Gates and Refusals" prefers -- it resolves by name, not
# position, so moving the site within its own file needs no census edit).
# Token characters match the SOP's rule: letters, digits, '.', '_', '-',
# starting with a letter or digit -- disjoint from "all digits", so which
# form an entry uses is unambiguous from its <key> alone.
_CENSUS_LOCATOR_LINE_RE = re.compile(
    r"^(?P<path>\S+):(?P<line>\d+)\s+(?P<cls>"
    + "|".join(re.escape(t) for t in CLASSIFICATION_TOKENS)
    + r")\s*$"
)
_CENSUS_LOCATOR_TOKEN_RE = re.compile(
    r"^(?P<path>\S+):(?P<token>[A-Za-z0-9][A-Za-z0-9._-]*)\s+(?P<cls>"
    + "|".join(re.escape(t) for t in CLASSIFICATION_TOKENS)
    + r")\s*$"
)

# A `# CENSUS-ANCHOR: <token>` marker: a comment and nothing else, on its
# own line (team/SOP-core.md "Gates and Refusals", "Writing a compliant
# anchor"). The refusal the marker names must sit within the marker's own
# line plus the five lines below it -- see _CENSUS_ANCHOR_FORWARD_WINDOW.
_CENSUS_ANCHOR_MARKER_RE = re.compile(
    r"^\s*#\s*CENSUS-ANCHOR:\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*$"
)
_CENSUS_ANCHOR_FORWARD_WINDOW = 5


@dataclass
class _CensusLocators:
    """The two Locator Index forms, parsed once per run and consulted by
    every scanned file: resolved absolute path -> the set of already-
    tracked lines (numeric-key entries) or tokens (name-key entries).

    ``token_class`` carries the same token-form entries a second way --
    resolved path -> {token: declared CLASS} -- so the marker-to-index
    reconciliation direction (see ``_scan_anchor_reconciliation`` below) can
    read each entry's declared class without changing ``by_token``'s
    existing set-of-tokens shape, which the exemption path
    (``_resolve_anchor_marker_lines``) and its tests already depend on."""

    by_line: dict[Path, set[int]] = field(default_factory=dict)
    by_token: dict[Path, set[str]] = field(default_factory=dict)
    token_class: dict[Path, dict[str, str]] = field(default_factory=dict)
    # Parallel to token_class, but for the numeric-key form: resolved path
    # -> {line: declared CLASS}. Only consulted by the locator->site
    # resolution finding (`_scan_locator_resolution`) to name the class an
    # unresolved line-form entry declared; the exemption path does not
    # need it.
    line_class: dict[Path, dict[int, str]] = field(default_factory=dict)

    def total_entries(self) -> int:
        return sum(len(v) for v in self.by_line.values()) + sum(
            len(v) for v in self.by_token.values()
        )


def _load_census_locators(repo_root: Path) -> _CensusLocators:
    """Parse the census's "Locator Index" fenced block into the two forms
    an entry may take (see "CENSUS-LOCATOR ANCHOR SCOPE" above and the
    census doc section of the same name). Fails soft to an empty result --
    a missing census file, a missing/malformed fenced block, or an
    unparseable entry costs this run a narrower anchor set (candidate sites
    that would have been exempted are scanned normally instead), never a
    crash; this lint's own posture is "never exits non-zero" and a
    doc-parsing hiccup must not change that."""
    census_path = repo_root / _CENSUS_DOC_RELATIVE_PATH
    try:
        text = census_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _CensusLocators()

    if _CENSUS_LOCATOR_BEGIN_MARKER not in text or _CENSUS_LOCATOR_END_MARKER not in text:
        return _CensusLocators()

    block = text.split(_CENSUS_LOCATOR_BEGIN_MARKER, 1)[1]
    block = block.split(_CENSUS_LOCATOR_END_MARKER, 1)[0]

    locators = _CensusLocators()
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_match = _CENSUS_LOCATOR_LINE_RE.match(line)
        if line_match:
            entry_path = (repo_root / line_match.group("path")).resolve()
            entry_line = int(line_match.group("line"))
            locators.by_line.setdefault(entry_path, set()).add(entry_line)
            locators.line_class.setdefault(entry_path, {})[entry_line] = line_match.group("cls")
            continue
        token_match = _CENSUS_LOCATOR_TOKEN_RE.match(line)
        if token_match:
            entry_path = (repo_root / token_match.group("path")).resolve()
            token = token_match.group("token")
            locators.by_token.setdefault(entry_path, set()).add(token)
            locators.token_class.setdefault(entry_path, {})[token] = token_match.group("cls")
            continue
        # An unparseable entry costs a missed anchor, not a crash -- see
        # the fail-soft rationale in this function's docstring.
    return locators


def _resolve_anchor_marker_lines(lines: list[str], tokens: set[str]) -> set[int]:
    """Return the 1-indexed lines exempted by a `# CENSUS-ANCHOR: <token>`
    marker whose token is a member of *tokens* (the file's token-keyed
    Locator Index entries). Each qualifying marker exempts its own line
    plus the next `_CENSUS_ANCHOR_FORWARD_WINDOW` lines -- the marker's
    documented forward window -- so the site resolves by the marker's name
    rather than by either the marker's or the refusal's line number."""
    if not tokens:
        return set()
    exempted: set[int] = set()
    for idx, raw_line in enumerate(lines):
        match = _CENSUS_ANCHOR_MARKER_RE.match(raw_line)
        if not match or match.group(1) not in tokens:
            continue
        marker_line = idx + 1
        exempted.update(
            range(marker_line, marker_line + _CENSUS_ANCHOR_FORWARD_WINDOW + 1)
        )
    return exempted


# A quoted literal argument -- either quote style, the same key charset
# the SOP's anchor-token rule uses (letters, digits, '.', '_', '-'). Used
# to read a dual-channel emitter call's own error-key argument back out of
# the source line(s), so a Locator Index entry can name that key directly
# instead of requiring a redundant CENSUS-ANCHOR comment at a site that
# already speaks a stable key of its own.
_LITERAL_STRING_ARG_RE = re.compile(r"""["']([A-Za-z0-9][A-Za-z0-9._-]*)["']""")

# How many lines forward of a resolving emitter call are exempted -- wider
# than the anchor marker's own forward window (_CENSUS_ANCHOR_FORWARD_WINDOW)
# because a call's literal key argument commonly sits several lines above
# the exit/raise it belongs to (the shipped tree's shape: state the key,
# then the message, then the remedy, each its own line, before the
# fatal exit that acts on all three).
_KEY_FORWARD_WINDOW = 20


def _resolve_error_key_lines(
    lines: list[str], keys: set[str], emitter_re: re.Pattern, is_python: bool
) -> set[int]:
    """Return the 1-indexed lines exempted by a dual-channel emitter call
    (``pgai_emit_error_contract`` / ``pgai_format_error_contract`` in bash;
    ``emit_error_contract`` / ``format_error_contract`` in Python) whose
    literal first-argument string is a member of *keys* -- the "key each
    site on its dual-channel error key" resolution path (see "LOCATOR
    RESOLUTION" in the module docstring). The literal key may sit on the
    call's own line or either of the next two (bash's line-continuation
    style, and a Python call split across lines), so each candidate call
    line is probed together with its two immediate followers. A matching
    call exempts its own line through the next `_KEY_FORWARD_WINDOW` lines."""
    if not keys:
        return set()
    strip = (lambda l: l.split("#", 1)[0]) if is_python else _strip_bash_comment
    exempted: set[int] = set()
    for idx, raw_line in enumerate(lines):
        if not emitter_re.search(strip(raw_line)):
            continue
        matched_key = False
        for probe in range(idx, min(idx + 3, len(lines))):
            literal_match = _LITERAL_STRING_ARG_RE.search(lines[probe])
            if literal_match and literal_match.group(1) in keys:
                matched_key = True
                break
        if not matched_key:
            continue
        start = idx + 1
        end = min(len(lines), idx + 1 + _KEY_FORWARD_WINDOW)
        exempted.update(range(start, end + 1))
    return exempted


def _window_classification_token(window_lines: list[str]) -> Optional[str]:
    """Return the first census classification token found in a comment
    across *window_lines*, or ``None`` if none of the three tokens appear.
    Unlike ``_window_has_classification`` (a bool), this names WHICH token
    was declared -- the anchor reconciliation direction needs to compare
    that value against the Locator Index's own declared class."""
    for line in window_lines:
        match = _CLASSIFICATION_TOKEN_RE.search(line)
        if match:
            return match.group(1)
    return None


def _scan_anchor_reconciliation(
    path: Path, lines: list[str], census_locators: _CensusLocators
) -> list[_Finding]:
    """The marker-to-index reconciliation direction (team/roles/CODER.md,
    "Writing a compliant anchor" -- "Authored together"; team/SOP-core.md,
    "Gates and Refusals"): every `# CENSUS-ANCHOR: <token>` marker in a
    scanned file must be named by a Locator Index `path:token CLASS` line,
    and that line's class must agree with what the site's own inline
    comment declares. This is the direction the exemption path
    (`_resolve_anchor_marker_lines`) does not check -- that path only ever
    USES the index to exempt a site; it never reports a marker the index
    does not name.

    A marker is reportable purely by its own presence -- it does not need
    a co-located literal ``exit N`` / ``sys.exit(N)`` this lint's
    fatal-exit regex happens to recognize. Several real anchors document a
    ``return N`` a caller later turns into a process exit (the exact shape
    ``engine-size-dupe-01`` uses), which the regex was never meant to
    catch; requiring a regex match here would silently exempt exactly the
    two-of-nineteen half-authored anchors this reconciliation direction
    exists to catch. The site's own declared class, when one is findable,
    is read from a window spanning back to the marker's own backward
    window and forward through its documented forward window -- covering
    both orderings seen in the shipped tree (the classification comment
    sits above the marker in some sites, below it in others)."""
    resolved_path = path.resolve()
    indexed_tokens = census_locators.by_token.get(resolved_path, set())
    indexed_classes = census_locators.token_class.get(resolved_path, {})
    findings: list[_Finding] = []
    for idx, raw_line in enumerate(lines):
        marker_match = _CENSUS_ANCHOR_MARKER_RE.match(raw_line)
        if not marker_match:
            continue
        token = marker_match.group(1)
        marker_line = idx + 1

        window_start = max(0, idx - _WINDOW_LINES + 1)
        window_end = min(len(lines), idx + _CENSUS_ANCHOR_FORWARD_WINDOW + 1)
        site_class = _window_classification_token(lines[window_start:window_end])

        if token not in indexed_tokens:
            findings.append(
                _Finding(
                    path=path,
                    line=marker_line,
                    snippet=raw_line.strip(),
                    kind=_FINDING_KIND_UNINDEXED_ANCHOR,
                    token=token,
                    site_class=site_class,
                )
            )
            continue

        index_class = indexed_classes.get(token)
        if site_class and index_class and site_class != index_class:
            findings.append(
                _Finding(
                    path=path,
                    line=marker_line,
                    snippet=raw_line.strip(),
                    kind=_FINDING_KIND_ANCHOR_CLASS_MISMATCH,
                    token=token,
                    index_class=index_class,
                    site_class=site_class,
                )
            )
    return findings


@dataclass
class _Finding:
    """One reported site. ``kind`` selects which of the finding
    vocabularies applies (see the ``_FINDING_KIND_*`` constants above):

      - ``missing_signal`` (the default/original kind): ``missing_signals``
        names which of census-classification / dual-channel-emitter is
        absent from the site's window.
      - ``unindexed_anchor``: ``token`` names the marker whose Locator
        Index entry is absent; ``site_class`` is the class its own inline
        comment declares, when one is found (may be ``None``).
      - ``anchor_class_mismatch``: ``token`` names the marker,
        ``index_class`` is what the Locator Index says, ``site_class`` is
        what the site's own inline comment says -- the two disagree.
      - ``rollup_mismatch``: ``table_summary`` names the counts the
        inventory table actually contains; ``rollup_summary`` names what
        the document's own "**Rollup: ...**" line claims -- the two
        disagree. There is at most one of these per run (the whole
        document has exactly one rollup line to reconcile).
      - ``decision_invalid``: ``row_number`` is the table row's ``#``,
        ``decision`` the Decision cell as written (``None`` when the row has
        no Decision cell), and ``decision_problem`` says what is wrong with
        it, as a phrase that completes "the row ...".
      - ``converted_invalid``: ``row_number`` is the table row's ``#``,
        ``converted`` the Converted cell as written (``None`` when the row
        has no Converted cell), and ``converted_problem`` says what is
        wrong with it, completing "the row ...".
      - ``convert_site_fatal``: ``path``/``line`` name the CONVERT row's
        call site and its first non-zero exit, ``row_number`` the row,
        ``token`` the Locator Index key the site was read through (``None``
        when the Source cell's whole file was read), and ``converted`` the
        row's Converted cell."""

    path: Path
    line: int
    snippet: str
    missing_signals: list[str] = field(default_factory=list)
    kind: str = _FINDING_KIND_MISSING_SIGNAL
    token: Optional[str] = None
    index_class: Optional[str] = None
    site_class: Optional[str] = None
    table_summary: Optional[str] = None
    rollup_summary: Optional[str] = None
    row_number: Optional[int] = None
    decision: Optional[str] = None
    decision_problem: Optional[str] = None
    converted: Optional[str] = None
    converted_problem: Optional[str] = None


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
    path: Path, census_locators: Optional[_CensusLocators] = None
) -> list[_Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    case_dispatch_ranges = _bash_positional_case_ranges(lines)
    resolved_path = path.resolve()
    _locator_keys = (census_locators.by_token if census_locators else {}).get(resolved_path, set())
    anchored_lines = set((census_locators.by_line if census_locators else {}).get(resolved_path, set()))
    anchored_lines |= _resolve_anchor_marker_lines(lines, _locator_keys)
    anchored_lines |= _resolve_error_key_lines(lines, _locator_keys, _BASH_EMITTER_RE, is_python=False)
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
    findings.extend(_scan_anchor_reconciliation(path, lines, census_locators or _CensusLocators()))
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
    path: Path, census_locators: Optional[_CensusLocators] = None
) -> list[_Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        # A file this lint cannot read is not this lint's failure to report --
        # unreadable/binary files are outside its scope.
        return []
    lines = text.splitlines()
    resolved_path = path.resolve()
    _locator_keys = (census_locators.by_token if census_locators else {}).get(resolved_path, set())
    anchored_lines = set((census_locators.by_line if census_locators else {}).get(resolved_path, set()))
    anchored_lines |= _resolve_anchor_marker_lines(lines, _locator_keys)
    anchored_lines |= _resolve_error_key_lines(lines, _locator_keys, _PYTHON_EMITTER_RE, is_python=True)
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
    findings.extend(_scan_anchor_reconciliation(path, lines, census_locators or _CensusLocators()))
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
# Locator Index -> site reconciliation (see "LOCATOR INDEX -> SITE
# RECONCILIATION" in the module docstring)
# ---------------------------------------------------------------------------


def _locator_entry_resolves(
    lines: Optional[list[str]],
    is_python: bool,
    *,
    line: Optional[int] = None,
    key: Optional[str] = None,
) -> bool:
    """True when a single Locator Index entry -- either a numeric *line* or
    a non-numeric *key* -- resolves against *lines* (``None`` when the
    named file could not be read at all, which never resolves anything).
    Exactly one of *line*/*key* is supplied by the caller."""
    if lines is None:
        return False
    if line is not None:
        exit_re = _PYTHON_FATAL_EXIT_RE if is_python else _BASH_FATAL_EXIT_RE
        strip = (lambda l: l.split("#", 1)[0]) if is_python else _strip_bash_comment
        if not (1 <= line <= len(lines)):
            return False
        return bool(exit_re.search(strip(lines[line - 1])))
    emitter_re = _PYTHON_EMITTER_RE if is_python else _BASH_EMITTER_RE
    keys = {key} if key else set()
    if _resolve_anchor_marker_lines(lines, keys):
        return True
    return bool(_resolve_error_key_lines(lines, keys, emitter_re, is_python))


def _scan_locator_resolution(census_locators: _CensusLocators, repo_root: Path) -> list[_Finding]:
    """The reconciliation direction "LOCATOR INDEX -> SITE" in the module
    docstring: every entry ``docs/blocking-site-census.md``'s Locator Index
    declares must resolve to something real in the file it names. Unlike
    the missing-signal scan (which walks a maintained include-list of
    in-scope files), this walks the census's OWN entries and checks each
    one directly -- so a stale pin in a file the default scan does not even
    cover (the census carries several such "inventory" entries; see the
    census doc's "Not every entry is lint-consumed") is still caught.
    A file this function cannot read at all (missing, moved, permission
    error) resolves nothing for any of its entries -- same fail-soft
    posture as the rest of this lint, reported as one unresolved-locator
    finding per entry rather than a crash."""
    findings: list[_Finding] = []
    all_paths = sorted(set(census_locators.by_line) | set(census_locators.by_token), key=str)
    for resolved_path in all_paths:
        is_python = resolved_path.suffix == ".py"
        try:
            lines: Optional[list[str]] = resolved_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            lines = None
        rel = _relative(resolved_path, repo_root)

        for line_no in sorted(census_locators.by_line.get(resolved_path, set())):
            if _locator_entry_resolves(lines, is_python, line=line_no):
                continue
            findings.append(
                _Finding(
                    path=resolved_path,
                    line=0,
                    snippet=f"{rel}:{line_no}",
                    kind=_FINDING_KIND_UNRESOLVED_LOCATOR,
                    token=str(line_no),
                    index_class=census_locators.line_class.get(resolved_path, {}).get(line_no),
                )
            )

        for key in sorted(census_locators.by_token.get(resolved_path, set())):
            if _locator_entry_resolves(lines, is_python, key=key):
                continue
            findings.append(
                _Finding(
                    path=resolved_path,
                    line=0,
                    snippet=f"{rel}:{key}",
                    kind=_FINDING_KIND_UNRESOLVED_LOCATOR,
                    token=key,
                    index_class=census_locators.token_class.get(resolved_path, {}).get(key),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Rollup reconciliation (the census document against itself; see "ROLLUP
# RECONCILIATION" in the module docstring)
# ---------------------------------------------------------------------------

# A numbered inventory-table row: any line of the form "| N | ... |" --
# matches the bug report's own repro command (`grep -cE '^\| [0-9]+ \|'`),
# so the row COUNT this lint derives agrees with the count a human running
# that command by hand would get.
_CENSUS_TABLE_ANY_ROW_RE = re.compile(r"^\|\s*\d+\s*\|")

# The same row, additionally capturing its Class cell: the cell before the
# trailing Decision and Converted cells, or the trailing cell itself on a
# row that has neither (that row is still counted by class;
# `_scan_census_decisions` and `_scan_census_converted` report the missing
# cells). Greedy `.*` is intentional and safe here: it is anchored at both
# ends (the leading `| N |` and the trailing `| CLASS |` plus at most two
# more cells), so it always lands on the last Class-shaped field regardless
# of how many literal `|` characters (escaped table pipes, code spans)
# appear in the row's middle columns.
_CENSUS_TABLE_ROW_RE = re.compile(
    r"^\|\s*\d+\s*\|.*\|\s*(?P<cls>"
    + "|".join(re.escape(t) for t in CLASSIFICATION_TOKENS)
    + r")\s*\|(?:[^|]*\|){0,2}\s*$"
)

# A numbered row split at its Class cell: the class, and every cell after
# it (the Decision cell, then the Converted cell). Neither trailing
# vocabulary contains a `|`, so the cells after Class are pipe-free, and the
# greedy `.*` lands on the last Class-shaped cell for the same reason as in
# `_CENSUS_TABLE_ROW_RE`.
_CENSUS_TABLE_TAIL_RE = re.compile(
    r"^\|\s*\d+\s*\|.*\|\s*(?P<cls>"
    + "|".join(re.escape(t) for t in CLASSIFICATION_TOKENS)
    + r")\s*\|(?P<tail>(?:[^|]*\|)*)\s*$"
)


def _census_row_tail(raw_line: str) -> Optional[tuple[str, list[str]]]:
    """``(class, cells after the Class cell)`` for one numbered table row,
    each cell unstripped, or ``None`` when no Class cell parses (the rollup
    check reports that row as unclassified)."""
    match = _CENSUS_TABLE_TAIL_RE.match(raw_line.rstrip())
    if not match:
        return None
    return match.group("cls"), match.group("tail").split("|")[:-1]

# A numbered row's number and its trailing cell -- the Decision cell, or the
# Class cell on a row that has none. The Decision vocabulary never contains
# a `|`, so the trailing cell is everything after the last inner pipe.
_CENSUS_TABLE_ROW_NUMBER_RE = re.compile(r"^\|\s*(?P<num>\d+)\s*\|")
_CENSUS_TABLE_LAST_CELL_RE = re.compile(r"\|(?P<cell>[^|]*)\|\s*$")

# The Decision column's closed set (see "DECISION COLUMN" in the module
# docstring): the decision wake's four outcomes
# (pgai_agent_kanban.overwatch.decide.OUTCOMES), a verb written with its
# name as verb(<name>).
_CENSUS_DECISION_WORDS = ("inbox", "runaway", "none")
_CENSUS_DECISION_VERB_RE = re.compile(r"^verb\s*\(\s*(?P<name>[^()]*?)\s*\)$", re.IGNORECASE)
_CENSUS_DECISION_VERB_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_CENSUS_DECISION_VOCABULARY = "verb(<name>), inbox, runaway or none"

# A finding's snippet for a table row is cut to this many characters -- a
# census row can run to a long paragraph.
_CENSUS_ROW_SNIPPET_CHARS = 120

# The two-line hand-authored summary beneath the table:
#   **Rollup: 57 sites, 0 unclassified.**
#   29 BLOCK-WORTHY, 26 CONVERT, 2 AGENT-RESOLVABLE.
_CENSUS_ROLLUP_SITES_RE = re.compile(
    r"^\*\*Rollup:\s*(?P<sites>\d+)\s*sites?,\s*(?P<unclassified>\d+)\s*unclassified\.\*\*$"
)
_CENSUS_ROLLUP_CLASSES_RE = re.compile(
    r"^(?P<block>\d+)\s*BLOCK-WORTHY,\s*(?P<convert>\d+)\s*CONVERT,\s*"
    r"(?P<agent>\d+)\s*AGENT-RESOLVABLE\.$"
)

# How many lines below the "**Rollup: ...**" line to look for its
# companion per-class breakdown line -- generous enough to survive a blank
# line or two between them without picking up an unrelated line further
# down the document.
_CENSUS_ROLLUP_CLASSES_FORWARD_WINDOW = 3


def _census_table_counts(lines: list[str]) -> tuple[int, dict[str, int]]:
    """Return ``(total_rows, {class: count})`` derived directly from the
    "At-a-glance rollup" table's own numbered rows -- the ground truth the
    document's summary line is reconciled against. ``total_rows`` counts
    every ``| N | ...`` row regardless of whether its trailing column
    parses as one of the three known classes (an unparseable trailing
    column would make ``total_rows`` exceed the sum of ``class counts``,
    which the caller reports as a nonzero "unclassified" count -- the same
    quantity the rollup line's own "M unclassified" half claims)."""
    total_rows = 0
    class_counts = {token: 0 for token in CLASSIFICATION_TOKENS}
    for raw_line in lines:
        if not _CENSUS_TABLE_ANY_ROW_RE.match(raw_line):
            continue
        total_rows += 1
        row_match = _CENSUS_TABLE_ROW_RE.match(raw_line)
        if row_match:
            class_counts[row_match.group("cls")] += 1
    return total_rows, class_counts


def _scan_census_rollup(repo_root: Path) -> list[_Finding]:
    """The document-against-itself reconciliation direction (see
    "ROLLUP RECONCILIATION" in the module docstring): the census's
    "**Rollup: N sites, M unclassified.**" summary line, and its companion
    per-class breakdown line, must agree with what the inventory table
    above them actually contains. Unlike every other scan in this lint,
    there is no code tree involved -- both sides of the comparison live in
    ``docs/blocking-site-census.md`` itself, so this reads that one file
    and reconciles it against its own table.

    Fails soft, matching this lint's "never crashes, never exits non-zero"
    posture (`_load_census_locators`'s docstring states the same rule for
    the sibling document-parsing helper): a missing census file, a table
    with no numbered rows, or a document with no parseable rollup line
    costs this run a silently skipped check, never a crash -- there is
    nothing to reconcile without both a table and a rollup line, so
    reporting a finding for the ABSENCE of either would be a false
    positive, not a caught defect."""
    census_path = repo_root / _CENSUS_DOC_RELATIVE_PATH
    try:
        text = census_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()

    total_rows, class_counts = _census_table_counts(lines)
    if total_rows == 0:
        return []

    rollup_line_no: Optional[int] = None
    rollup_sites: Optional[int] = None
    rollup_unclassified: Optional[int] = None
    rollup_block: Optional[int] = None
    rollup_convert: Optional[int] = None
    rollup_agent: Optional[int] = None
    for idx, raw_line in enumerate(lines):
        sites_match = _CENSUS_ROLLUP_SITES_RE.match(raw_line.strip())
        if not sites_match:
            continue
        rollup_line_no = idx + 1
        rollup_sites = int(sites_match.group("sites"))
        rollup_unclassified = int(sites_match.group("unclassified"))
        window_end = min(len(lines), idx + 1 + _CENSUS_ROLLUP_CLASSES_FORWARD_WINDOW)
        for follow_line in lines[idx + 1 : window_end]:
            classes_match = _CENSUS_ROLLUP_CLASSES_RE.match(follow_line.strip())
            if classes_match:
                rollup_block = int(classes_match.group("block"))
                rollup_convert = int(classes_match.group("convert"))
                rollup_agent = int(classes_match.group("agent"))
                break
        break

    if rollup_line_no is None or rollup_sites is None:
        return []

    unclassified_rows = total_rows - sum(class_counts.values())
    table_summary = (
        f"{total_rows} sites, {unclassified_rows} unclassified -- "
        f"{class_counts['BLOCK-WORTHY']} BLOCK-WORTHY, "
        f"{class_counts['CONVERT']} CONVERT, "
        f"{class_counts['AGENT-RESOLVABLE']} AGENT-RESOLVABLE"
    )
    if rollup_block is None:
        rollup_summary = (
            f"{rollup_sites} sites, {rollup_unclassified} unclassified -- "
            "no parseable per-class breakdown line found"
        )
        mismatch = True
    else:
        rollup_summary = (
            f"{rollup_sites} sites, {rollup_unclassified} unclassified -- "
            f"{rollup_block} BLOCK-WORTHY, {rollup_convert} CONVERT, "
            f"{rollup_agent} AGENT-RESOLVABLE"
        )
        mismatch = (
            rollup_sites != total_rows
            or rollup_unclassified != unclassified_rows
            or rollup_block != class_counts["BLOCK-WORTHY"]
            or rollup_convert != class_counts["CONVERT"]
            or rollup_agent != class_counts["AGENT-RESOLVABLE"]
        )

    if not mismatch:
        return []

    return [
        _Finding(
            path=census_path,
            line=rollup_line_no,
            snippet=lines[rollup_line_no - 1].strip(),
            kind=_FINDING_KIND_ROLLUP_MISMATCH,
            table_summary=table_summary,
            rollup_summary=rollup_summary,
        )
    ]


def _decision_wake_verbs() -> Optional[frozenset[str]]:
    """The decision wake's verb names (``decide.VERBS``), imported from the
    package that sits beside this script's own directory -- ``team/`` in
    the dev tree, the kanban root in an install. Returns ``None`` when that
    tree has no such module or it cannot be imported (a copied script, a
    missing dependency); the caller then checks verb names by shape only."""
    package_parent = Path(__file__).resolve().parent.parent
    if not (package_parent / "pgai_agent_kanban" / "overwatch" / "decide.py").is_file():
        return None
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))
    try:
        from pgai_agent_kanban.overwatch.decide import VERBS
    except Exception:  # noqa: BLE001 -- any import failure only narrows the check
        return None
    return frozenset(VERBS)


def _decision_problem(cell: str, known_verbs: Optional[frozenset[str]]) -> Optional[str]:
    """What is wrong with one Decision cell, as a phrase completing "the
    row ...", or ``None`` when it holds a value from the closed set."""
    value = cell.strip().strip("`").strip()
    if not value:
        return "has an empty Decision cell"
    if value.lower() in _CENSUS_DECISION_WORDS:
        return None
    verb_match = _CENSUS_DECISION_VERB_RE.match(value)
    if not verb_match:
        return f"has Decision '{value}', which is not one of {_CENSUS_DECISION_VOCABULARY}"
    name = verb_match.group("name").lower()
    if not _CENSUS_DECISION_VERB_NAME_RE.match(name):
        return f"has Decision '{value}', whose verb name is not a lower-case hyphenated name"
    if known_verbs is not None and name not in known_verbs:
        return (
            f"has Decision '{value}', but {name} is not a decision-wake verb "
            f"(one of {', '.join(sorted(known_verbs))})"
        )
    return None


def _scan_census_decisions(
    repo_root: Path, known_verbs: Optional[Iterable[str]] = None
) -> list[_Finding]:
    """The Decision-column check (see "DECISION COLUMN" in the module
    docstring): every numbered row of the census's "At-a-glance rollup"
    table must carry a Decision cell, the first cell after its Class cell,
    holding a value from the closed set. A row whose last cell is its Class
    has no Decision cell; a row whose Decision cell is empty, outside the
    set, or names an unknown verb carries a wrong one. Each is one
    ``decision_invalid`` finding.

    *known_verbs* is the verb vocabulary to check ``verb(<name>)`` against;
    by default it is ``decide.VERBS`` from this script's own tree
    (``_decision_wake_verbs``), and verb names are checked by shape only
    when that cannot be loaded. Fails soft like ``_scan_census_rollup``: a
    missing census file costs this check, never a crash."""
    census_path = repo_root / _CENSUS_DOC_RELATIVE_PATH
    try:
        text = census_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    verbs = frozenset(known_verbs) if known_verbs is not None else _decision_wake_verbs()

    findings: list[_Finding] = []
    for idx, raw_line in enumerate(text.splitlines()):
        number_match = _CENSUS_TABLE_ROW_NUMBER_RE.match(raw_line)
        if not number_match:
            continue
        decision: Optional[str]
        problem: Optional[str]
        tail = _census_row_tail(raw_line)
        if tail is not None:
            # The Decision cell is the first cell after Class; a Converted
            # cell may follow it (`_scan_census_converted` checks that one).
            cells_after_class = tail[1]
            if not cells_after_class:
                decision, problem = None, "has no Decision cell after its Class cell"
            else:
                decision = cells_after_class[0].strip()
                problem = _decision_problem(cells_after_class[0], verbs)
        else:
            # No Class cell parses: read the trailing cell as the Decision.
            cell_match = _CENSUS_TABLE_LAST_CELL_RE.search(raw_line)
            cell = cell_match.group("cell") if cell_match else ""
            if cell.strip().strip("`").strip() in CLASSIFICATION_TOKENS:
                decision, problem = None, "has no Decision cell after its Class cell"
            else:
                decision = cell.strip()
                problem = _decision_problem(cell, verbs)
        if problem is None:
            continue
        snippet = raw_line.strip()
        if len(snippet) > _CENSUS_ROW_SNIPPET_CHARS:
            snippet = snippet[: _CENSUS_ROW_SNIPPET_CHARS - 3] + "..."
        findings.append(
            _Finding(
                path=census_path,
                line=idx + 1,
                snippet=snippet,
                kind=_FINDING_KIND_DECISION_INVALID,
                row_number=int(number_match.group("num")),
                decision=decision,
                decision_problem=problem,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Converted column and CONVERT call-site check (see "CONVERTED COLUMN" in
# the module docstring)
# ---------------------------------------------------------------------------

# The Converted column's closed set (docs/blocking-site-census.md,
# "Classification vocabulary"): a CONVERT row reads yes or no, every other
# row reads n/a.
_CENSUS_CONVERTED_YES = "yes"
_CENSUS_CONVERTED_NO = "no"
_CENSUS_CONVERTED_NA = "n/a"
_CENSUS_CONVERTED_VALUES = (_CENSUS_CONVERTED_YES, _CENSUS_CONVERTED_NO, _CENSUS_CONVERTED_NA)
_CENSUS_CONVERTED_VOCABULARY = "yes, no or n/a"

# A table cell boundary: a `|` not escaped as `\|` (code spans in the
# Source and Trigger cells escape theirs).
_CENSUS_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
# A backticked token in a Source cell: a path, a locator key, or prose.
_CENSUS_SOURCE_TOKEN_RE = re.compile(r"`([^`]+)`")
# Source-cell tokens that name a file the check can read.
_CENSUS_SOURCE_FILE_SUFFIXES = (".sh", ".py")
# Where a Source-cell path is resolved from, relative to the repo root, in
# order: the census writes repo-relative paths (`team/scripts/x.sh`) and
# short ones relative to team/scripts/ (`lib/temp.sh`).
_CENSUS_SOURCE_BASES = (".", "team/scripts", "team")

# Python's usage-error status (argparse exits 2 on a malformed command
# line, and this tree's scripts return 2 for their own argument errors): a
# Python exit whose only status is 2 is an operator-CLI refusal, the
# SITE-TYPE the census scope excludes, unless a CONVERT Locator Index entry
# names it as a site.
_PYTHON_USAGE_ERROR_STATUS = 2

# Calls that end a Python process with the status of their first argument.
_PYTHON_EXIT_CALL_NAMES = frozenset({"sys.exit", "exit", "quit", "os._exit"})


def _census_row_source_cell(raw_line: str) -> str:
    """The Source cell (the third column) of one numbered table row, or an
    empty string when the row has fewer cells."""
    cells = _CENSUS_CELL_SPLIT_RE.split(raw_line.strip())
    # cells[0] is the empty text before the leading pipe; # is cells[1].
    return cells[3] if len(cells) > 3 else ""


def _census_row_call_sites(
    source_cell: str, repo_root: Path, census_locators: _CensusLocators
) -> list[tuple[Path, Optional[str]]]:
    """The call sites one CONVERT row's Source cell names, as
    ``(resolved file, locator key or None)``.

    A backticked token that is a Locator Index key names one site, read
    through that key's window; when any token is a key, the keys are the
    row's call sites. Otherwise every backticked ``*.sh``/``*.py`` path that
    resolves to a file is a call site, read whole (``None`` key)."""
    tokens = [token.strip() for token in _CENSUS_SOURCE_TOKEN_RE.findall(source_cell)]
    keyed: list[tuple[Path, Optional[str]]] = []
    for token in tokens:
        for entry_path in sorted(census_locators.by_token, key=str):
            if token in census_locators.by_token[entry_path] and (entry_path, token) not in keyed:
                keyed.append((entry_path, token))
    if keyed:
        return keyed

    files: list[tuple[Path, Optional[str]]] = []
    for token in tokens:
        if not token.endswith(_CENSUS_SOURCE_FILE_SUFFIXES) or re.search(r"[\s{}*<>]", token):
            continue
        for base in _CENSUS_SOURCE_BASES:
            candidate = (repo_root / base / token).resolve()
            if candidate.is_file():
                if (candidate, None) not in files:
                    files.append((candidate, None))
                break
    return files


def _python_call_name(func: ast.expr) -> str:
    """Dotted name of a call's target (``sys.exit``), or ``""``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        owner = _python_call_name(func.value)
        return f"{owner}.{func.attr}" if owner else ""
    return ""


def _python_nonzero_statuses(expr: Optional[ast.expr]) -> set[int]:
    """The literal non-zero integer statuses *expr* can evaluate to: the
    literal itself, or one inside a conditional or boolean expression
    (``0 if ok else 1``). Arguments of nested calls are not statuses."""
    statuses: set[int] = set()
    pending = [expr] if expr is not None else []
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and not isinstance(node.value, bool) and node.value != 0:
                statuses.add(node.value)
            continue
        if isinstance(node, (ast.Call, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return statuses


def _python_own_returns(function: ast.AST) -> list[ast.Return]:
    """``return`` statements of *function* itself, not of functions or
    classes nested inside it."""
    returns: list[ast.Return] = []
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Return):
            returns.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return returns


def _python_fatal_exits(text: str) -> dict[int, set[int]]:
    """``{line: statuses}`` for the 1-indexed lines of a Python file that
    end its process non-zero: an exit call (``sys.exit``, ``exit``,
    ``os._exit``) or ``raise SystemExit`` with a literal non-zero status,
    and a literal non-zero ``return`` in ``main`` -- the status
    ``sys.exit(main())`` exits with -- or in any function ``main`` hands its
    return to (``return _report(...)``), followed transitively. A file that
    does not parse falls back to the missing-signal scan's ``sys.exit(N)``
    pattern, status unknown (``{0}``)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {
            idx + 1: {0}
            for idx, line in enumerate(text.splitlines())
            if _PYTHON_FATAL_EXIT_RE.search(line.split("#", 1)[0])
        }

    fatal: dict[int, set[int]] = {}

    def _record(line_no: int, statuses: set[int]) -> None:
        if statuses:
            fatal.setdefault(line_no, set()).update(statuses)

    functions: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, node)
        elif isinstance(node, ast.Call) and _python_call_name(node.func) in _PYTHON_EXIT_CALL_NAMES:
            if node.args:
                _record(node.lineno, _python_nonzero_statuses(node.args[0]))
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            if _python_call_name(node.exc.func) == "SystemExit" and node.exc.args:
                _record(node.lineno, _python_nonzero_statuses(node.exc.args[0]))

    status_functions: set[str] = set()
    pending = ["main"]
    while pending:
        name = pending.pop()
        if name in status_functions or name not in functions:
            continue
        status_functions.add(name)
        for ret in _python_own_returns(functions[name]):
            if isinstance(ret.value, ast.Call):
                pending.append(_python_call_name(ret.value.func))
            else:
                _record(ret.lineno, _python_nonzero_statuses(ret.value))
    return fatal


_BASH_EXIT_STATUS_RE = re.compile(r"\bexit\s+([1-9]\d*)\b")


def _fatal_exits(path: Path, lines: list[str]) -> dict[int, set[int]]:
    """``{line: statuses}`` for the 1-indexed lines of *path* that end its
    process non-zero: bash ``exit N`` with a literal non-zero N, or the
    Python statements ``_python_fatal_exits`` names."""
    if path.suffix == ".py":
        return _python_fatal_exits("\n".join(lines))
    fatal: dict[int, set[int]] = {}
    for idx, line in enumerate(lines):
        statuses = {int(m.group(1)) for m in _BASH_EXIT_STATUS_RE.finditer(_strip_bash_comment(line))}
        if statuses:
            fatal[idx + 1] = statuses
    return fatal


def _key_window_lines(lines: list[str], key: str, is_python: bool) -> set[int]:
    """The lines one Locator Index key resolves to in *lines*: a marker's
    forward window, or an emitter call's (the exemption windows)."""
    emitter_re = _PYTHON_EMITTER_RE if is_python else _BASH_EMITTER_RE
    return _resolve_anchor_marker_lines(lines, {key}) | _resolve_error_key_lines(
        lines, {key}, emitter_re, is_python
    )


def _site_fatal_lines_whole_file(
    path: Path, lines: list[str], census_locators: _CensusLocators
) -> list[int]:
    """Fatal exit lines of a whole-file call site that belong to no other
    site. Not this row's: an exit some other class claims -- a non-CONVERT
    Locator Index entry resolves to it, or an inline ``BLOCK-WORTHY`` /
    ``AGENT-RESOLVABLE`` comment sits in its window -- and an operator-CLI
    argument refusal, the SITE-TYPE scope (a bash positional ``case``
    dispatch or usage-guarded exit, a Python usage-guarded exit, or a Python
    exit whose only status is the usage-error status 2 that no CONVERT
    Locator Index entry names)."""
    is_python = path.suffix == ".py"
    resolved = path.resolve()
    claimed: set[int] = set()
    convert_named: set[int] = set()
    for key, cls in census_locators.token_class.get(resolved, {}).items():
        if cls == "CONVERT":
            convert_named |= _key_window_lines(lines, key, is_python)
        else:
            claimed |= _key_window_lines(lines, key, is_python)
    for line_no, cls in census_locators.line_class.get(resolved, {}).items():
        (convert_named if cls == "CONVERT" else claimed).add(line_no)

    case_ranges = [] if is_python else _bash_positional_case_ranges(lines)
    own: list[int] = []
    for line_no, statuses in sorted(_fatal_exits(path, lines).items()):
        if line_no in claimed:
            continue
        idx = line_no - 1
        window = lines[max(0, idx - _WINDOW_LINES + 1) : idx + 1]
        declared = {
            match.group(1) for line in window for match in _CLASSIFICATION_TOKEN_RE.finditer(line)
        }
        if declared and "CONVERT" not in declared:
            continue
        if is_python:
            if statuses == {_PYTHON_USAGE_ERROR_STATUS} and line_no not in convert_named:
                continue
            if _window_has_usage_guard(
                window, lambda l: l.split("#", 1)[0], _PYTHON_USAGE_DEFINITION_RE
            ):
                continue
        elif _is_operator_cli_bash_site(idx, case_ranges, window):
            continue
        own.append(line_no)
    return own


def _converted_problem(cell: Optional[str], cls: str) -> Optional[str]:
    """What is wrong with one Converted cell, as a phrase completing "the
    row ...", or ``None`` when it holds the closed-set value its class
    takes. *cell* is ``None`` when the row has no Converted cell."""
    if cell is None:
        return "has no Converted cell after its Decision cell"
    value = cell.strip().strip("`").strip()
    if not value:
        return "has an empty Converted cell"
    if value.lower() not in _CENSUS_CONVERTED_VALUES:
        return f"has Converted '{value}', which is not one of {_CENSUS_CONVERTED_VOCABULARY}"
    if cls == "CONVERT" and value.lower() == _CENSUS_CONVERTED_NA:
        return "is classed CONVERT but its Converted cell reads n/a; a CONVERT row reads yes or no"
    if cls != "CONVERT" and value.lower() != _CENSUS_CONVERTED_NA:
        return (
            f"is classed {cls} but its Converted cell reads {value}; "
            "only a CONVERT row is converted, so it reads n/a"
        )
    return None


def _scan_census_converted(
    repo_root: Path, census_locators: Optional[_CensusLocators] = None
) -> list[_Finding]:
    """The Converted-column check (see "CONVERTED COLUMN" in the module
    docstring), two finding kinds:

      - ``converted_invalid`` -- a numbered row has no Converted cell (the
        cell after Decision), an empty one, a value outside yes / no / n/a,
        or a value its class does not take.
      - ``convert_site_fatal`` -- a CONVERT row's call site (see
        ``_census_row_call_sites``) still exits non-zero: one finding per
        row and file, at the file's first fatal line, whatever the row's
        Converted cell says.

    *census_locators* defaults to the census's own Locator Index. Fails
    soft like ``_scan_census_decisions``: a missing census file costs this
    check, and an unreadable call-site file costs that one site."""
    census_path = repo_root / _CENSUS_DOC_RELATIVE_PATH
    try:
        text = census_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    locators = census_locators if census_locators is not None else _load_census_locators(repo_root)

    findings: list[_Finding] = []
    for idx, raw_line in enumerate(text.splitlines()):
        number_match = _CENSUS_TABLE_ROW_NUMBER_RE.match(raw_line)
        if not number_match:
            continue
        tail = _census_row_tail(raw_line)
        if tail is None:
            # No Class cell parses; the rollup check reports the row.
            continue
        cls, cells_after_class = tail
        row_number = int(number_match.group("num"))
        converted_cell = cells_after_class[1] if len(cells_after_class) > 1 else None
        converted = converted_cell.strip().strip("`").strip() if converted_cell is not None else None
        snippet = raw_line.strip()
        if len(snippet) > _CENSUS_ROW_SNIPPET_CHARS:
            snippet = snippet[: _CENSUS_ROW_SNIPPET_CHARS - 3] + "..."

        problem = _converted_problem(converted_cell, cls)
        if problem is not None:
            findings.append(
                _Finding(
                    path=census_path,
                    line=idx + 1,
                    snippet=snippet,
                    kind=_FINDING_KIND_CONVERTED_INVALID,
                    row_number=row_number,
                    converted=converted,
                    converted_problem=problem,
                )
            )
        if cls != "CONVERT":
            continue

        fatal_by_file: dict[Path, tuple[int, Optional[str], list[str]]] = {}
        for site_path, key in _census_row_call_sites(
            _census_row_source_cell(raw_line), repo_root, locators
        ):
            try:
                site_lines = site_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            if key is None:
                fatal_lines = _site_fatal_lines_whole_file(site_path, site_lines, locators)
            else:
                window = _key_window_lines(site_lines, key, site_path.suffix == ".py")
                fatal_lines = sorted(n for n in _fatal_exits(site_path, site_lines) if n in window)
            if fatal_lines and (
                site_path not in fatal_by_file or fatal_lines[0] < fatal_by_file[site_path][0]
            ):
                fatal_by_file[site_path] = (fatal_lines[0], key, site_lines)
        for site_path, (line_no, key, site_lines) in fatal_by_file.items():
            findings.append(
                _Finding(
                    path=site_path,
                    line=line_no,
                    snippet=site_lines[line_no - 1].strip(),
                    kind=_FINDING_KIND_CONVERT_SITE_FATAL,
                    token=key,
                    row_number=row_number,
                    converted=converted,
                )
            )
    return findings

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


def _finding_cause(finding: _Finding, rel: str) -> str:
    """The "what is wrong" half of a finding's remedy pair -- one sentence
    per finding kind (see the ``_FINDING_KIND_*`` constants)."""
    if finding.kind == _FINDING_KIND_MISSING_SIGNAL:
        signals_str = " and ".join(_signal_description(s) for s in finding.missing_signals)
        return f"{rel}:{finding.line} -- refusal site is missing {signals_str}"
    if finding.kind == _FINDING_KIND_UNINDEXED_ANCHOR:
        return (
            f"{rel}:{finding.line} -- CENSUS-ANCHOR token '{finding.token}' has no "
            "matching 'path:token CLASS' line in docs/blocking-site-census.md's "
            "CENSUS-LOCATOR-INDEX"
        )
    if finding.kind == _FINDING_KIND_UNRESOLVED_LOCATOR:
        return (
            f"docs/blocking-site-census.md's CENSUS-LOCATOR-INDEX names "
            f"'{rel}:{finding.token}' ({finding.index_class or '<CLASS>'}) but no "
            f"refusal site in {rel} resolves that key -- no matching line, no "
            "CENSUS-ANCHOR marker, and no dual-channel emitter call carrying that "
            "literal error key"
        )
    if finding.kind == _FINDING_KIND_ROLLUP_MISMATCH:
        return (
            f"{rel}:{finding.line} -- the \"**Rollup: ...**\" summary line reads "
            f"'{finding.rollup_summary}' but the inventory table above it actually "
            f"contains {finding.table_summary}"
        )
    if finding.kind == _FINDING_KIND_DECISION_INVALID:
        return (
            f"{rel}:{finding.line} -- At-a-glance rollup row {finding.row_number} "
            f"{finding.decision_problem}"
        )
    if finding.kind == _FINDING_KIND_CONVERTED_INVALID:
        return (
            f"{rel}:{finding.line} -- At-a-glance rollup row {finding.row_number} "
            f"{finding.converted_problem}"
        )
    if finding.kind == _FINDING_KIND_CONVERT_SITE_FATAL:
        via = f" (through Locator Index key '{finding.token}')" if finding.token else ""
        marked = (
            ", although the row's Converted cell reads yes"
            if (finding.converted or "").lower() == _CENSUS_CONVERTED_YES
            else ""
        )
        return (
            f"{rel}:{finding.line} -- census row {finding.row_number} is CONVERT but this "
            f"call site{via} still exits non-zero ('{finding.snippet}'){marked}"
        )
    return (
        f"{rel}:{finding.line} -- CENSUS-ANCHOR token '{finding.token}' is indexed "
        f"as {finding.index_class} in docs/blocking-site-census.md but its own "
        f"inline comment declares {finding.site_class}"
    )


def _finding_remedy(finding: _Finding, rel: str) -> str:
    """The "what to do about it" half of a finding's remedy pair."""
    if finding.kind == _FINDING_KIND_MISSING_SIGNAL:
        return _example_fix_line(finding, rel)
    if finding.kind == _FINDING_KIND_UNINDEXED_ANCHOR:
        cls_hint = finding.site_class or "<CLASS>"
        return (
            f"Add '{rel}:{finding.token} {cls_hint}' to the CENSUS-LOCATOR-INDEX "
            "fenced block in docs/blocking-site-census.md, using the class the "
            "site's own inline comment declares"
        )
    if finding.kind == _FINDING_KIND_UNRESOLVED_LOCATOR:
        return (
            f"Open {rel}, find the refusal this entry meant, and either add "
            f"'# CENSUS-ANCHOR: {finding.token}' directly above it (skip this if "
            f"{finding.token} is already the literal first argument to a "
            "dual-channel emitter call there), or correct the Locator Index "
            "entry to the key that actually resolves at that site"
        )
    if finding.kind == _FINDING_KIND_ROLLUP_MISMATCH:
        return (
            f"Edit the \"**Rollup: ...**\" line and its per-class line in {rel} "
            f"to read '{finding.table_summary}' -- derived directly from the "
            "table's own numbered rows, so it cannot drift from them again "
            "(the document itself is a WRITER-owned edit; this lint only "
            "reports the drift)"
        )
    if finding.kind == _FINDING_KIND_DECISION_INVALID:
        return (
            f"Edit row {finding.row_number} of the At-a-glance rollup table in {rel} so it "
            "ends in a Decision cell after its Class cell, naming what the decision wake does "
            f"when the site fires: {_CENSUS_DECISION_VOCABULARY} (none when it never acts on "
            "the site; the description above the table says how to choose)"
        )
    if finding.kind == _FINDING_KIND_CONVERTED_INVALID:
        return (
            f"Edit row {finding.row_number} of the At-a-glance rollup table in {rel} so its "
            "Converted cell, after the Decision cell, reads yes (a CONVERT row whose call sites "
            "all warn and continue), no (a CONVERT row with a call site that still exits "
            "non-zero) or n/a (a BLOCK-WORTHY or AGENT-RESOLVABLE row)"
        )
    if finding.kind == _FINDING_KIND_CONVERT_SITE_FATAL:
        return (
            f"Make {rel}:{finding.line} warn and continue: report the finding through the "
            "dual-channel emitter and exit 0, so the gated runners merge it per instance "
            "(team/scripts/lint_hygiene.py is the model), then set census row "
            f"{finding.row_number}'s Converted cell to yes; if the site cannot be converted, "
            f"reclassify row {finding.row_number} with a one-sentence justification instead"
        )
    return (
        f"Reconcile '{rel}:{finding.token}' in docs/blocking-site-census.md's "
        f"CENSUS-LOCATOR-INDEX (currently {finding.index_class}) with the site's "
        f"own declared class ({finding.site_class}), or fix whichever one is wrong"
    )


def _format_summary_line(findings: list[_Finding]) -> str:
    """The remedy block's header, naming how many findings of each kind
    were produced -- generalizes the old "N refusal site(s)..." text now
    that this lint reports eight finding kinds, not just one."""
    counts = {
        _FINDING_KIND_MISSING_SIGNAL: 0,
        _FINDING_KIND_UNINDEXED_ANCHOR: 0,
        _FINDING_KIND_ANCHOR_CLASS_MISMATCH: 0,
        _FINDING_KIND_UNRESOLVED_LOCATOR: 0,
        _FINDING_KIND_ROLLUP_MISMATCH: 0,
        _FINDING_KIND_DECISION_INVALID: 0,
        _FINDING_KIND_CONVERTED_INVALID: 0,
        _FINDING_KIND_CONVERT_SITE_FATAL: 0,
    }
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1

    parts = []
    if counts[_FINDING_KIND_MISSING_SIGNAL]:
        parts.append(
            f"{counts[_FINDING_KIND_MISSING_SIGNAL]} refusal site(s) missing a "
            "census classification and/or the dual-channel error contract"
        )
    if counts[_FINDING_KIND_UNINDEXED_ANCHOR]:
        parts.append(
            f"{counts[_FINDING_KIND_UNINDEXED_ANCHOR]} CENSUS-ANCHOR marker(s) "
            "with no matching Locator Index entry"
        )
    if counts[_FINDING_KIND_ANCHOR_CLASS_MISMATCH]:
        parts.append(
            f"{counts[_FINDING_KIND_ANCHOR_CLASS_MISMATCH]} CENSUS-ANCHOR "
            "marker(s) whose Locator Index class disagrees with the site"
        )
    if counts[_FINDING_KIND_UNRESOLVED_LOCATOR]:
        parts.append(
            f"{counts[_FINDING_KIND_UNRESOLVED_LOCATOR]} Locator Index entry/entries "
            "that do not resolve to any site"
        )
    if counts[_FINDING_KIND_ROLLUP_MISMATCH]:
        parts.append(
            f"{counts[_FINDING_KIND_ROLLUP_MISMATCH]} rollup summary line(s) that "
            "disagree with the inventory table"
        )
    if counts[_FINDING_KIND_DECISION_INVALID]:
        parts.append(
            f"{counts[_FINDING_KIND_DECISION_INVALID]} inventory table row(s) with a "
            "missing or unknown Decision"
        )
    if counts[_FINDING_KIND_CONVERTED_INVALID]:
        parts.append(
            f"{counts[_FINDING_KIND_CONVERTED_INVALID]} inventory table row(s) with a "
            "missing or out-of-set Converted cell"
        )
    if counts[_FINDING_KIND_CONVERT_SITE_FATAL]:
        parts.append(
            f"{counts[_FINDING_KIND_CONVERT_SITE_FATAL]} CONVERT call site(s) that still "
            "exit non-zero"
        )
    return f"lint_gate_census_compliance: {len(findings)} finding(s) -- " + "; ".join(parts) + " (non-blocking finding)"


def _format_remedy_block(findings: list[_Finding], repo_root: Path, lint_script: Path) -> str:
    causes: list[str] = []
    commands: list[str] = []
    for finding in findings:
        rel = _relative(finding.path, repo_root)
        causes.append(_finding_cause(finding, rel))
        commands.append(_finding_remedy(finding, rel))
    commands.append(f'python3 "{lint_script}"')

    lines = [
        _format_summary_line(findings),
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
        "through the dual-channel error contract; every CENSUS-ANCHOR marker "
        "must be named by a Locator Index entry that agrees with the site's "
        "declared class (team/roles/CODER.md, \"Authoring a new gate\" / "
        "\"Writing a compliant anchor\")."
    )
    return "\n".join(lines)


def _finding_to_dict(finding: _Finding, repo_root: Path) -> dict:
    rel = _relative(finding.path, repo_root)
    return {
        "lint": "gate_census_compliance",
        "severity": "quality",
        "kind": finding.kind,
        "file": rel,
        "line": finding.line,
        "snippet": finding.snippet,
        "missing_signals": list(finding.missing_signals),
        "token": finding.token,
        "index_class": finding.index_class,
        "site_class": finding.site_class,
        "table_summary": finding.table_summary,
        "rollup_summary": finding.rollup_summary,
        "row": finding.row_number,
        "decision": finding.decision,
        "decision_problem": finding.decision_problem,
        "converted": finding.converted,
        "converted_problem": finding.converted_problem,
        "cause": _finding_cause(finding, rel),
        "remedy": _finding_remedy(finding, rel),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _default_bash_files(script_dir: Path) -> list[Path]:
    """Resolve ``_CENSUS_BASH_FILES`` and ``_CENSUS_BASH_GLOBS`` against
    *script_dir*, skipping any entry that no longer exists (a stale list
    entry costs a silently narrower scan, not a crash -- the same fail-soft
    posture the Python counterpart uses for unreadable files)."""
    files = [script_dir / rel for rel in _CENSUS_BASH_FILES]
    for pattern in _CENSUS_BASH_GLOBS:
        files.extend(sorted(script_dir.glob(pattern)))
    unique = {f.resolve(): f for f in files if f.is_file()}
    return sorted(unique.values())


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
            "the dual-channel error contract. With neither --bash-dirs nor "
            "--python-dir it also checks docs/blocking-site-census.md against "
            "the tree and itself: Locator Index entries resolve, the rollup "
            "line matches the inventory table, every table row carries a "
            "Decision of verb(<name>), inbox, runaway or none and a Converted "
            "cell of yes, no or n/a, and no CONVERT row's call site still exits "
            "non-zero. Quality-class lint: findings "
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
    """Run the gate census compliance lint over the candidate refusal sites.

    Prints each finding and a count. Exit codes: 0 always, whatever was found (quality class); 2 usage error (argparse).
    """
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
        f"({census_locators.total_entries()} census-anchored site(s) exempted)",
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

    # LOCATOR INDEX -> SITE reconciliation: every entry the census declares
    # must resolve to something real, independent of whether its file is on
    # the default scan surface above (several documented entries name files
    # that are not -- see the census doc's "Not every entry is lint-consumed").
    # Only run this against the census-aligned DEFAULT scan surface -- ---bash-dirs
    # / --python-dir point this lint at an unrelated directory tree for an
    # ad-hoc sweep (including every synthetic-fixture test in this lint's own
    # suite), and the real repo's census has nothing to say about a fixture
    # tree it was never written against.
    if not args.bash_dirs and not args.python_dir:
        findings.extend(_scan_locator_resolution(census_locators, repo_root))

    # ROLLUP RECONCILIATION: the census document's own
    # "**Rollup: ...**" summary line against its own inventory table --
    # both sides live in docs/blocking-site-census.md, so (like the
    # locator-resolution scan above) this only runs against the
    # census-aligned DEFAULT scan surface, never against an ad-hoc
    # --bash-dirs/--python-dir fixture sweep the real census has nothing
    # to say about.
    if not args.bash_dirs and not args.python_dir:
        findings.extend(_scan_census_rollup(repo_root))

    # DECISION COLUMN: every inventory table row names a decision from the
    # closed set -- again the census document alone, so default scope only.
    if not args.bash_dirs and not args.python_dir:
        findings.extend(_scan_census_decisions(repo_root))

    # CONVERTED COLUMN: every row's Converted cell is from the closed set,
    # and every CONVERT row whose call site still exits non-zero is
    # reported -- the census against the tree it names, so default scope only.
    if not args.bash_dirs and not args.python_dir:
        findings.extend(_scan_census_converted(repo_root, census_locators))

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

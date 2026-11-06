#!/usr/bin/env python3
"""
lint_help_presence.py
=====================
Lint: every executable script in the tree — shell script, Python entry
point or other ``#!`` script — must implement a ``--help`` or ``-h`` flag
that prints usage text. Each script that does not is reported as a finding.

Scripts that are exempt (process-internal helpers invoked by other scripts
rather than directly by operators, or shims whose sourced library answers
``--help``) are listed in an exempt-list data file read at runtime.

Scope
-----
By default the scanner walks the whole tree recursively: the repository
root in a dev tree (this file at ``team/scripts/``), or the kanban root in
an install (this file at ``scripts/``); ``--root`` names another tree. A
file is scanned when it is a regular file (not a symlink) with an execute
bit set and either a ``.sh``/``.py`` suffix or a ``#!`` first line.
Every ``*.sh``/``*.py`` file directly in the tree's scripts directory
(``team/scripts/`` or ``scripts/``) and its ``cm/`` subdirectory is scanned
too, execute bit or not: those are entry points run as
``python3 scripts/<name>.py``. Directories whose name starts with ``.``,
``__pycache__`` and ``node_modules`` are not entered.

Exempt by rule, because they are sourced rather than run and so have no
command line of their own:

  - sourced libraries: any file with a ``lib`` directory anywhere in its
    path (``team/scripts/lib/``, ``team/scripts/dashboard/lib/``,
    ``team/verification/lib/``, ...);
  - workflow plugins: a ``workflow.sh`` directly inside a
    ``workflows/<type>/`` directory (``team/workflows/release/workflow.sh``).

When ``--scripts-dir`` is supplied the scanner operates on that single
directory only (non-recursive), and every ``*.sh``/``*.py`` file in it is
scanned whatever its mode.

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

  6. (Python scripts only) An ``argparse.ArgumentParser(`` constructor call
     without a companion ``add_help=False`` — argparse adds a working
     ``-h``/``--help`` flag automatically unless that default is turned
     off, so a script built this way gets a genuine help handler for free.

  7. A call (on a line that is not a comment) to the shared header-help
     helpers in ``team/scripts/lib/header_help.sh``:
     ``header_help_exit_if_requested`` or ``header_help_print``.

These patterns match the conventions used across the team/scripts/ codebase.
A script that passes none of these checks is flagged as missing help.

Exempt list
-----------
A plain-text data file (one entry per line, ``#`` comments allowed, blank
lines ignored) lists scripts that are exempt from the check.  An entry
matches a script whose label (its path relative to the scanned tree, or
its basename under ``--scripts-dir``) equals the entry or ends with
``/`` plus the entry: a bare basename (``wake-batch.sh``), a trailing path
(``wake/claude.sh``, ``cm/open-doc.sh``) or a full label all work.  The
default path is ``help_presence_exempt.txt`` beside this script.  Override
via ``--exempt-list``.

Help-quality checks (quality class — recorded as findings like the rest)
------------------------------------------------------------------------
Two further checks run over every bash script (``*.sh``) under the scan
root, recursively, skipping any ``lib/`` directory (sourced helpers, not
entry points). The scan root is ``team/scripts/`` by default, or the
``--scripts-dir`` directory when one is given.

  (a) Undocumented flag. For a script that has a help handler, every long
      flag it parses must be named in its help source (``--help`` itself
      excepted: answering it is the presence check). A flag counts as
      parsed when it appears as a ``--name`` alternative in a ``case`` arm
      (``--name)``, ``--name=*)``, ``-n|--name)``), in an
      ``argparse_has name`` call, in an ``argparse_parse --value-flags "..."``
      list, or in an ``OPERATOR_VALID_FLAGS=(...)`` array. The help source
      is the header comment block (line 2 up to the first non-comment
      line), every heredoc body, the body of any function whose name
      contains ``usage`` or ``help``, any ``*USAGE*``/``*HELP*`` variable
      assignment, and ``OPERATOR_FLAG_HELP`` entries; a script that renders
      its help with ``operator_args_render_help_for_flags`` documents every
      ``OPERATOR_VALID_FLAGS`` entry by construction.

  (b) Fixed help range. A script must not print help through a fixed
      ``sed -n '<n>,<m>p' "$0"`` / ``"${BASH_SOURCE[0]}"`` range — the range
      truncates the help or runs into code on the next header edit. Use
      ``header_help_print`` from ``team/scripts/lib/header_help.sh``, which
      prints the header up to its first non-comment line.

Each finding is one narrated line naming the script; the run ends with a
count.

Findings
--------
CONVERT (quality class, team/SOP-core.md "Gates and Refusals"): a script
without ``--help`` is a discoverability gap, not a failure of the run that
finds it.  Each finding is printed and recorded as one object whose
``kind`` is ``missing-help``, ``undocumented-flag``,
``fixed-sed-help-range`` or ``exempt-list-missing`` (the exempt list could
not be read, so the presence check did not run); with --findings-file the
findings are written as a JSON array (the shape
pgai_agent_kanban.lint.findings defines), which the gated runners merge per
instance with gated_runner_merge_findings_file.

Exit codes
----------
  0   The lint ran, whatever it found (every kind above is a finding).
  2   Usage error: a --scripts-dir (or team/scripts/) that does not exist.

Usage
-----
  python3 scripts/lint_help_presence.py [--root PATH | --scripts-dir PATH]
                                        [--exempt-list PATH]
                                        [--findings-file PATH]
                                        [--verbose]

  --root PATH          Tree to scan recursively (default: the repository
                       root, or the kanban root in an install). The
                       help-quality checks run over its team/scripts/ (or
                       scripts/) directory.
  --scripts-dir PATH   Single directory to scan, non-recursive (instead of
                       the tree scan); also the root of the recursive
                       help-quality scan.
  --exempt-list PATH   Exempt-list data file (default: team/scripts/
                       help_presence_exempt.txt relative to repo root).
  --findings-file PATH Write the findings as a JSON array to PATH (parent
                       directories created; written even when empty).
  --verbose, -v        Print each file name as it is scanned.

Examples
--------
  # From repo root (scans every executable script in the tree):
  python3 team/scripts/lint_help_presence.py

  # With explicit single directory:
  python3 team/scripts/lint_help_presence.py \\
      --scripts-dir /path/to/team/scripts

  # With explicit paths:
  python3 team/scripts/lint_help_presence.py \\
      --scripts-dir /path/to/team/scripts \\
      --exempt-list /path/to/team/scripts/help_presence_exempt.txt

  # Record findings for a downstream merge:
  python3 team/scripts/lint_help_presence.py \\
      --findings-file "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/help_presence.json"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# The finding shape and the error-contract emitter live in the team package
# beside scripts/; this script is run directly as well as imported, so the
# package root is put on sys.path from this file's own location.
_TEAM_DIR = Path(__file__).resolve().parent.parent  # team/
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lint.findings import (  # noqa: E402
    display_path,
    quality_finding,
    write_findings_file,
)

_LINT_NAME = "help_presence"

# ---------------------------------------------------------------------------
# Help-detection patterns
# ---------------------------------------------------------------------------

# Pattern 1: case-branch ``--help)`` / ``-h)`` / combined forms.
# Captures: --help) or -h) or --help|-h) or -h|--help) and variants.
_CASE_HELP: re.Pattern[str] = re.compile(r"""(?:--help|-h)\s*[|)]""")

# Pattern 2: operator_args.sh framework: argparse_has (with or without quotes).
# Forms: argparse_has help  argparse_has "help"  argparse_has 'h'  argparse_has h
_ARGPARSE_HELP: re.Pattern[str] = re.compile(r"""argparse_has\s+['"']?(help|h)['"']?\b""")

# Pattern 3: string comparison against "--help" or "-h" (scripts that use
# $arg == "--help" or [[ "$1" == "--help" ]] patterns).
_ARG_CMP_HELP: re.Pattern[str] = re.compile(r"""["'](?:--help|-h)["']\s*""")

# Pattern 4: exec/delegate with "$@" passthrough (thin dispatcher scripts
# that forward all flags including --help to a subcommand).
_EXEC_PASSTHROUGH: re.Pattern[str] = re.compile(r"""\bexec\b.*\"\$@\"""")

# Pattern 5: --help in a heredoc documentation block (indented --help entry).
# Matches lines like "  --help, -h   Show help" inside a cat <<EOF block.
_HEREDOC_HELP: re.Pattern[str] = re.compile(r"""^\s{2,}--help\b""")

# Pattern 7: a call to the shared header-help helpers (lib/header_help.sh)
# on a line that is not a comment; both print the script's header as usage.
_HEADER_HELP_CALL: re.Pattern[str] = re.compile(r"""^(?!\s*#).*\bheader_help_(?:exit_if_requested|print)\b""")

# Combined list: a script is help-aware if any line matches any pattern.
_HELP_PATTERNS: list[re.Pattern[str]] = [
    _CASE_HELP,
    _ARGPARSE_HELP,
    _ARG_CMP_HELP,
    _EXEC_PASSTHROUGH,
    _HEREDOC_HELP,
    _HEADER_HELP_CALL,
]

# Pattern 6 (``.py`` only): a Python entry point built on
# ``argparse.ArgumentParser(`` gets a working ``-h``/``--help`` flag for
# free, unless the constructor call disables it with ``add_help=False``.
_ARGPARSE_CONSTRUCTOR: re.Pattern[str] = re.compile(r"""argparse\.ArgumentParser\(""")
_ARGPARSE_HELP_DISABLED: re.Pattern[str] = re.compile(r"""add_help\s*=\s*False""")


def _is_python_script(path: Path) -> bool:
    """True for a ``.py`` file or a suffix-less script with a python shebang."""
    if path.suffix == ".py":
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as handle:
            first_line = handle.readline(200)
    except OSError:
        return False
    return first_line.startswith(b"#!") and b"python" in first_line


def _has_help_handler(path: Path) -> bool:
    """Return True if *path* contains any help-presence detection pattern.

    Python scripts get one extra whole-file check (pattern 6, above) ahead of
    the shared line-by-line scan: an ``argparse.ArgumentParser(`` call with
    its default ``add_help=True`` behavior intact counts as help-aware even
    when no individual line matches one of the bash-oriented patterns below.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    if _is_python_script(path) and _ARGPARSE_CONSTRUCTOR.search(text):
        if not _ARGPARSE_HELP_DISABLED.search(text):
            return True

    for line in text.splitlines():
        for pat in _HELP_PATTERNS:
            if pat.search(line):
                return True
    return False


# ---------------------------------------------------------------------------
# Exempt list reader
# ---------------------------------------------------------------------------


def _load_exempt_list(exempt_list_path: Path) -> frozenset[str] | None:
    """Load the exempt list and return a frozenset of entry strings, or
    ``None`` when the file does not exist (the caller records that as a
    finding; see `_report_exempt_list_missing`).

    File format: one entry per line, ``#`` comments, blank lines ignored.
    Entries are matched by `_is_listed_exempt`: a bare basename
    (``wake-batch.sh``), a trailing path (``cm/open-doc.sh``) or a full label.
    """
    if not exempt_list_path.is_file():
        return None

    names: set[str] = set()
    for raw_line in exempt_list_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return frozenset(names)


# ---------------------------------------------------------------------------
# Per-directory scan
# ---------------------------------------------------------------------------


class _Offender(NamedTuple):
    """One missing-``--help`` finding, structured for both display and
    remedy-command derivation (see docs/coding-standards.md "Gates prescribe,
    not just diagnose").  Callers that need the label string read ``.label``."""

    label: str
    path: Path


def _is_listed_exempt(label: str, exempt_names: frozenset[str]) -> bool:
    """True when *label* equals an exempt-list entry or ends with ``/<entry>``."""
    return any(label == entry or label.endswith("/" + entry) for entry in exempt_names)


def _check_scripts(
    labelled: list[tuple[str, Path]],
    *,
    exempt_names: frozenset[str],
    verbose: bool,
) -> tuple[list[_Offender], int, int]:
    """Check each ``(label, path)`` for a help handler, skipping listed exemptions.

    Returns ``(offenders, exempt_count, checked_count)``.
    """
    offenders: list[_Offender] = []
    exempt_count = 0
    checked_count = 0
    for label, script in labelled:
        if _is_listed_exempt(label, exempt_names):
            if verbose:
                print(f"  exempt: {label}", flush=True)
            exempt_count += 1
            continue
        if verbose:
            print(f"  scanning: {label}", flush=True)
        checked_count += 1
        if not _has_help_handler(script):
            offenders.append(_Offender(label, script))
    return offenders, exempt_count, checked_count


# Directory names the tree walk never enters, beside every name starting
# with "." (VCS and tool state): byte-code caches and vendored packages.
_PRUNED_DIR_NAMES: frozenset[str] = frozenset({"__pycache__", "node_modules"})


def _is_executable_script(path: Path) -> bool:
    """True for a regular, non-symlink file with an execute bit set and a
    ``.sh``/``.py`` suffix or a ``#!`` first line."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if not path.stat().st_mode & 0o111:
            return False
        if path.suffix in (".sh", ".py"):
            return True
        with path.open("rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def _sourced_by_rule(relative: Path) -> bool:
    """True for a sourced library (a ``lib`` directory in the path) or a
    workflow plugin (``workflows/<type>/workflow.sh``): neither is run with
    a command line of its own, so neither owes a ``--help``."""
    parents = relative.parts[:-1]
    if "lib" in parents:
        return True
    return relative.name == "workflow.sh" and len(parents) >= 2 and parents[-2] == "workflows"


def _list_tree_scripts(root: Path) -> tuple[list[tuple[str, Path]], int]:
    """Walk *root* recursively; return ``(labelled, rule_exempt_count)``.

    ``labelled`` holds ``(label, path)`` for each executable script outside
    the rule exemptions (`_sourced_by_rule`), label being the path relative
    to *root*; ``rule_exempt_count`` counts the executable scripts the rule
    skipped. Hidden directories, ``__pycache__`` and ``node_modules`` are not
    entered. Sorted by label for deterministic narration.
    """
    labelled: list[tuple[str, Path]] = []
    rule_exempt = 0
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _PRUNED_DIR_NAMES)
        for filename in filenames:
            path = Path(current) / filename
            if not _is_executable_script(path):
                continue
            relative = path.relative_to(root)
            if _sourced_by_rule(relative):
                rule_exempt += 1
                continue
            labelled.append((relative.as_posix(), path))
    labelled.sort()
    return labelled, rule_exempt


def _default_tree_root(script_dir: Path) -> Path:
    """The tree this script belongs to: the repository root when it sits at
    ``team/scripts/``, the kanban root when it sits at ``scripts/`` in an
    install."""
    return script_dir.parent.parent if script_dir.parent.name == "team" else script_dir.parent


def _tree_scripts_dir(root: Path) -> Path | None:
    """The scripts directory of tree *root* (``team/scripts/`` or ``scripts/``),
    where the help-quality checks run, or ``None`` when it has neither."""
    for candidate in (root / "team" / "scripts", root / "scripts"):
        if candidate.is_dir():
            return candidate
    return None


def _tree_scan_set(root: Path) -> tuple[list[tuple[str, Path]], int, Path | None]:
    """Return ``(labelled, rule_exempt_count, scripts_dir)`` for a tree scan.

    The executable scripts under *root* (`_list_tree_scripts`), plus every
    ``*.sh``/``*.py`` directly in the tree's scripts directory and its
    ``cm/`` subdirectory whatever its mode, each listed once; *scripts_dir*
    is that directory (``None`` when the tree has none).
    """
    labelled, rule_exempt = _list_tree_scripts(root)
    scripts_dir = _tree_scripts_dir(root)
    if scripts_dir is not None:
        seen = {path for _label, path in labelled}
        for directory in (scripts_dir, scripts_dir / "cm"):
            if not directory.is_dir():
                continue
            for path in _list_scannable_scripts(directory):
                if path not in seen and not path.is_symlink():
                    labelled.append((path.relative_to(root).as_posix(), path))
                    seen.add(path)
        labelled.sort()
    return labelled, rule_exempt, scripts_dir


def _list_scannable_scripts(scripts_dir: Path) -> list[Path]:
    """Return the operator-facing scripts in *scripts_dir*, non-recursive.

    Covers both shell scripts (``*.sh``) and Python entry points (``*.py``)
    — the two file kinds an operator can invoke directly from this
    directory. Sorted by full path so scan order is deterministic and
    stable regardless of the mix of the two extensions.
    """
    return sorted(p for p in scripts_dir.iterdir() if p.is_file() and p.suffix in (".sh", ".py"))


def _scan_directory(
    scripts_dir: Path,
    *,
    label_prefix: str,
    exempt_names: frozenset[str],
    verbose: bool,
) -> tuple[list[_Offender], int, int]:
    """Scan *scripts_dir* non-recursively for scripts missing --help.

    Returns ``(offenders, exempt_count, checked_count)`` where each offender
    is an ``_Offender(label, path)`` pair.  ``label`` has the form
    ``<label_prefix><basename>`` (e.g. ``cm/release.sh`` or ``release.sh``);
    ``path`` is the resolved script path, needed to derive the remedy's fix
    command.

    *exempt_names* is matched against the label form (``cm/name.sh`` or bare
    ``name.sh``) by `_is_listed_exempt`, allowing directory-specific
    exemptions.
    """
    labelled = [(label_prefix + script.name, script) for script in _list_scannable_scripts(scripts_dir)]
    return _check_scripts(labelled, exempt_names=exempt_names, verbose=verbose)


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------

# A minimal, self-contained --help/-h case branch.  Appending this single
# line to the end of an offending shell script is a genuine, working fix —
# the handler actually prints a usage line and exits 0 on --help/-h — not
# merely a pattern-match satisfying the lint.  Every value is resolved at
# runtime by the script itself ($0, $1), so the fix command carries no
# site-specific placeholder: the same literal line is correct for any
# offending script.
_HELP_HANDLER_SNIPPET = 'case "${1:-}" in --help|-h) echo "Usage: $(basename "$0") [--help]"; ' "exit 0 ;; esac"

# The equivalent guard for a Python entry point.  Unlike the shell case
# branch, this cannot simply be appended to the end of the file: a Python
# script whose __main__ guard already calls sys.exit() would never reach
# code placed after it, so the fix command below (`_build_py_fix_command`)
# inserts this text as the first executable statement instead, after a
# leading shebang line when the file has one.
_HELP_GUARD_PY_SOURCE = (
    "import sys\n"
    "if any(a in ('--help', '-h') for a in sys.argv[1:]):\n"
    "    print('Usage: ' + sys.argv[0] + ' [--help]')\n"
    "    sys.exit(0)\n"
)


def _build_causes(offenders: list[_Offender]) -> list[str]:
    """Return one cause bullet per offender, naming the concrete script path."""
    return [f"{o.path}: does not implement a --help/-h handler (label: {o.label})" for o in offenders]


def _py_single_quoted_literal(value: str) -> str:
    """Return *value* as a single-line, single-quote-delimited Python string
    literal: backslashes, embedded single quotes, and real newlines are all
    escaped so the result is safe to splice into a one-line ``python3 -c``
    program with no further quoting concerns."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return "'" + escaped + "'"


def _build_py_fix_command(path: Path) -> str:
    """Return a verbatim-runnable, single-line command that inserts the
    --help/-h guard as the first executable statement of the Python entry
    point at *path*.

    Preserves a leading shebang line (moves the guard after it, not before)
    so direct execution (``./script.py``) keeps working. The rewrite is a
    single-line ``python3 -c`` program — every string it needs (the target
    path, the guard source) is embedded via `_py_single_quoted_literal` so
    the whole program uses single quotes exclusively; the outer shell
    argument is then double-quoted with no conflicting characters inside.
    """
    rewrite_source = (
        "import pathlib; "
        f"p = pathlib.Path({_py_single_quoted_literal(str(path))}); "
        "lines = p.read_text().splitlines(True); "
        "i = 1 if lines and lines[0].startswith('#!') else 0; "
        f"guard = {_py_single_quoted_literal(_HELP_GUARD_PY_SOURCE)}; "
        "p.write_text(''.join(lines[:i]) + guard + ''.join(lines[i:]))"
    )
    return f'python3 -c "{rewrite_source}"'


def _build_fix_commands(offenders: list[_Offender], lint_script: Path, recheck_args: list[str]) -> list[str]:
    """Return the numbered, verbatim-runnable fix commands for *offenders*.

    Each shell-script (``.sh``) offender gets one single-line command that
    appends the minimal working --help/-h handler (`_HELP_HANDLER_SNIPPET`)
    to the end of that script.  The single quotes around the snippet protect
    every `$` inside it from expansion by the shell running the *fix*
    command — the literal text lands in the target script unexpanded, to be
    evaluated only when that script itself later runs.

    Each Python (``.py``) offender gets a `_build_py_fix_command` rewrite
    instead, since appending after a `sys.exit()`-calling `__main__` guard
    would never execute (see that function's docstring).

    The final command re-runs this lint with the exact arguments used at
    detection time (*recheck_args*), proving the fix cleared the gate.
    """
    commands = [
        (
            _build_py_fix_command(o.path)
            if _is_python_script(o.path)
            else f"echo '{_HELP_HANDLER_SNIPPET}' >> \"{o.path}\""
        )
        for o in offenders
    ]
    args_str = " ".join(recheck_args)
    suffix = f" {args_str}" if args_str else ""
    commands.append(f'python3 "{lint_script}"{suffix}')
    return commands


def _format_remedy_block(offenders: list[_Offender], lint_script: Path, recheck_args: list[str]) -> str:
    """Assemble the full three-part remedy block: cause(s) with concrete
    location, a numbered fix block of verbatim-runnable commands ending with
    the re-lint check, then the one-line rule the gate enforces.
    """
    causes = _build_causes(offenders)
    commands = _build_fix_commands(offenders, lint_script, recheck_args)

    lines = [
        f"lint_help_presence: {len(offenders)} script(s) missing --help",
        "",
        "Cause(s):",
    ]
    for cause in causes:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append("To fix:")
    for i, command in enumerate(commands, start=1):
        lines.append(f"  {i}. {command}")
    lines.append("")
    lines.append(
        "Rule: every executable script must implement a --help/-h handler so "
        "operators can discover its usage without reading the source; sourced "
        "libraries (lib/) and workflow plugins are exempt by rule, and scripts "
        "that are process-internal (never invoked directly by an operator) "
        "belong on the exempt list instead."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Help-quality checks (quality class: narrated, never fatal)
# ---------------------------------------------------------------------------

# A long-flag token: ``--name`` not preceded by a word character or dash.
_LONG_FLAG_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]*"

# A ``case`` arm pattern list: ``  --a|-b|--c=*)`` (optionally opened by
# ``(``). The captured group is the ``|``-separated alternative list; each
# alternative is checked separately in `_case_arm_flags`.
_CASE_ARM_LINE: re.Pattern[str] = re.compile(r"""^\s*\(?\s*([^\s()#][^()]*?)\s*\)""")

# One alternative of a case arm that names a long flag, optionally quoted,
# optionally followed by an ``=*`` / ``=`` value glob.
_CASE_ARM_FLAG: re.Pattern[str] = re.compile(r"""^["']?--(""" + _LONG_FLAG_NAME + r""")(?:=\*?)?["']?$""")

# ``argparse_has name`` / ``argparse_has "name"`` (operator_args/argparse.sh).
_ARGPARSE_HAS_FLAG: re.Pattern[str] = re.compile(r"""argparse_has\s+["']?(""" + _LONG_FLAG_NAME + r""")["']?""")

# ``argparse_parse --value-flags "a b c"`` — bare names, each one a ``--name``.
_VALUE_FLAGS_LIST: re.Pattern[str] = re.compile(r"""--value-flags\s+(?:"([^"]*)"|'([^']*)')""")

# ``OPERATOR_VALID_FLAGS=(a b c)`` — bare names, each one a ``--name``.
_OPERATOR_VALID_FLAGS: re.Pattern[str] = re.compile(r"""OPERATOR_VALID_FLAGS=\(([^)]*)\)""")

# A script whose help is rendered from OPERATOR_VALID_FLAGS names every entry.
_RENDER_HELP_FOR_FLAGS: re.Pattern[str] = re.compile(r"""\boperator_args_render_help_for_flags\b""")

# Heredoc opener: ``<<EOF``, ``<<-EOF``, ``<<'EOF'``, ``<< "EOF"``. ``<<<``
# (a here-string) is excluded by the negative look-ahead.
_HEREDOC_OPEN: re.Pattern[str] = re.compile(r"""<<(?!<)-?\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?""")

# Function definition whose name marks it as a usage/help printer.
_HELP_FUNCTION_OPEN: re.Pattern[str] = re.compile(
    r"""^\s*(?:function\s+)?[A-Za-z0-9_:-]*(?:usage|help|USAGE|HELP)""" r"""[A-Za-z0-9_:-]*\s*\(\)\s*\{?\s*$"""
)

# Assignment to a usage/help text variable (``_USAGE_TEXT="$(cat <<EOF``,
# ``USAGE="..."``, ``declare -A OPERATOR_FLAG_HELP=(``).
_HELP_VARIABLE_OPEN: re.Pattern[str] = re.compile(
    r"""^\s*(?:(?:declare|local|readonly)\s+(?:-\w+\s+)*)?""" r"""[A-Za-z0-9_]*(?:USAGE|HELP)[A-Za-z0-9_]*="""
)

# Check (b): a fixed ``sed -n 'A,Bp'`` range printed straight from the
# script's own file. Loose on quoting and spacing; the file operand is
# ``$0``, ``${0}`` or ``${BASH_SOURCE[0]}`` (quoted or not).
_FIXED_SED_HELP_RANGE: re.Pattern[str] = re.compile(
    r"""sed\s+-n\s+["']?\d+\s*,\s*\d+\s*p["']?\s+""" r"""["']?\$(?:0|\{0\}|\{BASH_SOURCE\[0\]\})["']?"""
)


class _QualityFinding(NamedTuple):
    """One help-quality finding: which check, which script, what. ``path``
    is the script itself and ``line`` the offending line, when the check
    has one (the fixed sed range); both feed the recorded finding."""

    check: str
    label: str
    detail: str
    path: Path | None = None
    line: int | None = None


def _list_quality_scripts(root: Path) -> list[Path]:
    """Return every ``*.sh`` under *root*, recursively, outside ``lib/`` dirs.

    ``lib/`` directories hold sourced helpers rather than entry points, so
    they are skipped wherever they sit (``lib/``, ``dashboard/lib/``, ...).
    Sorted for deterministic narration.
    """
    scripts: list[Path] = []
    for path in sorted(root.rglob("*.sh")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts[:-1]
        if "lib" in relative_parts:
            continue
        scripts.append(path)
    return scripts


def _header_block(lines: list[str]) -> list[str]:
    """Return the header comment block: line 2 (after a shebang) up to the
    first non-comment line — the same block ``header_help_print`` prints."""
    block: list[str] = []
    for index, line in enumerate(lines):
        if index == 0 and line.startswith("#!"):
            continue
        if not line.startswith("#"):
            break
        block.append(line)
    return block


def _heredoc_bodies(lines: list[str]) -> list[str]:
    """Return the body lines of every heredoc in *lines*."""
    bodies: list[str] = []
    index = 0
    while index < len(lines):
        match = _HEREDOC_OPEN.search(lines[index])
        index += 1
        if not match:
            continue
        terminator = match.group(1)
        while index < len(lines) and lines[index].strip() != terminator:
            bodies.append(lines[index])
            index += 1
        index += 1  # skip the terminator line
    return bodies


def _help_function_bodies(lines: list[str]) -> list[str]:
    """Return the lines of every function whose name says usage/help, from
    its opening line to the first ``}`` at or left of its indentation."""
    bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not _HELP_FUNCTION_OPEN.match(line):
            continue
        indent = len(line) - len(line.lstrip())
        while index < len(lines):
            body_line = lines[index]
            index += 1
            stripped = body_line.strip()
            if stripped.startswith("}") and (len(body_line) - len(body_line.lstrip()) <= indent):
                break
            bodies.append(body_line)
    return bodies


def _help_variable_bodies(lines: list[str]) -> list[str]:
    """Return the lines of every usage/help variable assignment, from the
    assignment line up to the first line that closes it (a line ending in
    ``)``, ``)"`` or a closing quote) or the next blank line."""
    bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not _HELP_VARIABLE_OPEN.match(line):
            continue
        bodies.append(line)
        while index < len(lines):
            body_line = lines[index]
            stripped = body_line.strip()
            if not stripped:
                break
            bodies.append(body_line)
            index += 1
            if stripped in (")", ')"', ")'", '"', "'") or stripped.endswith(')"'):
                break
    return bodies


def _case_arm_flags(line: str) -> set[str]:
    """Return the long-flag names a ``case`` arm line lists, if it is one."""
    match = _CASE_ARM_LINE.match(line)
    if not match:
        return set()
    names: set[str] = set()
    for alternative in match.group(1).split("|"):
        flag = _CASE_ARM_FLAG.match(alternative.strip())
        if flag:
            names.add(flag.group(1))
    return names


def _bare_flag_names(words: str) -> set[str]:
    """Split a whitespace list of bare flag names; drop one-letter shorts."""
    return {
        word.strip("\"'")
        for word in words.split()
        if len(word.strip("\"'")) > 1 and re.fullmatch(_LONG_FLAG_NAME, word.strip("\"'"))
    }


def _parsed_flags(lines: list[str]) -> tuple[set[str], set[str]]:
    """Return ``(parsed, operator_valid)``: every long flag the script
    parses, and the subset declared in ``OPERATOR_VALID_FLAGS``."""
    parsed: set[str] = set()
    operator_valid: set[str] = set()
    for line in lines:
        code = line.lstrip()
        if code.startswith("#"):
            continue
        parsed |= _case_arm_flags(line)
        for match in _ARGPARSE_HAS_FLAG.finditer(line):
            if len(match.group(1)) > 1:
                parsed.add(match.group(1))
        for match in _VALUE_FLAGS_LIST.finditer(line):
            parsed |= _bare_flag_names(match.group(1) or match.group(2) or "")
        for match in _OPERATOR_VALID_FLAGS.finditer(line):
            names = _bare_flag_names(match.group(1))
            parsed |= names
            operator_valid |= names
    return parsed, operator_valid


def _undocumented_flags(path: Path) -> list[str]:
    """Return the ``--flag`` names *path* parses but its help never names."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    parsed, operator_valid = _parsed_flags(lines)
    if not parsed:
        return []

    help_source = "\n".join(
        _header_block(lines) + _heredoc_bodies(lines) + _help_function_bodies(lines) + _help_variable_bodies(lines)
    )
    rendered_from_valid_flags = bool(_RENDER_HELP_FOR_FLAGS.search(text))

    missing: list[str] = []
    for name in sorted(parsed):
        # --help is the flag the reader just used; whether a script answers
        # it at all is the presence check's job, not this one's.
        if name == "help":
            continue
        if rendered_from_valid_flags and name in operator_valid:
            continue
        named = re.search(
            r"(?<![A-Za-z0-9_-])--" + re.escape(name) + r"(?![A-Za-z0-9_-])",
            help_source,
        )
        if not named:
            missing.append(f"--{name}")
    return missing


def _fixed_sed_ranges(path: Path) -> list[tuple[int, str]]:
    """Return ``(line_number, line)`` for each fixed sed help range in *path*."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        if _FIXED_SED_HELP_RANGE.search(line):
            hits.append((number, line.strip()))
    return hits


def _scan_help_quality(root: Path, *, verbose: bool) -> tuple[list[_QualityFinding], int]:
    """Run both help-quality checks over the bash scripts under *root*.

    Returns ``(findings, checked_count)``. Check (a) applies only to scripts
    with a help handler — a script without one is the presence check's
    finding, not this one's; check (b) applies to every script scanned.
    """
    findings: list[_QualityFinding] = []
    scripts = _list_quality_scripts(root)
    for script in scripts:
        label = str(script.relative_to(root))
        if verbose:
            print(f"  help-quality: {label}", flush=True)
        if _has_help_handler(script):
            missing = _undocumented_flags(script)
            if missing:
                findings.append(
                    _QualityFinding(
                        "undocumented-flag",
                        label,
                        f"parses {', '.join(missing)} but its --help never "
                        "names "
                        + ("it" if len(missing) == 1 else "them")
                        + " (add a one-line description to the header "
                        "block, help heredoc or OPERATOR_FLAG_HELP; an "
                        "operator-only or internal flag goes under its own "
                        "heading)",
                        script,
                    )
                )
        for number, line in _fixed_sed_ranges(script):
            findings.append(
                _QualityFinding(
                    "fixed-sed-help-range",
                    f"{label}:{number}",
                    f"prints help through a fixed range ({line}); source "
                    "lib/header_help.sh and call header_help_print "
                    '"${BASH_SOURCE[0]}" instead',
                    script,
                    number,
                )
            )
    return findings, len(scripts)


def _narrate_help_quality(findings: list[_QualityFinding], checked: int, root: Path) -> None:
    """Print the help-quality findings, one line each, then a count line.

    Quality class: the caller records each one as a finding too; none
    changes the exit status.
    """
    for finding in findings:
        print(
            f"lint_help_presence: quality [{finding.check}] " f"{finding.label}: {finding.detail}",
            flush=True,
        )
    print(
        f"lint_help_presence: help-quality — {checked} bash script(s) under "
        f"{root} checked for undocumented flags and fixed sed help ranges, "
        f"{len(findings)} finding(s) (quality class; exit status unaffected).",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_help_presence.py",
        description=(
            "Check that every executable script in the tree (a file with an "
            "execute bit and a .sh/.py suffix or a #! line, found recursively) "
            "implements a --help or -h flag that prints usage text.  Exempt by "
            "rule, because they are sourced and have no command line of their "
            "own: sourced libraries (any path with a lib/ directory) and "
            "workflow plugins (workflows/<type>/workflow.sh).  Scripts listed in "
            "the exempt list are skipped too.  Quality class: each script "
            "missing help is a finding, and the lint exits 0 whatever it finds "
            "(2 when the scan directory does not exist)."
        ),
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--root",
        metavar="PATH",
        help=(
            "Tree to scan recursively.  Default: the repository root (this "
            "script at team/scripts/) or the kanban root (this script at "
            "scripts/ in an install).  The help-quality checks run over its "
            "team/scripts/ or scripts/ directory."
        ),
    )
    scope.add_argument(
        "--scripts-dir",
        metavar="PATH",
        help=(
            "Single directory to scan instead of the tree, non-recursive; every "
            "*.sh and *.py file in it is checked whatever its mode.  Also the "
            "root of the recursive help-quality scan."
        ),
    )
    parser.add_argument(
        "--exempt-list",
        metavar="PATH",
        help=(
            "Path to the exempt-list data file (one entry per line; an entry "
            "matches a script whose label equals it or ends with '/' plus it, "
            "so a basename or a trailing path such as cm/open-doc.sh works; "
            "# comments allowed).  Default: help_presence_exempt.txt beside "
            "this script."
        ),
    )
    parser.add_argument(
        "--findings-file",
        metavar="PATH",
        help=(
            "Write the findings as a JSON array to PATH (parent directories "
            "created; written even when empty).  Findings are printed either way."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each file name as it is scanned.",
    )
    return parser.parse_args(argv)


def _recheck_command(lint_script: Path, recheck_args: list[str]) -> str:
    """This lint re-run with *recheck_args*, as a shell command."""
    suffix = "".join(f" {a}" if a.startswith("--") else f' "{a}"' for a in recheck_args)
    return f'python3 "{lint_script}"{suffix}'


def _report_exempt_list_missing(
    exempt_list_path: Path, *, explicit: bool, lint_script: Path, findings_file: str | None
) -> int:
    """Record the unreadable exempt list as the run's one finding and return 0.

    Without the list the presence check would flag every exempt script, so
    it does not run. The remedy re-runs on the default list when an
    explicit --exempt-list was given, and restores the tracked default list
    from git otherwise.
    """
    repo_root = _TEAM_DIR.parent
    if explicit:
        remedy = _recheck_command(lint_script, [])
    else:
        rel = display_path(exempt_list_path, repo_root)
        remedy = f'git -C "{repo_root}" checkout -- {rel} && {_recheck_command(lint_script, [])}'
    finding = quality_finding(
        _LINT_NAME,
        f"exempt list {exempt_list_path} not found, so the --help presence check did not run",
        remedy,
        file=display_path(exempt_list_path, repo_root),
        kind="exempt-list-missing",
    )
    write_findings_file(findings_file, [finding], lint="lint_help_presence")
    print(
        f"ERROR: exempt list not found: {exempt_list_path}\n" "Use --exempt-list to specify an alternative path.",
        file=sys.stderr,
    )
    # CENSUS-ANCHOR: lint-help-presence-exempt-list-missing-01
    # CONVERT: quality-class lint precondition -- a missing exempt list
    # means this run cannot classify scripts correctly, not that the
    # tree's --help coverage regressed; recorded as a finding, returns 0.
    emit_error_contract("lint.help-presence-exempt-list-missing", finding["cause"], remedy)
    return 0


def _findings(
    offenders: list[_Offender],
    quality: list[_QualityFinding],
    *,
    lint_script: Path,
    recheck_args: list[str],
) -> list[dict]:
    """One quality finding per script missing ``--help`` (``kind``
    ``missing-help``, remedied by its own fix command from the remedy block)
    and per help-quality finding (``kind`` is the check's name)."""
    repo_root = _TEAM_DIR.parent
    recheck = _recheck_command(lint_script, recheck_args)
    fixes = _build_fix_commands(offenders, lint_script, recheck_args)[:-1]
    findings = [
        quality_finding(
            _LINT_NAME,
            f"{display_path(o.path, repo_root)} does not implement a --help/-h handler " f"(label: {o.label})",
            f"{fix} && {recheck}",
            file=display_path(o.path, repo_root),
            kind="missing-help",
        )
        for o, fix in zip(offenders, fixes)
    ]
    for q in quality:
        path = display_path(q.path, repo_root) if q.path else q.label
        if q.check == "fixed-sed-help-range":
            edit = (
                f"replace the fixed sed range on line {q.line} with "
                'header_help_print "${BASH_SOURCE[0]}" from lib/header_help.sh'
            )
        else:
            edit = (
                "name each flag listed in this finding in the script's help source "
                "(header block, help heredoc or OPERATOR_FLAG_HELP)"
            )
        findings.append(
            quality_finding(
                _LINT_NAME,
                f"{path}: {q.detail}",
                f"in {q.path or q.label}, {edit}, then re-check: {recheck}",
                file=path,
                line=q.line,
                kind=q.check,
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    """Run the help-presence lint and its help-quality checks.

    Exit codes: 0 whatever was found (each script missing ``--help``, each
    help-quality finding and a missing exempt list is a quality finding,
    printed and written to --findings-file); 2 usage error, a scan directory
    that does not exist.
    """
    args = _parse_args(argv)

    # This script's own directory: team/scripts/ in a dev tree, scripts/ in
    # an install. The default tree root and exempt list are found from it.
    script_dir = Path(__file__).resolve().parent

    # Resolve the exempt list path.
    if args.exempt_list:
        exempt_list_path = Path(args.exempt_list).resolve()
    else:
        exempt_list_path = script_dir / "help_presence_exempt.txt"

    lint_script = Path(__file__).resolve()
    exempt_names = _load_exempt_list(exempt_list_path)
    if exempt_names is None:
        return _report_exempt_list_missing(
            exempt_list_path,
            explicit=bool(args.exempt_list),
            lint_script=lint_script,
            findings_file=args.findings_file,
        )

    # ------------------------------------------------------------------
    # Determine what to scan.
    #
    # --scripts-dir supplied: one directory, non-recursive.
    # Otherwise: every executable script under the tree root, recursively.
    # ------------------------------------------------------------------
    if args.scripts_dir:
        scan_dir = Path(args.scripts_dir).resolve()
        if not scan_dir.is_dir():
            print(
                f"ERROR: scripts directory not found: {scan_dir}\n" "Use --scripts-dir to specify an alternative path.",
                file=sys.stderr,
            )
            return 2
        print(
            f"lint_help_presence: scanning {len(_list_scannable_scripts(scan_dir))} scripts in {scan_dir}",
            flush=True,
        )
        offenders, exempt_count, checked_count = _scan_directory(
            scan_dir,
            label_prefix="",
            exempt_names=exempt_names,
            verbose=args.verbose,
        )
        quality_root: Path | None = scan_dir
    else:
        scan_dir = Path(args.root).resolve() if args.root else _default_tree_root(script_dir)
        if not scan_dir.is_dir():
            print(
                f"ERROR: tree root not found: {scan_dir}\n" "Use --root to specify an alternative path.",
                file=sys.stderr,
            )
            return 2
        labelled, rule_exempt, quality_root = _tree_scan_set(scan_dir)
        print(
            f"lint_help_presence: scanning {len(labelled)} scripts under {scan_dir} "
            f"(recursive; {rule_exempt} sourced libraries and workflow plugins exempt by rule)",
            flush=True,
        )
        offenders, exempt_count, checked_count = _check_scripts(
            labelled, exempt_names=exempt_names, verbose=args.verbose
        )

    # Help-quality checks (quality class): narrated whatever the presence
    # check found, and never part of the exit status below.
    quality_findings: list[_QualityFinding] = []
    if quality_root is not None:
        quality_findings, quality_checked = _scan_help_quality(quality_root, verbose=args.verbose)
        _narrate_help_quality(quality_findings, quality_checked, quality_root)

    # Reconstruct the exact re-lint invocation so the recheck command in
    # the remedy block reproduces this run's scan surface precisely.
    recheck_args: list[str] = []
    if args.scripts_dir:
        recheck_args += ["--scripts-dir", str(scan_dir)]
    elif args.root:
        recheck_args += ["--root", str(scan_dir)]
    recheck_args += ["--exempt-list", str(exempt_list_path)]
    findings = _findings(offenders, quality_findings, lint_script=lint_script, recheck_args=recheck_args)
    write_findings_file(args.findings_file, findings, lint="lint_help_presence")

    if offenders:
        print(
            "\n" + _format_remedy_block(offenders, lint_script, recheck_args),
            flush=True,
        )
        # CENSUS-ANCHOR: lint-help-presence-01
        # CONVERT: a script without --help is a discoverability gap with
        # zero runtime-failure risk -- recorded as findings; returns 0.
        emit_error_contract(
            "lint.help-presence",
            f"{len(offenders)} executable script(s) do not implement a --help/-h "
            "handler; each is listed above with its fix.",
            findings[0]["remedy"],
        )
        return 0

    print(
        f"lint_help_presence: OK — {checked_count} script(s) checked, " f"{exempt_count} exempt, 0 offenders.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

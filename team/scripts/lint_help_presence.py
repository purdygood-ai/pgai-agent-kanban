#!/usr/bin/env python3
"""
lint_help_presence.py
=====================
Lint gate: every operator-facing script under team/scripts/ — shell script
or Python entry point — must implement a ``--help`` or ``-h`` flag that
prints usage text.

Scripts that are exempt (dashboard-internal pane scripts, process-internal
helpers invoked by other scripts rather than directly by operators) are
listed in an exempt-list data file read at runtime.

Scope
-----
The scanner operates on two directories by default (non-recursive in each):

  1. ``team/scripts/`` — top-level operator-facing scripts (``*.sh``,
     ``*.py``).
  2. ``team/scripts/cm/`` — CM subsystem operator entry points
     (``cm/release.sh``, ``cm/open-rc.sh``, ``cm/finalize.sh``,
     ``cm/promote_bundled_items.py``, etc.).

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

  6. (``.py`` files only) An ``argparse.ArgumentParser(`` constructor call
     without a companion ``add_help=False`` — argparse adds a working
     ``-h``/``--help`` flag automatically unless that default is turned
     off, so a script built this way gets a genuine help handler for free.

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

Help-quality checks (quality class — narrated, never change the exit code)
--------------------------------------------------------------------------
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
count. Neither check ever makes the lint exit non-zero.

Exit codes
----------
  0   No --help presence findings (help-quality findings, if any, are
      narrated and do not change this).
  1   One or more scripts are missing --help (see stdout for the list).
  2   Usage error or internal failure (missing directory, missing exempt list).

Usage
-----
  python3 scripts/lint_help_presence.py [--scripts-dir PATH]
                                        [--exempt-list PATH]
                                        [--verbose]

  --scripts-dir PATH   Single directory to scan (overrides the default two-
                       directory scan of team/scripts/ and team/scripts/cm/);
                       also the root of the recursive help-quality scan.
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
from typing import NamedTuple

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

# Pattern 6 (``.py`` only): a Python entry point built on
# ``argparse.ArgumentParser(`` gets a working ``-h``/``--help`` flag for
# free, unless the constructor call disables it with ``add_help=False``.
_ARGPARSE_CONSTRUCTOR: re.Pattern[str] = re.compile(
    r"""argparse\.ArgumentParser\("""
)
_ARGPARSE_HELP_DISABLED: re.Pattern[str] = re.compile(
    r"""add_help\s*=\s*False"""
)


def _has_help_handler(path: Path) -> bool:
    """Return True if *path* contains any help-presence detection pattern.

    ``.py`` files get one extra whole-file check (pattern 6, above) ahead of
    the shared line-by-line scan: an ``argparse.ArgumentParser(`` call with
    its default ``add_help=True`` behavior intact counts as help-aware even
    when no individual line matches one of the bash-oriented patterns below.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    if path.suffix == ".py" and _ARGPARSE_CONSTRUCTOR.search(text):
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
        # CONVERT: quality-class lint precondition -- a missing exempt list
        # means this run cannot classify scripts correctly, not that the
        # tree's --help coverage regressed.
        # CENSUS-ANCHOR: lint-help-presence-exempt-list-missing-01
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


class _Offender(NamedTuple):
    """One missing-``--help`` finding, structured for both display and
    remedy-command derivation (see docs/coding-standards.md "Gates prescribe,
    not just diagnose").  Callers that need the label string read ``.label``."""

    label: str
    path: Path


def _list_scannable_scripts(scripts_dir: Path) -> list[Path]:
    """Return the operator-facing scripts in *scripts_dir*, non-recursive.

    Covers both shell scripts (``*.sh``) and Python entry points (``*.py``)
    — the two file kinds an operator can invoke directly from this
    directory. Sorted by full path so scan order is deterministic and
    stable regardless of the mix of the two extensions.
    """
    return sorted(
        p
        for p in scripts_dir.iterdir()
        if p.is_file() and p.suffix in (".sh", ".py")
    )


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
    ``name.sh``), allowing directory-specific exemptions.
    """
    scripts: list[Path] = _list_scannable_scripts(scripts_dir)

    offenders: list[_Offender] = []
    exempt_count = 0
    checked_count = 0

    for script in scripts:
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
            offenders.append(_Offender(label, script))

    return offenders, exempt_count, checked_count


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
_HELP_HANDLER_SNIPPET = (
    'case "${1:-}" in --help|-h) echo "Usage: $(basename "$0") [--help]"; '
    'exit 0 ;; esac'
)

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
    return [
        f"{o.path}: does not implement a --help/-h handler (label: {o.label})"
        for o in offenders
    ]


def _py_single_quoted_literal(value: str) -> str:
    """Return *value* as a single-line, single-quote-delimited Python string
    literal: backslashes, embedded single quotes, and real newlines are all
    escaped so the result is safe to splice into a one-line ``python3 -c``
    program with no further quoting concerns."""
    escaped = (
        value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    )
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


def _build_fix_commands(
    offenders: list[_Offender], lint_script: Path, recheck_args: list[str]
) -> list[str]:
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
        f"echo '{_HELP_HANDLER_SNIPPET}' >> \"{o.path}\""
        if o.path.suffix != ".py"
        else _build_py_fix_command(o.path)
        for o in offenders
    ]
    args_str = " ".join(recheck_args)
    suffix = f" {args_str}" if args_str else ""
    commands.append(f'python3 "{lint_script}"{suffix}')
    return commands


def _format_remedy_block(
    offenders: list[_Offender], lint_script: Path, recheck_args: list[str]
) -> str:
    """Assemble the full three-part remedy block: cause(s) with concrete
    location, a numbered fix block of verbatim-runnable commands ending with
    the re-lint check, then the one-line rule the gate enforces.
    """
    causes = _build_causes(offenders)
    commands = _build_fix_commands(offenders, lint_script, recheck_args)

    lines = [
        f"lint_help_presence: FAIL — {len(offenders)} script(s) missing --help",
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
        "Rule: every operator-facing script under team/scripts/ must implement "
        "a --help/-h handler so operators can discover its usage without "
        "reading the source; scripts that are process-internal (never invoked "
        "directly by an operator) belong on the exempt list instead."
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
_CASE_ARM_FLAG: re.Pattern[str] = re.compile(
    r"""^["']?--(""" + _LONG_FLAG_NAME + r""")(?:=\*?)?["']?$"""
)

# ``argparse_has name`` / ``argparse_has "name"`` (operator_args/argparse.sh).
_ARGPARSE_HAS_FLAG: re.Pattern[str] = re.compile(
    r"""argparse_has\s+["']?(""" + _LONG_FLAG_NAME + r""")["']?"""
)

# ``argparse_parse --value-flags "a b c"`` — bare names, each one a ``--name``.
_VALUE_FLAGS_LIST: re.Pattern[str] = re.compile(
    r"""--value-flags\s+(?:"([^"]*)"|'([^']*)')"""
)

# ``OPERATOR_VALID_FLAGS=(a b c)`` — bare names, each one a ``--name``.
_OPERATOR_VALID_FLAGS: re.Pattern[str] = re.compile(
    r"""OPERATOR_VALID_FLAGS=\(([^)]*)\)"""
)

# A script whose help is rendered from OPERATOR_VALID_FLAGS names every entry.
_RENDER_HELP_FOR_FLAGS: re.Pattern[str] = re.compile(
    r"""\boperator_args_render_help_for_flags\b"""
)

# Heredoc opener: ``<<EOF``, ``<<-EOF``, ``<<'EOF'``, ``<< "EOF"``. ``<<<``
# (a here-string) is excluded by the negative look-ahead.
_HEREDOC_OPEN: re.Pattern[str] = re.compile(
    r"""<<(?!<)-?\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?"""
)

# Function definition whose name marks it as a usage/help printer.
_HELP_FUNCTION_OPEN: re.Pattern[str] = re.compile(
    r"""^\s*(?:function\s+)?[A-Za-z0-9_:-]*(?:usage|help|USAGE|HELP)"""
    r"""[A-Za-z0-9_:-]*\s*\(\)\s*\{?\s*$"""
)

# Assignment to a usage/help text variable (``_USAGE_TEXT="$(cat <<EOF``,
# ``USAGE="..."``, ``declare -A OPERATOR_FLAG_HELP=(``).
_HELP_VARIABLE_OPEN: re.Pattern[str] = re.compile(
    r"""^\s*(?:(?:declare|local|readonly)\s+(?:-\w+\s+)*)?"""
    r"""[A-Za-z0-9_]*(?:USAGE|HELP)[A-Za-z0-9_]*="""
)

# Check (b): a fixed ``sed -n 'A,Bp'`` range printed straight from the
# script's own file. Loose on quoting and spacing; the file operand is
# ``$0``, ``${0}`` or ``${BASH_SOURCE[0]}`` (quoted or not).
_FIXED_SED_HELP_RANGE: re.Pattern[str] = re.compile(
    r"""sed\s+-n\s+["']?\d+\s*,\s*\d+\s*p["']?\s+"""
    r"""["']?\$(?:0|\{0\}|\{BASH_SOURCE\[0\]\})["']?"""
)


class _QualityFinding(NamedTuple):
    """One narrated help-quality finding: which check, which script, what."""

    check: str
    label: str
    detail: str


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
            if stripped.startswith("}") and (
                len(body_line) - len(body_line.lstrip()) <= indent
            ):
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
        if len(word.strip("\"'")) > 1
        and re.fullmatch(_LONG_FLAG_NAME, word.strip("\"'"))
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
        _header_block(lines)
        + _heredoc_bodies(lines)
        + _help_function_bodies(lines)
        + _help_variable_bodies(lines)
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


def _scan_help_quality(
    root: Path, *, verbose: bool
) -> tuple[list[_QualityFinding], int]:
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
                    )
                )
        for number, line in _fixed_sed_ranges(script):
            findings.append(
                _QualityFinding(
                    "fixed-sed-help-range",
                    f"{label}:{number}",
                    f"prints help through a fixed range ({line}); source "
                    "lib/header_help.sh and call header_help_print "
                    "\"${BASH_SOURCE[0]}\" instead",
                )
            )
    return findings, len(scripts)


def _narrate_help_quality(
    findings: list[_QualityFinding], checked: int, root: Path
) -> None:
    """Print the help-quality findings, one line each, then a count line.

    Quality class: this only narrates. The caller's exit status is decided
    by the --help presence check alone.
    """
    for finding in findings:
        print(
            f"lint_help_presence: quality [{finding.check}] "
            f"{finding.label}: {finding.detail}",
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
    """Run the help-presence lint and its help-quality checks.

    Exit codes: 0 no script is missing ``--help`` (help-quality findings are
    narrated and do not change this); 1 one or more scripts are missing
    ``--help`` (listed); 2 usage error or internal failure, such as a missing
    directory or exempt list.
    """
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

        scripts: list[Path] = _list_scannable_scripts(single_dir)
        print(
            f"lint_help_presence: scanning {len(scripts)} scripts in {single_dir}",
            flush=True,
        )

        offenders: list[_Offender] = []
        exempt_count = 0
        checked_count = 0

        for script in scripts:
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
                offenders.append(_Offender(name, script))

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
        top_scripts = _list_scannable_scripts(top_dir)
        cm_scripts: list[Path] = []
        if cm_dir.is_dir():
            cm_scripts = _list_scannable_scripts(cm_dir)

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

    # Help-quality checks (quality class): narrated whatever the presence
    # check found, and never part of the exit status below.
    quality_root = single_dir if args.scripts_dir else script_dir
    quality_findings, quality_checked = _scan_help_quality(
        quality_root, verbose=args.verbose
    )
    _narrate_help_quality(quality_findings, quality_checked, quality_root)

    if offenders:
        # Reconstruct the exact re-lint invocation so the recheck command in
        # the remedy block reproduces this run's scan surface precisely.
        recheck_args: list[str] = []
        if args.scripts_dir:
            recheck_args += ["--scripts-dir", str(single_dir)]
        recheck_args += ["--exempt-list", str(exempt_list_path)]

        lint_script = Path(__file__).resolve()
        print(
            "\n" + _format_remedy_block(offenders, lint_script, recheck_args),
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

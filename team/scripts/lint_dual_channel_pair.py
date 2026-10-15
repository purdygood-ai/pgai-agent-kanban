#!/usr/bin/env python3
"""
lint_dual_channel_pair.py
==========================
Quality-class lint: scans every call site of the shared dual-channel
error-contract emitter and flags any call that does not supply all three
required fields -- error, message, remedy -- with real, non-empty content.

A fail-loud site (a HALT reason, a gate refusal, a hook-failure reason, a
blocker narration, a release-state event) is only useful to the next reader
when it speaks both channels: a stable machine key a tool can grep, and a
plain-language message plus a runnable remedy a stranger can act on without
the internal ledger open. The emitter primitive itself
(``team/scripts/lib/error_contract.sh`` and
``pgai_agent_kanban.lib.error_contract``) already refuses a blank field at
run time -- but that refusal only fires the moment the failure path actually
executes, which can be rare in production. This lint is the static,
always-run counterpart: it inspects every call site in the source tree so a
missing or blank field is visible before the failure path ever runs.

WHAT IS CHECKED
---------------
Two source surfaces are scanned for call sites of the four emitter
functions:

  Bash    (pgai_format_error_contract, pgai_emit_error_contract)
          -- team/scripts/, team/scripts/lib/, team/scripts/cm/,
             team/scripts/dashboard/lib/ (each scanned non-recursively).
  Python  (format_error_contract, emit_error_contract)
          -- team/pgai_agent_kanban/ (scanned recursively), excluding test
             and fixture directories.

For each call site, the three required fields (error, message, remedy) are
checked in the fixed field order the house shape defines. A field is a
finding when it is:

  - missing entirely (fewer than three arguments supplied), or
  - supplied as a literal empty (or whitespace-only) string.

A field passed as a variable, command substitution, or any other non-literal
expression is not evaluated for content -- this lint is a structural,
static check, not a runtime one; the emitter's own validation already
refuses blank content at the moment the call actually executes. Call sites
whose argument shape this lint cannot statically resolve (a Python call
using ``*args``/``**kwargs`` spreading, a bash statement that could not be
tokenized as shell words) are skipped rather than guessed at -- silence over
a false accusation.

QUALITY-CLASS POSTURE
----------------------
This lint NEVER exits non-zero. It is deliberately built to the posture the
release's failure-handling policy establishes for its whole class: lint
findings are noted and filed, never a reason to stop a build or a release.
Findings print in full to stdout, in the same three-part cause/fix/rule
shape every deterministic gate in this codebase uses, and are also emitted
as a machine-readable JSON array (to stdout, and optionally to a file via
``--findings-file``) so a caller -- human or automated -- can act on them
without treating a non-zero exit as a gate failure. Wiring this lint's
output into a repo-wide findings pipeline consumed by a verification report
is a follow-on integration point this script's output is shaped to support,
not something this script performs itself.

Exit codes
----------
  0   Always. See "Quality-class posture" above.

Usage
-----
  python3 team/scripts/lint_dual_channel_pair.py [--bash-dirs DIR [DIR ...]]
                                                  [--python-dir DIR]
                                                  [--findings-file PATH]
                                                  [--verbose]

  --bash-dirs DIR [DIR ...]  Directories to scan (non-recursively) for
                              ``*.sh`` call sites. Default: the four
                              production bash surfaces listed above,
                              resolved relative to this script's location.
  --python-dir DIR           Root directory to scan (recursively) for
                              ``*.py`` call sites. Default:
                              team/pgai_agent_kanban/ relative to the repo
                              root this script lives in.
  --findings-file PATH       Write the findings as a JSON array to PATH
                              (parent directories created as needed). When
                              omitted, findings are still printed to stdout
                              as a JSON array; no file is written.
  --verbose, -v               Print each file name as it is scanned.

Examples
--------
  # From repo root, default scan surfaces:
  python3 team/scripts/lint_dual_channel_pair.py

  # Write findings to a file for a downstream pipeline to ingest:
  python3 team/scripts/lint_dual_channel_pair.py \\
      --findings-file "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/dual_channel_pair.json"
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

FIELD_NAMES = ("error", "message", "remedy")

_BASH_FUNC_NAMES = frozenset({"pgai_format_error_contract", "pgai_emit_error_contract"})
_PYTHON_FUNC_NAMES = frozenset({"format_error_contract", "emit_error_contract"})

_BASH_FUNC_RE = re.compile(r"\b(pgai_format_error_contract|pgai_emit_error_contract)\b")

# The emitter's own definition file is excluded from every scan surface --
# it defines the functions and mentions their names in its own docstring
# and usage examples, but never calls them itself.
_EXCLUDED_BASH_BASENAMES = frozenset({"error_contract.sh"})
_EXCLUDED_PYTHON_BASENAMES = frozenset({"error_contract.py", "conftest.py"})

_EXAMPLE_REMEDY_TEXT = (
    "re-run the command that produced this failure after the cause "
    "diagnosed above is resolved"
)

# Availability-probe command heads this lint recognizes: a bare emitter
# identifier immediately following one of these heads (as the head's sole
# operand) is being tested for existence, not invoked. This is the
# idiomatic guarded-self-source shape (`if ! command -v <emitter>
# >/dev/null 2>&1; then source ...; fi`) that bootstraps the emitter before
# it is known to exist -- not a call site missing its fields. The four
# spellings below -- 'command -v NAME', 'type NAME', 'type -t NAME', and
# 'declare -F NAME' -- are the probe family this lint supports; a fifth
# spelling not on this list is still treated as a call (silence over a
# false accusation runs the other way here: an unrecognized probe form is
# scanned as a real call, which can only ever over-report, never hide a
# genuine malformed call). Each pattern is anchored to the text
# immediately preceding the identifier match -- via `$` -- and excludes a
# leading `$` on the head word so a variable expansion that merely happens
# to read `$type` or `$command` is never mistaken for the builtin.
_PROBE_TAIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![\w$])command\s+-v\s+['\"]?$"),
    re.compile(r"(?<![\w$])type\s+-t\s+['\"]?$"),
    re.compile(r"(?<![\w$])type\s+['\"]?$"),
    re.compile(r"(?<![\w$])declare\s+-F\s+['\"]?$"),
)


@dataclass
class _Finding:
    """One call site missing (or blanking) one or more required fields."""

    path: Path
    line: int
    function: str
    missing: list[str] = field(default_factory=list)
    blank: list[str] = field(default_factory=list)

    @property
    def affected_fields(self) -> list[str]:
        """Missing and blank fields, in fixed house-shape order."""
        return [name for name in FIELD_NAMES if name in self.missing or name in self.blank]


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


def _extract_bash_statement(text: str, start: int) -> str:
    """Collect shell statement text beginning at *start*.

    Honors backslash line-continuations (a trailing ``\\`` immediately
    followed by a newline joins the next line with a single space, matching
    bash's own line-continuation semantics) and single/double quoting.
    Stops at the first unescaped, unquoted newline or command separator
    (``;``, ``|``, ``)``).
    """
    out: list[str] = []
    i = start
    n = len(text)
    in_single = False
    in_double = False
    while i < n:
        c = text[i]
        if c == "\\" and not in_single:
            if i + 1 < n and text[i + 1] == "\n":
                out.append(" ")
                i += 2
                continue
            if i + 1 < n:
                out.append(c)
                out.append(text[i + 1])
                i += 2
                continue
        if c == "'" and not in_double:
            in_single = not in_single
            out.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            out.append(c)
            i += 1
            continue
        if not in_single and not in_double and c in "\n;|)":
            break
        out.append(c)
        i += 1
    return "".join(out)


def _tokenize_bash_args(statement: str) -> Optional[list[str]]:
    """Split *statement* into shell words. Returns None when unparseable."""
    try:
        return shlex.split(statement, posix=True)
    except ValueError:
        return None


def _is_availability_probe_operand(prefix: str) -> bool:
    """Return True when *prefix* -- the line text up to (not including) the
    identifier match -- ends with one of the ``_PROBE_TAIL_PATTERNS``
    availability-probe heads, meaning the identifier is the probe's operand
    rather than a call to it.

    Matching against the tail of the raw line (rather than tokenizing a
    quote-bounded clause) deliberately looks through constructs such as
    ``[[ "$(type -t NAME)" != "function" ]]`` where the probe sits inside a
    command substitution: the probe head is still the words immediately
    before the identifier, regardless of the quoting and parens wrapped
    around it. What matters for classifying this occurrence is only "what
    are the words right before the name" -- not the surrounding statement
    shape.
    """
    return any(pattern.search(prefix) for pattern in _PROBE_TAIL_PATTERNS)


def _bash_call_finding(path: Path, line_no: int, func_name: str, tokens: Optional[list[str]]) -> Optional[_Finding]:
    if tokens is None:
        return None
    missing = [name for i, name in enumerate(FIELD_NAMES) if i >= len(tokens)]
    blank = [
        name
        for i, name in enumerate(FIELD_NAMES)
        if i < len(tokens) and name not in missing and tokens[i].strip() == ""
    ]
    if not missing and not blank:
        return None
    return _Finding(path=path, line=line_no, function=func_name, missing=missing, blank=blank)


def _scan_bash_file(path: Path) -> list[_Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[_Finding] = []
    for match in _BASH_FUNC_RE.finditer(text):
        idx = match.start()
        line_no = text.count("\n", 0, idx) + 1
        line_start = text.rfind("\n", 0, idx) + 1
        line_end = text.find("\n", idx)
        line_end = line_end if line_end != -1 else len(text)
        stripped_line = _strip_bash_comment(text[line_start:line_end])
        col = idx - line_start
        if col >= len(stripped_line):
            # The match falls inside a '#' comment on its own line -- not a
            # real call site, just prose mentioning the function name.
            continue
        if _is_availability_probe_operand(stripped_line[:col]):
            # The identifier is the operand of an availability probe (the
            # guarded self-source bootstrap idiom), not an invocation of the
            # emitter -- not a real call site.
            continue
        statement = _extract_bash_statement(text, match.end())
        tokens = _tokenize_bash_args(statement)
        finding = _bash_call_finding(path, line_no, match.group(1), tokens)
        if finding is not None:
            findings.append(finding)
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


def _call_func_name(node: ast.Call) -> Optional[str]:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_blank_string_literal(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip() == ""
    )


def _python_call_finding(path: Path, node: ast.Call, func_name: str) -> Optional[_Finding]:
    # A call site using *args or **kwargs spreading cannot be statically
    # resolved to a fixed error/message/remedy assignment -- skip rather
    # than guess.
    if any(isinstance(a, ast.Starred) for a in node.args):
        return None
    if any(kw.arg is None for kw in node.keywords):
        return None

    supplied: dict[str, ast.expr] = {}
    for i, arg in enumerate(node.args):
        if i < len(FIELD_NAMES):
            supplied[FIELD_NAMES[i]] = arg
    for kw in node.keywords:
        if kw.arg in FIELD_NAMES:
            supplied[kw.arg] = kw.value

    missing = [name for name in FIELD_NAMES if name not in supplied]
    blank = [
        name
        for name, value_node in supplied.items()
        if _is_blank_string_literal(value_node)
    ]
    if not missing and not blank:
        return None
    return _Finding(path=path, line=node.lineno, function=func_name, missing=missing, blank=blank)


def _scan_python_file(path: Path) -> list[_Finding]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        # A file this lint cannot parse is not this lint's failure to
        # report -- syntax errors are breakage-class, caught elsewhere.
        return []

    findings: list[_Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _call_func_name(node)
        if func_name not in _PYTHON_FUNC_NAMES:
            continue
        finding = _python_call_finding(path, node, func_name)
        if finding is not None:
            findings.append(finding)
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


def _example_fix_line(finding: _Finding, rel: str) -> str:
    fields_str = ", ".join(finding.affected_fields)
    if finding.function in _BASH_FUNC_NAMES:
        example = (
            f'{finding.function} "$error_key" "$message" "{_EXAMPLE_REMEDY_TEXT}"'
        )
    else:
        example = f'{finding.function}(error_key, message, remedy="{_EXAMPLE_REMEDY_TEXT}")'
    return (
        f"Edit {rel}:{finding.line} — supply the {fields_str} field(s) to the "
        f"{finding.function} call, e.g.: {example}"
    )


def _format_remedy_block(findings: list[_Finding], repo_root: Path, lint_script: Path) -> str:
    causes: list[str] = []
    commands: list[str] = []
    for finding in findings:
        rel = _relative(finding.path, repo_root)
        fields_str = ", ".join(finding.affected_fields)
        causes.append(
            f"{rel}:{finding.line} — {finding.function} call is missing the "
            f"{fields_str} field(s)"
        )
        commands.append(_example_fix_line(finding, rel))
    commands.append(f'python3 "{lint_script}"')

    lines = [
        f"lint_dual_channel_pair: {len(findings)} fail-loud call site(s) do not "
        "supply the full error/message/remedy triple (non-blocking finding)",
        "",
        "Cause(s):",
    ]
    for cause in causes:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append(
        "To fix (adapt the example remedy text below to the failure this site "
        "actually reports):"
    )
    for i, command in enumerate(commands, start=1):
        lines.append(f"  {i}. {command}")
    lines.append("")
    lines.append(
        "Rule: every fail-loud site must supply a non-empty error key, message, "
        "and remedy so a stranger without the internal ledger open understands "
        "what stopped, why, and what to do next."
    )
    return "\n".join(lines)


def _finding_to_dict(finding: _Finding, repo_root: Path) -> dict:
    rel = _relative(finding.path, repo_root)
    return {
        "lint": "dual_channel_pair",
        "severity": "quality",
        "file": rel,
        "line": finding.line,
        "function": finding.function,
        "missing_fields": finding.affected_fields,
        "cause": (
            f"{rel}:{finding.line} — {finding.function} call is missing the "
            f"{', '.join(finding.affected_fields)} field(s)"
        ),
        "remedy": _example_fix_line(finding, rel),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _default_bash_dirs(script_dir: Path) -> list[Path]:
    return [
        script_dir,  # team/scripts/
        script_dir / "lib",  # team/scripts/lib/
        script_dir / "cm",  # team/scripts/cm/
        script_dir / "dashboard" / "lib",  # team/scripts/dashboard/lib/
    ]


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_dual_channel_pair.py",
        description=(
            "Scan every call site of the shared dual-channel error-contract "
            "emitter (bash and Python) and report any call that does not "
            "supply the full error/message/remedy triple. Quality-class "
            "lint: findings print in full and are emitted as machine-"
            "readable JSON, but this script never exits non-zero."
        ),
    )
    parser.add_argument(
        "--bash-dirs",
        nargs="+",
        metavar="DIR",
        help=(
            "Directories to scan (non-recursively) for *.sh call sites. "
            "Default: team/scripts/, team/scripts/lib/, team/scripts/cm/, "
            "team/scripts/dashboard/lib/ relative to this script's location."
        ),
    )
    parser.add_argument(
        "--python-dir",
        metavar="DIR",
        help=(
            "Root directory to scan (recursively) for *.py call sites. "
            "Default: team/pgai_agent_kanban/ relative to the repo root."
        ),
    )
    parser.add_argument(
        "--findings-file",
        metavar="PATH",
        help=(
            "Write the findings as a JSON array to PATH (parent directories "
            "created as needed). Findings are always printed to stdout as "
            "JSON as well; this flag additionally persists them to disk."
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
    """Run the dual-channel pair lint, printing each finding and its JSON array.

    Exit codes: 0 always, whatever was found (quality class); 2 usage error
    (argparse).
    """
    args = _parse_args(argv)

    script_dir = Path(__file__).resolve().parent  # team/scripts/
    repo_root = script_dir.parent.parent  # repo root

    bash_dirs = (
        [Path(d) for d in args.bash_dirs] if args.bash_dirs else _default_bash_dirs(script_dir)
    )
    python_dir = (
        Path(args.python_dir) if args.python_dir else (repo_root / "team" / "pgai_agent_kanban")
    )

    bash_files = _iter_bash_files(bash_dirs)
    python_files = _iter_python_files(python_dir)

    print(
        f"lint_dual_channel_pair: scanning {len(bash_files)} bash file(s) and "
        f"{len(python_files)} python file(s) for dual-channel call sites",
        flush=True,
    )

    findings: list[_Finding] = []
    checked = 0
    for bash_file in bash_files:
        checked += 1
        if args.verbose:
            print(f"  scanning: {bash_file}", flush=True)
        findings.extend(_scan_bash_file(bash_file))

    for python_file in python_files:
        checked += 1
        if args.verbose:
            print(f"  scanning: {python_file}", flush=True)
        findings.extend(_scan_python_file(python_file))

    findings.sort(key=lambda f: (str(f.path), f.line))

    if findings:
        lint_script = Path(__file__).resolve()
        print("\n" + _format_remedy_block(findings, repo_root, lint_script), flush=True)
    else:
        print(
            f"lint_dual_channel_pair: OK — {checked} file(s) checked, 0 offenders.",
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

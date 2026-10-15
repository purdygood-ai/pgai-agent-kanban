#!/usr/bin/env python3
"""
lint_runnable_remedy.py
========================
Quality-class lint: scans every call site of the shared dual-channel
error-contract emitter and flags any REMEDY field that still carries an
angle-bracket placeholder — ``<name>``, ``<project>``, ``<sha>`` and the
like — text a stranger cannot paste into a shell and run as printed.

The dual-channel contract (``team/scripts/lib/error_contract.sh``,
``pgai_agent_kanban.lib.error_contract``) already guarantees a remedy
field exists and is non-blank; ``lint_dual_channel_pair.py`` checks
exactly that. Neither checks that the remedy can be RUN. A remedy such as
``"Re-run with --project <name>, e.g. team/scripts/defer.sh --project
<name> --until vX.Y.Z."`` hands the reader a template, not a command —
this lint is the static check that closes that gap.

WHAT IS CHECKED
---------------
This lint reuses ``lint_dual_channel_pair.py``'s call-site discovery
verbatim (same scanned-surface list, same bash statement tokenizer, same
Python AST call matcher) by importing that module directly, so the two
lints never drift on "what counts as a call site":

  Bash    (pgai_format_error_contract, pgai_emit_error_contract)
          -- team/scripts/, team/scripts/lib/, team/scripts/cm/,
             team/scripts/dashboard/lib/ (each scanned non-recursively).
  Python  (format_error_contract, emit_error_contract)
          -- team/pgai_agent_kanban/ (scanned recursively), excluding test
             and fixture directories.

Only the REMEDY argument is in scope — the third positional argument to
either bash emitter function, or the third positional argument / a
``remedy=`` keyword to either Python emitter function. The error and
message fields, and every usage header, docstring, and comment in the
tree, are out of scope by design: a message field may legitimately
describe an expected FORMAT with a placeholder ("expected vX.Y.Z"); only
the remedy — the thing a stranger is told to run right now — must be free
of one.

A remedy is a finding when its literal text contains an angle-bracket
placeholder matching ``<[^<>]+>``:

  - Bash: the token is checked as its raw (unexpanded) text — a shell
    variable such as ``${_project}`` never renders as a literal ``<...>``,
    so only text the script author actually typed between angle brackets
    is ever flagged.
  - Python: a plain string literal is checked whole; an f-string
    (``ast.JoinedStr``) has its literal chunks concatenated and checked,
    skipping the interpolated ``{expr}`` parts — a remedy built entirely
    from a resolved value never trips this lint, even when the same
    expression could theoretically resolve to something surprising at
    runtime. That risk is out of scope for a static, literal-text check,
    exactly like its sibling lint.

A remedy passed as a bare variable, a bash command substitution with no
surrounding literal brackets, or a Python call whose remedy argument is
not statically resolvable (``*args``/``**kwargs`` spreading) is not
evaluated for content — silence over a false accusation, matching
``lint_dual_channel_pair.py``'s own posture.

QUALITY-CLASS POSTURE
----------------------
This lint is CONVERT-class (docs/blocking-site-census.md): a remedy that
still carries a placeholder is a quality/usability regression, not a
runtime failure — the refusal itself already fired correctly, and the
dual-channel triple is already non-blank. It is wrapped by
``run-unit-tests.sh`` through ``gated_runner_run_quality_lint``, which
absorbs this script's own exit code into a non-fatal finding, exactly like
``lint_operator_verb_manifest.py``. Run standalone (not through that
wrapper), this script's own process exit code is 1 when it finds anything
and 0 when the tree is clean — the exit code is the return value pytest
and other automated callers read; only the wrapped invocation in
``run-unit-tests.sh`` treats a 1 as non-fatal.

Exit codes (standalone invocation)
-----------------------------------
  0   No remedy in the scanned surface carries an angle-bracket
      placeholder.
  1   One or more remedies do.

Usage
-----
  python3 team/scripts/lint_runnable_remedy.py [--bash-dirs DIR [DIR ...]]
                                                [--python-dir DIR]
                                                [--findings-file PATH]
                                                [--verbose]

  --bash-dirs DIR [DIR ...]  Directories to scan (non-recursively) for
                              ``*.sh`` call sites. Default: the same four
                              production bash surfaces
                              ``lint_dual_channel_pair.py`` scans.
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
  python3 team/scripts/lint_runnable_remedy.py

  # Write findings to a file for a downstream pipeline to ingest:
  python3 team/scripts/lint_runnable_remedy.py \\
      --findings-file "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/runnable_remedy.json"
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ``lint_dual_channel_pair.py`` lives next to this script; importing it
# directly (rather than re-deriving its scanning logic) is the reuse the
# task brief asks for -- the two lints share one definition of "what is a
# call site" and can never drift apart on that question.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_dual_channel_pair as _ldc  # noqa: E402

REMEDY_FIELD_INDEX = _ldc.FIELD_NAMES.index("remedy")

_PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")


@dataclass
class _Finding:
    """One remedy field that still carries an angle-bracket placeholder."""

    path: Path
    line: int
    function: str
    remedy: str
    placeholders: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bash scanning — mirrors lint_dual_channel_pair._scan_bash_file, but checks
# the remedy token's literal text for a placeholder instead of checking all
# three fields for missing/blank content.
# ---------------------------------------------------------------------------


def _scan_bash_file(path: Path) -> list[_Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[_Finding] = []
    for match in _ldc._BASH_FUNC_RE.finditer(text):
        idx = match.start()
        line_no = text.count("\n", 0, idx) + 1
        line_start = text.rfind("\n", 0, idx) + 1
        line_end = text.find("\n", idx)
        line_end = line_end if line_end != -1 else len(text)
        stripped_line = _ldc._strip_bash_comment(text[line_start:line_end])
        col = idx - line_start
        if col >= len(stripped_line):
            # The match falls inside a '#' comment on its own line -- not a
            # real call site, just prose mentioning the function name.
            continue
        if _ldc._is_availability_probe_operand(stripped_line[:col]):
            continue
        statement = _ldc._extract_bash_statement(text, match.end())
        tokens = _ldc._tokenize_bash_args(statement)
        if tokens is None or len(tokens) <= REMEDY_FIELD_INDEX:
            # Fewer than three arguments: lint_dual_channel_pair.py already
            # reports the missing-field case; a remedy that was never
            # supplied has no placeholder text to check here.
            continue
        remedy = tokens[REMEDY_FIELD_INDEX]
        placeholders = _PLACEHOLDER_RE.findall(remedy)
        if placeholders:
            findings.append(
                _Finding(
                    path=path,
                    line=line_no,
                    function=match.group(1),
                    remedy=remedy,
                    placeholders=list(dict.fromkeys(placeholders)),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Python scanning — mirrors lint_dual_channel_pair._scan_python_file's
# call-matching, but resolves and checks only the remedy argument's literal
# text.
# ---------------------------------------------------------------------------


def _remedy_literal_text(node: ast.expr) -> Optional[str]:
    """Return the literal text of *node* for placeholder scanning.

    A plain string constant returns its value whole. An f-string
    (``ast.JoinedStr``) returns the concatenation of its literal chunks
    only -- interpolated ``{expr}`` parts are skipped, since an
    already-resolved value can never itself spell a literal ``<...>``
    placeholder the way hand-typed template text can. Any other node shape
    (a bare variable, a function call, string concatenation via ``+``)
    returns None -- not statically resolvable to literal text, and
    therefore not evaluated, matching this lint's sibling's posture of
    silence over a false accusation.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts)
    return None


def _python_call_finding(path: Path, node: ast.Call, func_name: str) -> Optional[_Finding]:
    if any(isinstance(a, ast.Starred) for a in node.args):
        return None
    if any(kw.arg is None for kw in node.keywords):
        return None

    supplied: dict[str, ast.expr] = {}
    for i, arg in enumerate(node.args):
        if i < len(_ldc.FIELD_NAMES):
            supplied[_ldc.FIELD_NAMES[i]] = arg
    for kw in node.keywords:
        if kw.arg in _ldc.FIELD_NAMES:
            supplied[kw.arg] = kw.value

    remedy_node = supplied.get("remedy")
    if remedy_node is None:
        return None

    literal_text = _remedy_literal_text(remedy_node)
    if literal_text is None:
        return None

    placeholders = _PLACEHOLDER_RE.findall(literal_text)
    if not placeholders:
        return None

    return _Finding(
        path=path,
        line=node.lineno,
        function=func_name,
        remedy=literal_text,
        placeholders=list(dict.fromkeys(placeholders)),
    )


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
        func_name = _ldc._call_func_name(node)
        if func_name not in _ldc._PYTHON_FUNC_NAMES:
            continue
        finding = _python_call_finding(path, node, func_name)
        if finding is not None:
            findings.append(finding)
    return findings


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


def _finding_to_dict(finding: _Finding, repo_root: Path) -> dict:
    rel = _relative(finding.path, repo_root)
    substrings = ", ".join(finding.placeholders)
    return {
        "lint": "runnable_remedy",
        "severity": "quality",
        "file": rel,
        "line": finding.line,
        "function": finding.function,
        "placeholders": finding.placeholders,
        "cause": (
            f"{rel}:{finding.line} — {finding.function} remedy contains "
            f"angle-bracket placeholder(s) {substrings}: {finding.remedy!r}"
        ),
        "remedy": (
            f"Edit {rel}:{finding.line} — rewrite the remedy argument to a "
            "literal runnable command: substitute the value the script "
            "already resolved, or (where nothing was resolvable at the "
            "refusal point) name the environment variable to set or a real "
            "value read from projects.cfg instead of a placeholder."
        ),
    }


def _print_findings_block(findings: list[_Finding], repo_root: Path) -> None:
    print(
        f"\nlint_runnable_remedy: {len(findings)} remedy field(s) still "
        "carry an angle-bracket placeholder a stranger cannot run as "
        "printed:\n",
        flush=True,
    )
    for finding in findings:
        rel = _relative(finding.path, repo_root)
        substrings = ", ".join(finding.placeholders)
        print(
            f"  - {rel}:{finding.line} — {finding.function}(...): "
            f"placeholder(s) {substrings} in remedy {finding.remedy!r}",
            flush=True,
        )
    print(
        "\nRule: the remedy field is what a stranger is told to run to "
        "clear the refusal -- it must be pasteable as printed, with every "
        "value the script already resolved substituted in and every "
        "unresolvable value named as an env var or a real config value "
        "instead of a '<...>' template slot.",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_runnable_remedy.py",
        description=(
            "Scan every call site of the shared dual-channel error-contract "
            "emitter (bash and Python) and report any remedy field that "
            "still carries an angle-bracket placeholder. Quality-class "
            "(CONVERT) lint: findings print in full and are emitted as "
            "machine-readable JSON; wrap this script's exit code through "
            "gated_runner_run_quality_lint (as run-unit-tests.sh does) to "
            "keep a finding non-fatal."
        ),
    )
    parser.add_argument(
        "--bash-dirs",
        nargs="+",
        metavar="DIR",
        help=(
            "Directories to scan (non-recursively) for *.sh call sites. "
            "Default: the same four surfaces lint_dual_channel_pair.py "
            "scans, relative to this script's location."
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
    """Run the runnable-remedy lint and print each placeholder-bearing remedy.

    Exit codes (standalone): 0 no remedy carries an angle-bracket placeholder;
    1 one or more do. The gated runner records a 1 as a quality finding.
    """
    args = _parse_args(argv)

    script_dir = Path(__file__).resolve().parent  # team/scripts/
    repo_root = script_dir.parent.parent  # repo root

    bash_dirs = (
        [Path(d) for d in args.bash_dirs] if args.bash_dirs else _ldc._default_bash_dirs(script_dir)
    )
    python_dir = (
        Path(args.python_dir) if args.python_dir else (repo_root / "team" / "pgai_agent_kanban")
    )

    bash_files = _ldc._iter_bash_files(bash_dirs)
    python_files = _ldc._iter_python_files(python_dir)

    print(
        f"lint_runnable_remedy: scanning {len(bash_files)} bash file(s) and "
        f"{len(python_files)} python file(s) for remedy placeholders",
        flush=True,
    )

    findings: list[_Finding] = []
    for bash_file in bash_files:
        if args.verbose:
            print(f"  scanning: {bash_file}", flush=True)
        findings.extend(_scan_bash_file(bash_file))

    for python_file in python_files:
        if args.verbose:
            print(f"  scanning: {python_file}", flush=True)
        findings.extend(_scan_python_file(python_file))

    findings.sort(key=lambda f: (str(f.path), f.line))

    finding_dicts = [_finding_to_dict(f, repo_root) for f in findings]
    findings_json = json.dumps(finding_dicts, indent=2, sort_keys=True)

    if args.findings_file:
        findings_path = Path(args.findings_file)
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_text(findings_json + "\n", encoding="utf-8")

    if not findings:
        print(
            f"lint_runnable_remedy: OK — {len(bash_files) + len(python_files)} "
            "file(s) checked, 0 offenders.",
            flush=True,
        )
        print("\nFINDINGS_JSON:", flush=True)
        print(findings_json, flush=True)
        return 0

    _print_findings_block(findings, repo_root)
    print("\nFINDINGS_JSON:", flush=True)
    print(findings_json, flush=True)

    error = "runnable_remedy.placeholder_found"
    message = (
        f"{len(findings)} dual-channel emitter call site(s) hand the "
        "reader a remedy containing an angle-bracket placeholder -- see "
        "the findings printed above"
    )
    remedy = (
        "rewrite each flagged remedy to a literal runnable command "
        "(substitute the value the script already resolved, or name the "
        "environment variable / a real projects.cfg value where nothing "
        "was resolvable), then re-run: "
        "python3 team/scripts/lint_runnable_remedy.py"
    )
    # CONVERT (docs/blocking-site-census.md): a placeholder-laden remedy is
    # a usability/quality regression in an already-correct refusal -- the
    # dual-channel triple is present and non-blank, the refusal itself
    # already fired -- so this never blocks a build or a release; it is
    # wrapped through gated_runner_run_quality_lint in run-unit-tests.sh,
    # which absorbs this exit code into a non-fatal finding exactly like
    # lint_operator_verb_manifest.py's own CONVERT site.
    # CENSUS-ANCHOR: runnable-remedy-placeholder-01
    try:
        from pgai_agent_kanban.lib.error_contract import emit_error_contract

        emit_error_contract(error, message, remedy)
    except ImportError:
        print(f"error:   {error}\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

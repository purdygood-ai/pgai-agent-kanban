#!/usr/bin/env python3
"""
lint_inline_python.py
=====================
A ratchet lint that keeps new inline Python out of the bash scripts under
``team/scripts``.

The coding standard is that a bash script is a thin wrapper which calls a
Python module (``pp_run_ops`` / ``python3 -m`` / ``python3 <file>.py``);
Python logic embedded in a bash file cannot be imported, reused, or unit
tested on its own. The tree already carries inline Python, so this lint does
not demand zero: it compares every file against a per-file allowlist
recorded from the tree as it stood, and reports any file that grew.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Gates and Refusals" and
"Blocking Severity: only real damage blocks"). Inline Python carries no
runtime-failure risk by itself, so this lint narrates its findings, writes
them to ``--findings-file`` for the gated runner's aggregate, and ALWAYS
exits 0. It never fails a runner or a release.

WHAT IS COUNTED
---------------
Every non-test ``*.sh`` file under the scan directory (default
``team/scripts``). A file is a test file, and is skipped, when any directory
between the scan directory and the file is named ``tests`` or ``test``.

Two shapes count as one inline-Python BLOCK each:

  1. A heredoc fed to a Python interpreter reading its program from stdin:
     ``python3 - [args] <<'PY'``, ``python3 /dev/stdin [args] <<'PY'`` or
     ``python3 <<'PY'``. The command may be
     spread across backslash-continued lines (``python3 - \\`` followed by
     argument lines and a ``<<'PY'`` line), and the interpreter may carry a
     path prefix or a version suffix (``/usr/bin/python3.11``). Its PYTHON
     LINES are the body lines between the opener and the closing delimiter.
     A heredoc fed to anything else (``cat > file.py <<'PY'``, a Python
     script given as a file argument reading data on stdin) is not counted,
     but its body is skipped so text inside it is never mistaken for code.
  2. A ``python3 -c <program>`` invocation. Its PYTHON LINES are the lines
     of the program string with leading and trailing blank lines removed,
     minimum 1 (a one-liner, or a program passed in a variable, is 1 line).

Comment-only lines (first non-blank character ``#``) are never scanned.

THE ALLOWLIST AND THE RATCHET RULE
----------------------------------
``team/config/inline-python-allowlist.txt`` holds one line per file:

    <path>\\t<blocks>\\t<python_lines>

``<path>`` is relative to the repository root (``team/scripts/foo.sh``).
Fields may be separated by any run of whitespace; blank lines and lines
starting with ``#`` are ignored. Reading rules:

  * An unlisted file must have 0 blocks.            -> finding ``unlisted``
  * A listed file may not exceed either count.      -> finding ``above``
  * A listed file below either count (and above
    neither) must have its entry tightened to the
    measured counts in the same change.             -> finding ``below``
  * An entry for a file that no longer exists, or
    is no longer scanned, is reported for removal.  -> finding ``below``
  * An unparseable allowlist line.                  -> finding ``allowlist-malformed``

The ratchet only turns one way: an entry is lowered or removed when a block
is converted to a module, and never raised. ``--emit-allowlist`` prints the
measured counts in allowlist format, for writing a tightened entry; it is
not a licence to raise one.

Independently of the counts, a Python heredoc opened with an UNQUOTED
delimiter (``<<PY`` rather than ``<<'PY'``) is reported as
``unquoted-delimiter`` even when its file is within its allowlist: bash
expands ``$`` and backticks inside such a body before Python sees it.

OUTPUT
------
One narration line per finding, then a totals line:

    lint_inline_python: totals: <N> file(s) scanned, <B> block(s)
    (<H> heredoc, <C> python3 -c), <L> python line(s); <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (one object per
finding, keys ``lint``, ``severity``, ``kind``, ``file``, ``line``,
``summary``, ``remedy``) in the shape
``team/scripts/lib/gated_runner_lint.sh``'s ``gated_runner_merge_findings_file``
merges. The file is always written, as ``[]`` when there is nothing to report.

USAGE
-----
  python3 team/scripts/lint_inline_python.py [OPTIONS]

  --root PATH            Repository root (default: two levels above this
                         script's directory).
  --scan-dir PATH        Directory to scan, relative to --root or absolute
                         (default: team/scripts).
  --allowlist PATH       Allowlist file, relative to --root or absolute
                         (default: team/config/inline-python-allowlist.txt).
  --findings-file PATH   Write findings as a JSON array to PATH.
  --emit-allowlist       Print the measured per-file counts in allowlist
                         format to stdout and exit 0 (no findings check).
  --verbose, -v          Print each file's counts as it is scanned.

EXIT CODES
----------
  0   Always, whatever was found (quality class).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

# team/ import path -- needed for the dual-channel error-contract emitter.
_TEAM_DIR = Path(__file__).resolve().parent.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

LINT_NAME = "inline_python"
DEFAULT_SCAN_DIR = "team/scripts"
DEFAULT_ALLOWLIST = "team/config/inline-python-allowlist.txt"
TEST_DIR_NAMES = frozenset({"tests", "test"})
RUN_COMMAND = "python3 team/scripts/lint_inline_python.py"

# A Python interpreter word: optional path prefix, python or python3, optional
# minor-version suffix. The leading boundary keeps `mypython3` or `python3x`
# from matching.
_INTERP = r"(?<![\w.-])(?:[\w./${}-]*/)?python3?(?:\.\d+)?(?![\w.-])"

# Interpreter reading its program from stdin: options other than -c/-m, then
# a lone `-`, an explicit /dev/stdin or /dev/fd/0, or straight into a heredoc
# opener.
_STDIN_INVOCATION_RE = re.compile(
    _INTERP
    + r"(?:[ \t]+-(?![cm](?:\s|$))[A-Za-z]+)*"
    + r"(?:[ \t]+(?:-|/dev/stdin|/dev/fd/0)(?=\s|$)|[ \t]*(?=<<))"
)

# Interpreter given its program inline with -c.
_DASH_C_RE = re.compile(_INTERP + r"(?:[ \t]+-(?![cm](?:\s|$))[A-Za-z]+)*[ \t]+-c[ \t]+")

# Heredoc opener: `<<` or `<<-` (never the `<<<` here-string), then a
# delimiter word that is bare, quoted, or backslash-escaped.
_HEREDOC_RE = re.compile(
    r"(?<!<)<<(?!<)(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\([A-Za-z_][\w]*)|([A-Za-z_][\w]*))"
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Block(NamedTuple):
    """One inline-Python block found in a file."""

    kind: str  # "heredoc" or "dash-c"
    line: int  # 1-based line of the invocation / opener
    python_lines: int
    unquoted_delimiter: bool = False
    delimiter: str = ""


class FileCounts(NamedTuple):
    """Per-file totals."""

    rel_path: str
    blocks: list[Block]

    @property
    def block_count(self) -> int:
        """Return the number of inline-Python blocks counted in the file."""
        return len(self.blocks)

    @property
    def line_count(self) -> int:
        """Return the total Python lines across the file's inline-Python blocks."""
        return sum(b.python_lines for b in self.blocks)


class Finding(NamedTuple):
    """One lint finding."""

    kind: str
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity``, ``kind``, ``file``, ``line``, ``summary``, ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": self.remedy,
        }


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _ends_with_continuation(segment: str) -> bool:
    """True when *segment* ends in an odd run of backslashes (line continues)."""
    stripped = segment.rstrip(" \t")
    run = len(stripped) - len(stripped.rstrip("\\"))
    return run % 2 == 1


def _line_end(text: str, pos: int) -> int:
    """Offset of the newline ending the line that contains *pos* (or len)."""
    nl = text.find("\n", pos)
    return len(text) if nl == -1 else nl


def _find_program_end(text: str, start: int) -> tuple[int, int]:
    """Locate the -c program token beginning at *start*.

    Returns ``(content_start, content_end)``: offsets of the program text,
    excluding its quotes. An unterminated quote runs to end of file.
    """
    if start >= len(text):
        return start, start
    ch = text[start]
    if ch == "$" and start + 1 < len(text) and text[start + 1] == "'":
        start += 1
        ch = "'"
    if ch == "'":
        close = text.find("'", start + 1)
        if close == -1:
            close = len(text)
        return start + 1, close
    if ch == '"':
        i = start + 1
        while i < len(text):
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == '"':
                return start + 1, i
            i += 1
        return start + 1, len(text)
    # Bare token ($VAR, word): runs to the next whitespace.
    m = re.compile(r"\S+").match(text, start)
    end = m.end() if m else start
    return start, end


def _program_line_count(program: str) -> int:
    """Lines of a -c program, leading/trailing blank lines removed, minimum 1."""
    lines = program.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return max(1, len(lines))


def scan_text(text: str) -> list[Block]:
    """Return every inline-Python block in a bash script's *text*."""
    blocks: list[Block] = []
    n = len(text)
    pos = 0
    while pos < n:
        end = _line_end(text, pos)
        first_line = text[pos:end]
        if first_line.lstrip().startswith("#") or not first_line.strip():
            pos = end + 1
            continue

        # Grow the logical command over backslash continuations and over -c
        # program strings that span lines. Program spans are recorded so they
        # can be masked before looking for heredoc openers.
        program_spans: list[tuple[int, int]] = []
        search_from = pos
        while True:
            while _ends_with_continuation(text[pos:end]) and end < n:
                end = _line_end(text, end + 1)
            m = _DASH_C_RE.search(text, search_from, end)
            if m is None:
                break
            c_start, c_end = _find_program_end(text, m.end())
            program = text[c_start:c_end]
            blocks.append(
                Block(
                    kind="dash-c",
                    line=text.count("\n", 0, m.start()) + 1,
                    python_lines=_program_line_count(program),
                )
            )
            program_spans.append((c_start, c_end))
            search_from = min(c_end + 1, n)
            if c_end > end:
                end = _line_end(text, c_end)

        # Masked logical command: program text blanked (newlines kept so
        # offsets still map to lines), continuation backslash-newlines joined.
        chars = list(text[pos:end])
        for s, e in program_spans:
            for k in range(max(s, pos), min(e, end)):
                if chars[k - pos] != "\n":
                    chars[k - pos] = " "
        logical = "".join(chars)
        logical_joined = re.sub(r"\\\n", "  ", logical)

        body_pos = end + 1
        prev_opener_end = 0
        for hm in _HEREDOC_RE.finditer(logical_joined):
            dash, sq, dq, esc, bare = hm.groups()
            delimiter = sq or dq or esc or bare
            quoted = bare is None
            is_python = (
                _STDIN_INVOCATION_RE.search(logical_joined, prev_opener_end, hm.start() + 2)
                is not None
            )
            prev_opener_end = hm.end()

            # Walk the body to its closing delimiter line.
            body_lines = 0
            cursor = body_pos
            closed = False
            while cursor < n:
                lend = _line_end(text, cursor)
                candidate = text[cursor:lend].rstrip(" \t\r")
                if dash:
                    candidate = candidate.lstrip("\t")
                if candidate == delimiter:
                    closed = True
                    cursor = lend + 1
                    break
                body_lines += 1
                cursor = lend + 1
            if not closed:
                cursor = n
            if is_python:
                blocks.append(
                    Block(
                        kind="heredoc",
                        line=text.count("\n", 0, pos + hm.start()) + 1,
                        python_lines=body_lines,
                        unquoted_delimiter=not quoted,
                        delimiter=delimiter,
                    )
                )
            body_pos = cursor

        pos = max(body_pos, end + 1)
    blocks.sort(key=lambda b: b.line)
    return blocks


def scan_file(path: Path) -> list[Block]:
    """Scan one bash file."""
    return scan_text(path.read_text(encoding="utf-8", errors="replace"))


def iter_scan_files(scan_dir: Path) -> list[Path]:
    """Every non-test ``*.sh`` file under *scan_dir*, sorted."""
    out = []
    for p in sorted(scan_dir.rglob("*.sh")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(scan_dir).parts[:-1]
        if any(part in TEST_DIR_NAMES for part in rel_parts):
            continue
        out.append(p)
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def measure(root: Path, scan_dir: Path, *, verbose: bool = False) -> list[FileCounts]:
    """Count inline Python in every scanned file."""
    results = []
    for p in iter_scan_files(scan_dir):
        rel = _rel(p, root)
        counts = FileCounts(rel_path=rel, blocks=scan_file(p))
        if verbose:
            print(f"  [inline-python] {rel}: {counts.block_count} block(s), {counts.line_count} line(s)")
        results.append(counts)
    return results


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def load_allowlist(path: Path) -> tuple[dict[str, tuple[int, int, int]], list[Finding]]:
    """Read the allowlist into ``{path: (blocks, lines, lineno)}``.

    Liberal reader: any whitespace run separates fields; blank and ``#``
    lines are skipped. Unparseable lines become findings and are ignored.
    """
    entries: dict[str, tuple[int, int, int]] = {}
    findings: list[Finding] = []
    allow_rel = path.as_posix()
    if not path.is_file():
        findings.append(
            Finding(
                kind="allowlist-missing",
                file=allow_rel,
                line=0,
                summary=(
                    f"allowlist {allow_rel} not found; every file with inline Python "
                    "is reported as unlisted"
                ),
                remedy=f"{RUN_COMMAND} --emit-allowlist",
            )
        )
        return entries, findings
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 3 or not fields[1].isdigit() or not fields[2].isdigit():
            findings.append(
                Finding(
                    kind="allowlist-malformed",
                    file=allow_rel,
                    line=lineno,
                    summary=(
                        f"allowlist line is not '<path> <blocks> <python_lines>' "
                        f"and was ignored: {raw!r}"
                    ),
                    remedy=f"{RUN_COMMAND} --emit-allowlist",
                )
            )
            continue
        entries[fields[0]] = (int(fields[1]), int(fields[2]), lineno)
    return entries, findings


def format_allowlist(results: list[FileCounts]) -> str:
    """Render measured counts in allowlist format (files with blocks only)."""
    rows = [
        f"{r.rel_path}\t{r.block_count}\t{r.line_count}"
        for r in sorted(results, key=lambda r: r.rel_path)
        if r.block_count
    ]
    return "\n".join(rows) + ("\n" if rows else "")


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare(
    results: list[FileCounts],
    allowlist: dict[str, tuple[int, int, int]],
    allowlist_rel: str,
) -> list[Finding]:
    """Compare measured counts with the allowlist; return findings."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for r in results:
        seen.add(r.rel_path)
        b, n = r.block_count, r.line_count
        entry = allowlist.get(r.rel_path)
        if entry is None:
            if b:
                first = r.blocks[0].line
                findings.append(
                    Finding(
                        kind="unlisted",
                        file=r.rel_path,
                        line=first,
                        summary=(
                            f"{r.rel_path} is not in the allowlist but carries {b} inline "
                            f"Python block(s) ({n} line(s)); first at line {first}"
                        ),
                        remedy=(
                            "move the Python into a module and call it with python3 -m "
                            f"or pp_run_ops, then re-run: {RUN_COMMAND}"
                        ),
                    )
                )
        else:
            ab, an, _lineno = entry
            if b > ab or n > an:
                findings.append(
                    Finding(
                        kind="above",
                        file=r.rel_path,
                        line=r.blocks[-1].line if r.blocks else 0,
                        summary=(
                            f"{r.rel_path} has {b} block(s) / {n} python line(s), above its "
                            f"allowlist entry of {ab} / {an}"
                        ),
                        remedy=(
                            "move the added Python into a module and call it with "
                            f"python3 -m or pp_run_ops, then re-run: {RUN_COMMAND}"
                        ),
                    )
                )
            elif b < ab or n < an:
                if b:
                    action = f"set its {allowlist_rel} entry to '{r.rel_path}\t{b}\t{n}'"
                else:
                    action = f"remove its {allowlist_rel} entry"
                findings.append(
                    Finding(
                        kind="below",
                        file=r.rel_path,
                        line=0,
                        summary=(
                            f"{r.rel_path} has {b} block(s) / {n} python line(s), below its "
                            f"allowlist entry of {ab} / {an}: tighten the entry ({action})"
                        ),
                        remedy=f"{RUN_COMMAND} --emit-allowlist",
                    )
                )
        for blk in r.blocks:
            if blk.unquoted_delimiter:
                findings.append(
                    Finding(
                        kind="unquoted-delimiter",
                        file=r.rel_path,
                        line=blk.line,
                        summary=(
                            f"{r.rel_path}:{blk.line} opens a Python heredoc with the unquoted "
                            f"delimiter <<{blk.delimiter}; bash expands $ and backticks in its "
                            "body before Python runs it"
                        ),
                        remedy=(
                            f"quote the delimiter as <<'{blk.delimiter}' and pass shell values "
                            f"as arguments, then re-run: {RUN_COMMAND}"
                        ),
                    )
                )
    for path, (ab, an, lineno) in sorted(allowlist.items()):
        if path in seen:
            continue
        findings.append(
            Finding(
                kind="below",
                file=path,
                line=0,
                summary=(
                    f"allowlist entry '{path}' ({ab} / {an}, {allowlist_rel} line {lineno}) "
                    "names a file that is not scanned: tighten the entry (remove it)"
                ),
                remedy=f"{RUN_COMMAND} --emit-allowlist",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_inline_python",
        description=(
            "Ratchet lint: count inline Python (python3 heredocs and python3 -c) "
            "in non-test bash under team/scripts and compare each file against "
            "the per-file allowlist. Quality class: always exits 0."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Allowlist format: one '<path>\\t<blocks>\\t<python_lines>' line per file,\n"
            "path relative to the repository root. Entries are only ever lowered or\n"
            "removed, never raised.\n\n"
            "Exit codes:\n  0  Always (findings are narrated, never fatal).\n"
        ),
    )
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root (default: two levels above this script's directory).")
    parser.add_argument("--scan-dir", metavar="PATH", default=DEFAULT_SCAN_DIR,
                        help=f"Directory to scan, relative to --root or absolute (default: {DEFAULT_SCAN_DIR}).")
    parser.add_argument("--allowlist", metavar="PATH", default=DEFAULT_ALLOWLIST,
                        help=f"Allowlist file, relative to --root or absolute (default: {DEFAULT_ALLOWLIST}).")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                        help="Write findings as a JSON array to PATH (always written).")
    parser.add_argument("--emit-allowlist", action="store_true",
                        help="Print measured counts in allowlist format and exit 0.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print each file's counts as it is scanned.")
    return parser


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


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
        print(f"lint_inline_python: could not write findings file {path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the lint. Always returns 0."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent.parent
    scan_dir = _resolve(root, args.scan_dir)
    allowlist_path = _resolve(root, args.allowlist)
    allowlist_rel = _rel(allowlist_path, root)

    findings: list[Finding] = []
    if not scan_dir.is_dir():
        findings.append(
            Finding(
                kind="scan-dir-missing",
                file=_rel(scan_dir, root),
                line=0,
                summary=f"scan directory {scan_dir} does not exist; nothing was scanned",
                remedy=f"{RUN_COMMAND} --scan-dir team/scripts",
            )
        )
        results: list[FileCounts] = []
    else:
        results = measure(root, scan_dir, verbose=args.verbose)

    if args.emit_allowlist:
        sys.stdout.write(format_allowlist(results))
        return 0

    entries, allow_findings = load_allowlist(allowlist_path)
    allow_findings = [f._replace(file=allowlist_rel) for f in allow_findings]
    findings.extend(allow_findings)
    findings.extend(compare(results, entries, allowlist_rel))
    _write_findings(args.findings_file, findings)

    for f in findings:
        print(f"lint_inline_python: [{f.kind}] {f.summary}")

    heredocs = sum(1 for r in results for b in r.blocks if b.kind == "heredoc")
    dash_c = sum(1 for r in results for b in r.blocks if b.kind == "dash-c")
    total_lines = sum(r.line_count for r in results)
    print(
        f"lint_inline_python: totals: {len(results)} file(s) scanned, "
        f"{heredocs + dash_c} block(s) ({heredocs} heredoc, {dash_c} python3 -c), "
        f"{total_lines} python line(s); {len(findings)} finding(s)"
    )

    if findings:
        message = (
            f"{len(findings)} inline-Python finding(s) against {allowlist_rel}; "
            "see the lines printed above"
        )
        remedy = (
            "convert new inline Python to a module called via python3 -m or "
            "pp_run_ops, tighten any entry reported below its counts, then re-run: "
            f"{RUN_COMMAND}"
        )
        # CENSUS-ANCHOR: inline-python-01
        # CONVERT: a quality-class finding with zero runtime-failure risk --
        # narrated and filed, never fatal; the lint returns 0 below.
        sys.stdout.flush()
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract("lint.inline-python", message, remedy)
        except ImportError:
            print(f"error:   lint.inline-python\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

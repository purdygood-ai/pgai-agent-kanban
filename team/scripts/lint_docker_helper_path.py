#!/usr/bin/env python3
"""
lint_docker_helper_path.py
==========================
Checks that kanban code reaches a container engine (docker or podman) only
through the shared helper ``pgai_docker`` in ``team/scripts/lib/docker_use.sh``.

That helper is the one path to an engine, and it is what records each use
in ``logs/docker-use.log``. A script that calls ``docker build`` (or runs a
configured engine binary such as ``"${ENGINE}" run``) directly bypasses the
log, so nobody can see that it reached for docker. This lint names every such
call.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Blocking Severity: only real
damage blocks"). A direct engine call is a hygiene finding, not a runtime
failure: the lint narrates each finding, writes them to ``--findings-file``
for the gated runner's aggregate, and ALWAYS exits 0.

WHAT IS SCANNED
---------------
Every ``*.sh`` and ``*.py`` file under ``team/`` (relative to ``--root``),
except:

  * test code -- any file with a directory named ``tests`` or ``test``
    between ``team/`` and the file. Tests drive engines through stubs, and
    the container-tier tests run inside a harness's own docker-use session;
  * the helper itself, ``team/scripts/lib/docker_use.sh``.

WHAT COUNTS AS A DIRECT CALL
----------------------------
Shell files (comment lines and heredoc bodies are skipped; the text inside
quoted strings is ignored, except a quoted lone variable such as
``"${ENGINE}"``, which is a command word):

  * ``docker`` or ``podman`` (optionally with a path, e.g.
    ``/usr/bin/docker``) in command position followed by an argument --
    at the start of a line or after ``;``, ``&``, ``|``, ``(``, ``{``,
    ``!``, `` ` ``, ``$(``, or the words ``then``, ``do``, ``else``,
    ``exec``, ``if``, ``while``, ``until``, ``command``, ``time``;
  * a variable whose name mentions an engine (``ENGINE``, ``DOCKER``,
    ``PODMAN``, ``_EXE``; any case) in command position followed by an
    engine subcommand (``build``, ``run``, ``pull``, ``info``, ...).

Python files:

  * an argument list starting with the literal ``"docker"`` or ``"podman"``
    (``subprocess.run(["docker", ...])``), or an ``os.system`` /
    ``subprocess`` call whose command string starts with ``docker `` or
    ``podman ``.

A probe such as ``command -v docker`` or ``shutil.which("docker")`` is not
a call and is never reported.

OUTPUT
------
One line per finding, then a totals line:

    lint_docker_helper_path: <file>:<line>: <what was found> -- <fix>
    lint_docker_helper_path: totals: <N> file(s) scanned; <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (keys
``lint``, ``severity``, ``kind``, ``file``, ``line``, ``summary``,
``remedy``) in the shape ``team/scripts/lib/gated_runner_lint.sh``'s
``gated_runner_merge_findings_file`` merges; ``[]`` when clean.

USAGE
-----
  python3 team/scripts/lint_docker_helper_path.py [--root PATH] [--findings-file PATH]

  --root PATH           Repository root to scan (default: two levels above
                        this script's directory).
  --findings-file PATH  Write findings as a JSON array to PATH.

EXIT CODES
----------
  0   Always, whatever was found (quality class).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

LINT_NAME = "docker_helper_path"
SCAN_TOP = "team"
HELPER_REL = "team/scripts/lib/docker_use.sh"
TEST_DIR_NAMES = frozenset({"tests", "test"})
FIX = (
    "route it through pgai_docker (source team/scripts/lib/docker_use.sh, "
    "then: pgai_docker <engine> <subcommand> ...) so the use lands in logs/docker-use.log"
)
REMEDY = "python3 team/scripts/lint_docker_helper_path.py"

_ENGINE_SUBCOMMANDS = (
    "build|run|pull|push|create|start|stop|restart|rm|rmi|exec|image|images|"
    "container|ps|info|version|compose|tag|inspect|logs|kill|load|save|"
    "network|volume|system|login|logout|events|cp|commit|buildx|stats|top|wait"
)

# What may precede a command word on a shell line.
_CMD_POSITION = (
    r"(?:^|[;&|({!`]|\$\(|\b(?:then|do|else|exec|if|while|until|command|time)\b)\s*"
)
_LITERAL_ENGINE_RE = re.compile(
    _CMD_POSITION + r"((?:[\w./-]*/)?(?:docker|podman))\s+[\w-]"
)
_VARIABLE_ENGINE_RE = re.compile(
    _CMD_POSITION
    + r"(\$\{?[A-Za-z_]*(?:ENGINE|DOCKER|PODMAN|_EXE)[A-Za-z0-9_]*\}?)\s+(?:"
    + _ENGINE_SUBCOMMANDS
    + r")\b",
    re.IGNORECASE,
)

_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_]\w*))")
_LONE_VAR_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")

_PY_LIST_RE = re.compile(r"""\[\s*[rbuf]?["'](?:[\w./-]*/)?(docker|podman)["']""")
_PY_STRING_CALL_RE = re.compile(
    r"""(?:os\.system|os\.popen|subprocess\.\w+|Popen)\(\s*[rbuf]?["'](docker|podman)\s"""
)


class Finding(NamedTuple):
    """One direct engine call."""

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
            "kind": "direct-engine-call",
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": REMEDY,
        }


def _blank_quoted(line: str) -> str:
    """Return *line* with quoted text blanked, keeping quoted lone variables."""

    def _double(match: re.Match) -> str:
        inner = match.group(1)
        return inner if _LONE_VAR_RE.match(inner) else '""'

    line = re.sub(r'"((?:[^"\\]|\\.)*)"', _double, line)
    return re.sub(r"'[^']*'", "''", line)


def scan_shell(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, description)`` for each direct call in shell *text*."""
    hits: list[tuple[int, str]] = []
    heredoc_end: str | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        if heredoc_end is not None:
            if raw.strip() == heredoc_end:
                heredoc_end = None
            continue
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code = _blank_quoted(raw)
        # The heredoc opener is read from the raw line: its delimiter is
        # often quoted (<<'EOF'), which the blanked copy no longer carries.
        opener = _HEREDOC_RE.search(raw) if "<<" in raw else None
        literal = _LITERAL_ENGINE_RE.search(code)
        variable = _VARIABLE_ENGINE_RE.search(code)
        # A command substitution inside a double-quoted string
        # ("$(docker images ...)") is blanked above with the rest of the
        # string, so the text after each "$(" is checked on its own too.
        for start in [m.end() for m in re.finditer(r"\$\(", raw)]:
            if literal or variable:
                break
            inner = _blank_quoted(raw[start:])
            literal = _LITERAL_ENGINE_RE.match(inner)
            variable = _VARIABLE_ENGINE_RE.match(inner)
        if literal:
            hits.append((number, f"calls '{literal.group(1)}' directly: {stripped[:120]}"))
        elif variable:
            hits.append((number, f"runs engine variable '{variable.group(1)}' directly: {stripped[:120]}"))
        if opener is not None:
            heredoc_end = next(g for g in opener.groups() if g)
    return hits


def _engine_word(node: ast.AST) -> str | None:
    """Return "docker"/"podman" when *node* is a string naming an engine command."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        first = node.value.strip().split(" ", 1)[0]
        name = first.rsplit("/", 1)[-1]
        if name in ("docker", "podman"):
            return name
    return None


def scan_python(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, description)`` for each direct call in Python *text*.

    Only real call sites count: the module is parsed and every call's first
    argument is checked -- an argument list whose first element names an
    engine, or a command string that starts with one. Docstrings and
    comments that merely mention docker are never reported. A file that
    does not parse falls back to a line-by-line pattern check.
    """
    hits: list[tuple[int, str]] = []
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        for number, raw in enumerate(lines, start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _PY_LIST_RE.search(raw) or _PY_STRING_CALL_RE.search(raw)
            if match:
                hits.append((number, f"invokes '{match.group(1)}' directly: {stripped[:120]}"))
        return hits
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        word = None
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            word = _engine_word(first.elts[0])
        elif isinstance(first, ast.Constant):
            value = first.value if isinstance(first.value, str) else ""
            if " " in value.strip():
                word = _engine_word(first)
        if word:
            source = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
            hits.append((node.lineno, f"invokes '{word}' directly: {source[:120]}"))
    return sorted(hits)


def _scanned_files(root: Path) -> list[Path]:
    top = root / SCAN_TOP
    if not top.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(top.rglob("*")):
        if path.suffix not in (".sh", ".py") or not path.is_file():
            continue
        rel_parts = path.relative_to(top).parts[:-1]
        if any(part in TEST_DIR_NAMES for part in rel_parts):
            continue
        if path.relative_to(root).as_posix() == HELPER_REL:
            continue
        files.append(path)
    return files


def run(root: Path) -> tuple[int, list[Finding]]:
    """Scan *root*; return ``(files_scanned, findings)``."""
    findings: list[Finding] = []
    files = _scanned_files(root)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = scan_shell(text) if path.suffix == ".sh" else scan_python(text)
        rel = path.relative_to(root).as_posix()
        findings.extend(Finding(rel, number, desc) for number, desc in hits)
    return len(files), findings


def main(argv: list[str] | None = None) -> int:
    """Run the docker-path lint, printing each direct engine call and writing ``--findings-file``.

    Exit codes: 0 always, whatever was found (quality class); 2 usage error (argparse).
    """
    parser = argparse.ArgumentParser(
        description="Report container-engine calls that bypass pgai_docker (quality class; always exits 0)."
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="repository root to scan (default: this checkout)",
    )
    parser.add_argument("--findings-file", default=None, help="write findings as a JSON array here")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    scanned, findings = run(root)
    for finding in findings:
        print(f"{LINT_NAME}: {finding.file}:{finding.line}: {finding.summary} -- {FIX}")
    print(f"lint_{LINT_NAME}: totals: {scanned} file(s) scanned; {len(findings)} finding(s)")

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

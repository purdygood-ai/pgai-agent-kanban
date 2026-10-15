"""
test_printed_script_paths_exist.py
==================================
Printed text in the kanban's own code names only scripts that exist.

Usage text, --help output, echo/printf lines, error remedies and the strings
Python code prints tell an operator what to run next. A script name there
that no longer exists sends the operator to a file that is not there -- the
retired flat names (``cm-release.sh``, ``wake-claude.sh`` ...) lingered in
exactly those places after the scripts moved under ``cm/`` and ``wake/``.
Documentation citations are covered separately by
team/scripts/lint_doc_command_truth.py; this check covers code.

What counts as printed text
---------------------------
* Bash (``*.sh`` and extensionless bash scripts): every line that is not a
  ``#`` comment, plus every heredoc body (usage blocks are heredocs).
  Comments are not printed and are not checked -- except in a script whose
  ``--help`` is rendered from its own comments. ``grep '^#'`` over the
  script itself prints every column-0 comment line, so all of them are
  checked; any other self-read with a ``^#`` pattern (``awk``/``sed`` over
  ``"$0"`` or ``"${BASH_SOURCE[0]}"``) is taken to print the leading
  comment block, so that block is checked. The ``#!`` shebang is never help.
* Python: string literals passed to a printing or reporting call
  (:data:`_PRINTING_CALLS` -- print, write, logging, argparse help, the
  error-contract emitters, list building for printed output), raised
  exception messages, and returned f-strings. Docstrings and data literals
  (filename allowlists, suffix tests) are not printed and are not checked.

What counts as a script reference, and when it exists
-----------------------------------------------------
A token ending in ``.sh`` or ``.py`` that is not part of a longer,
variable-built path (``${LIB}/x.sh``, ``$ROOT/scripts/x.sh``). A bare name
exists when some file in the repository has that name; a name with
directories exists when some file's repository path ends with it, so
``scripts/cm/release.sh`` (the live-install path), ``cm/release.sh`` and
``team/scripts/cm/release.sh`` all resolve. Pattern placeholders
(``migrate_vX.Y.Z_to_vA.B.C.sh``) are skipped, and the few names of files
the kanban writes into other trees are listed in :data:`_GENERATED_NAMES`.

The scanner is exercised twice: on the real tree (no finding) and on a
seeded fixture tree (the missing names are reported, nothing else).
"""

from __future__ import annotations

import ast
import os
import pathlib
import posixpath
import re
import subprocess

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_SCANNED_DIRS = ("team/scripts", "team/pgai_agent_kanban")
_SKIPPED_PARTS = {"tests", "__pycache__", ".git"}

#: A script reference: a .sh/.py token not glued to a longer path or variable.
_TOKEN_RE = re.compile(r"(?<![\w${}/.~@*<>-])([\w.](?:[\w.-]*/)*[\w.-]*\.(?:sh|py))(?![\w/-])")
_SCRIPT_SEGMENT_RE = re.compile(r"^[\w-][\w.-]*\.(?:sh|py)$")
_PLACEHOLDER_RE = re.compile(r"X\.Y\.Z|vA\.B\.C|NNN|YYYY")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")

#: The script reading its own source: "$0" or "${BASH_SOURCE[0]}", double-quoted.
_SELF_SOURCE = r'"\$(?:0|\{BASH_SOURCE\[0\]\})"'
#: ``grep '^#'`` over the script itself: column-0 comment lines are printed.
_WHOLE_FILE_COMMENT_HELP_RE = re.compile(
    r"""grep\s+(?:-(?![A-Za-z]*v)\S+\s+)*['"]\^#['"]\s+""" + _SELF_SOURCE + r".*"
)
#: Bounds a renderer may put on that grep's output, 1-based where sed/head count.
_SED_RANGE_RE = re.compile(r"""sed\s+-n\s+['"]?(\d+)\s*,\s*(\d+)\s*p""")
_HEAD_COUNT_RE = re.compile(r"head\s+(?:-n\s*)?-?(\d+)")
_ARRAY_SLICE_RE = re.compile(r"\[@\]:(\d+):(\d+)\}")
_SHEBANG_FILTER_RE = re.compile(r"""grep\s+-v\s+['"]\^#!""")
#: Any other self-read with a ``^#`` pattern on one line (awk/sed/head piped
#: through a ``^#`` filter): the leading comment block is printed.
_HEADER_COMMENT_HELP_LINE_RE = re.compile(r"\^#.*" + _SELF_SOURCE + "|" + _SELF_SOURCE + r".*\^#")
#: A multi-line single-quoted awk program with a ``^#`` rule, run over the script.
_HEADER_COMMENT_HELP_AWK_RE = re.compile(r"awk\s+'[^']*\^#[^']*'\s*" + _SELF_SOURCE)

#: Callees whose string arguments are printed output.
_PRINTING_CALLS = frozenset({
    "print", "write", "say", "log", "_log", "warn", "_warn", "warning", "error", "info",
    "debug", "critical", "exception", "emit_error_contract", "format_error_contract",
    "add_argument", "add_parser", "ArgumentParser", "exit", "fail", "die", "_die", "echo",
    "append", "extend", "_print_line", "format",
})

#: Names of files the kanban writes into other trees, so absent here by design.
_GENERATED_NAMES = {
    "contract_check.sh": "written into each new workflow plugin by pgai_agent_kanban/workflows/create_new_workflow.py",
    "hello.py": "written into the hello-world sample project by scripts/harness/helloworld-flow-check.sh",
    "test_hello.py": "written into the hello-world sample project by scripts/harness/helloworld-flow-check.sh",
}

#: (file, token) pairs that are labelled examples of a path, not a reference.
_EXAMPLE_REFERENCES = {
    ("team/scripts/cleanup/test-stale-artifact-branches.sh", "team/scripts/some-script.sh"):
        "fixture task README text for the smoke test",
}

_RETIRED_NAMES_RE = re.compile(
    r"(cm-(release|cancel-rc|open-doc|open-rc|finalize-release)\.sh|wake-claude\.sh)([^a-zA-Z0-9_-]|$)"
)


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------


def _is_bash(path: pathlib.Path) -> bool:
    if path.suffix == ".sh":
        return True
    if path.suffix:
        return False
    try:
        head = path.read_bytes()[:64]
    except OSError:
        return False
    return head.startswith(b"#!") and b"bash" in head.split(b"\n", 1)[0]


def _comment_help_lines(text: str) -> list[tuple[int, str]]:
    """The comment lines a script's ``--help`` prints from its own source.

    Detection reads code lines only, so a comment that merely describes a
    renderer does not make its file one.
    """
    lines = text.splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    whole = _WHOLE_FILE_COMMENT_HELP_RE.search(code)
    if whole:
        # What grep emits, then the slice the renderer prints of it.
        printed = [(n, line) for n, line in enumerate(lines, 1) if line.startswith("#")]
        if _SHEBANG_FILTER_RE.search(whole.group(0)):
            printed = [(n, line) for n, line in printed if not line.startswith("#!")]
        sed_range = _SED_RANGE_RE.search(whole.group(0))
        head_count = _HEAD_COUNT_RE.search(whole.group(0))
        array_slice = _ARRAY_SLICE_RE.search(code)
        if sed_range:
            printed = printed[int(sed_range.group(1)) - 1:int(sed_range.group(2))]
        elif head_count:
            printed = printed[:int(head_count.group(1))]
        elif array_slice:
            start = int(array_slice.group(1))
            printed = printed[start:start + int(array_slice.group(2))]
    elif _HEADER_COMMENT_HELP_LINE_RE.search(code) or _HEADER_COMMENT_HELP_AWK_RE.search(code):
        printed = []
        for number, line in enumerate(lines, 1):
            if not line.startswith("#"):
                break
            printed.append((number, line))
    else:
        return []
    return [(n, re.sub(r"^# ?", "", line)) for n, line in printed if not line.startswith("#!")]


def _bash_printed_lines(text: str) -> list[tuple[int, str]]:
    """Non-comment lines, heredoc bodies and comment-rendered help, with line numbers."""
    out: list[tuple[int, str]] = _comment_help_lines(text)
    heredoc_end: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            else:
                out.append((number, line))
            continue
        if line.lstrip().startswith("#"):
            continue
        out.append((number, line))
        match = _HEREDOC_RE.search(line)
        if match and "<<<" not in line:
            heredoc_end = match.group(1)
    return out


def _strings_in(node: ast.AST) -> list[tuple[int, str]]:
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            for offset, line in enumerate(sub.value.splitlines() or [""]):
                found.append((sub.lineno + offset, line))
    return found


def _python_printed_lines(text: str) -> list[tuple[int, str]]:
    """Strings printed by a call in _PRINTING_CALLS, raised, or returned as f-strings."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _PRINTING_CALLS:
                for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                    out.extend(_strings_in(arg))
        elif isinstance(node, ast.Raise) and node.exc is not None:
            out.extend(_strings_in(node.exc))
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.JoinedStr):
            out.extend(_strings_in(node.value))
    return out


def _tree_index(root: pathlib.Path) -> tuple[set[str], set[str]]:
    """(basenames, repository-relative paths) of every file under *root*."""
    names: set[str] = set()
    paths: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
        rel_dir = os.path.relpath(dirpath, root)
        for filename in filenames:
            names.add(filename)
            paths.add(filename if rel_dir == "." else f"{rel_dir}/{filename}")
    return names, paths


def _references(token: str) -> list[str]:
    """The script references in *token*; ``a.sh/b.sh`` reads as two."""
    segments = token.split("/")
    if any(_SCRIPT_SEGMENT_RE.match(s) for s in segments[:-1]):
        return [s for s in segments if _SCRIPT_SEGMENT_RE.match(s)]
    return [token] if _SCRIPT_SEGMENT_RE.match(segments[-1]) else []


def _exists(reference: str, names: set[str], paths: set[str], rel_dir: str = "") -> bool:
    if "/" not in reference:
        return reference in names
    if reference.startswith("../"):
        # Relative to the naming file (``# shellcheck source=../lib/x.sh``).
        return posixpath.normpath(posixpath.join(rel_dir, reference)) in paths
    tail = reference[2:] if reference.startswith("./") else reference
    return any(path == tail or path.endswith("/" + tail) for path in paths)


def find_missing_script_references(root: pathlib.Path) -> list[str]:
    """``file:line: name`` for every printed script name under *root* that does not exist."""
    names, paths = _tree_index(root)
    findings: list[str] = []
    for scanned in _SCANNED_DIRS:
        base = root / scanned
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or _SKIPPED_PARTS.intersection(path.relative_to(root).parts):
                continue
            if path.suffix == ".py":
                lines = _python_printed_lines(path.read_text(encoding="utf-8", errors="replace"))
            elif _is_bash(path):
                lines = _bash_printed_lines(path.read_text(encoding="utf-8", errors="replace"))
            else:
                continue
            rel = path.relative_to(root).as_posix()
            for number, line in lines:
                for match in _TOKEN_RE.finditer(line):
                    for reference in _references(match.group(1)):
                        if (
                            _PLACEHOLDER_RE.search(reference)
                            or os.path.basename(reference) in _GENERATED_NAMES
                            or (rel, reference) in _EXAMPLE_REFERENCES
                            or _exists(reference, names, paths, posixpath.dirname(rel))
                        ):
                            continue
                        findings.append(f"{rel}:{number}: {reference}")
    return sorted(set(findings))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_printed_text_names_only_scripts_that_exist():
    findings = find_missing_script_references(_REPO_ROOT)

    assert findings == [], (
        "printed text names script(s) that do not exist in the tree -- point each at the "
        "script's real path (e.g. scripts/cm/release.sh), or, for a file the kanban writes "
        "into another tree, add it to _GENERATED_NAMES with the writer named:\n  "
        + "\n  ".join(findings)
    )


def test_a_seeded_reference_to_a_missing_script_is_reported(tmp_path):
    scripts = tmp_path / "team" / "scripts"
    (scripts / "cm").mkdir(parents=True)
    (scripts / "cm" / "release.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts / "seeded.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# cm-release.sh in a header comment is not printed\n"
        'echo "re-run cm-release.sh to finish"\n'
        'echo "re-run scripts/cm/release.sh to finish"\n'
        'source "${LIB_DIR}/missing_lib.sh"\n'
        "cat <<EOF\n"
        "Usage: team/scripts/cancel-rc.sh --project NAME\n"
        "EOF\n",
        encoding="utf-8",
    )
    package = tmp_path / "team" / "pgai_agent_kanban"
    package.mkdir(parents=True)
    (package / "seeded.py").write_text(
        '"""Docstring naming gone.py is not printed."""\n'
        "import sys\n"
        'SUFFIXES = ("_test.py",)\n'
        'print("run scripts/never-there.sh next", file=sys.stderr)\n'
        'raise SystemExit("see cm/release.sh")\n',
        encoding="utf-8",
    )

    findings = find_missing_script_references(tmp_path)

    assert findings == [
        "team/pgai_agent_kanban/seeded.py:4: scripts/never-there.sh",
        "team/scripts/seeded.sh:3: cm-release.sh",
        "team/scripts/seeded.sh:7: team/scripts/cancel-rc.sh",
    ]


def test_a_seeded_comment_rendered_help_naming_a_missing_script_is_reported(tmp_path):
    """A script whose --help prints its own comments has those comments
    checked: the printed slice of a ``grep '^#'`` renderer, and the leading
    block of an awk renderer. Comments past the printed slice, body comments
    under a leading-block renderer, and comments in a script that renders no
    help from them stay unchecked."""
    scripts = tmp_path / "team" / "scripts"
    (scripts / "dashboard").mkdir(parents=True)
    (scripts / "lib").mkdir()
    (scripts / "lib" / "real_lib.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts / "dashboard" / "sliced.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# dashboard-sliced.sh\n"
        "# Usage: sliced.sh, started by dashboard/sliced.sh\n"
        "# shellcheck source=../lib/real_lib.sh\n"
        "# last printed line\n"
        "set -euo pipefail\n"
        'case "${1:-}" in\n'
        "  -h|--help)\n"
        "    mapfile -t __help_lines < <(grep '^#' \"$0\" | grep -v '^#!' | sed 's/^# \\{0,1\\}//')\n"
        "    printf '%s\\n' \"${__help_lines[@]:0:4}\"\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "# past-the-slice.sh is in no printed line\n",
        encoding="utf-8",
    )
    (scripts / "leading.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# gone-from-header.sh\n"
        "set -euo pipefail\n"
        "# body-comment-only.sh is not in the leading block\n"
        "mapfile -t lines < <(awk '\n"
        "  /^#!/ { next }\n"
        "  /^#/ { sub(/^# ?/, \"\"); print; next }\n"
        "  { exit }\n"
        "' \"$0\")\n",
        encoding="utf-8",
    )
    (scripts / "unrendered.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# never-printed.sh -- this script's help is a heredoc, and a comment\n"
        "# describing grep '^#' \"$0\" does not make it a comment renderer\n"
        "echo ok\n",
        encoding="utf-8",
    )

    findings = find_missing_script_references(tmp_path)

    assert findings == [
        "team/scripts/dashboard/sliced.sh:2: dashboard-sliced.sh",
        "team/scripts/leading.sh:2: gone-from-header.sh",
    ]


def test_retired_flat_script_names_are_gone_from_code():
    """The retired cm-*.sh / wake-claude.sh names appear nowhere in code --
    comments included -- except lint_doc_command_truth.py's docstring, whose
    never-existing example is the point of that lint."""
    hits = []
    for target in ("team/scripts", "team/pgai_agent_kanban", "install.sh", "project.cfg_example"):
        path = _REPO_ROOT / target
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for file in files:
            if not file.is_file() or _SKIPPED_PARTS.intersection(file.relative_to(_REPO_ROOT).parts):
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if _RETIRED_NAMES_RE.search(line):
                    hits.append(f"{file.relative_to(_REPO_ROOT).as_posix()}:{number}")

    assert len(hits) == 1 and hits[0].startswith("team/scripts/lint_doc_command_truth.py:"), hits


def test_cancel_rc_help_points_at_unwind_rc(tmp_path):
    root = tmp_path / "kanban"
    (root / "tmp").mkdir(parents=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    result = subprocess.run(
        ["bash", str(_TEAM_DIR / "scripts" / "cm" / "cancel-rc.sh"), "--help"],
        capture_output=True, text=True, timeout=30, env=env, cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "scripts/unwind-rc.sh" in result.stdout + result.stderr

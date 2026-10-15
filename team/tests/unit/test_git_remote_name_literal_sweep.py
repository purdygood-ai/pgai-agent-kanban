"""
test_git_remote_name_literal_sweep.py
=====================================
Static check: the release machinery that talks to a project's git remote
names it through the shared resolver (``pp_git_remote_name`` /
``pgai_agent_kanban.lib.git_remote``), never by spelling ``origin``.

Over every file that pushes, fetches, runs ``ls-remote`` or reads a
remote-tracking ref for a project's release (the list below), no code line
may

  - pass a literal ``origin`` to ``git push`` / ``fetch`` / ``ls-remote``, or
    narrate one of those commands with ``origin`` in it (a ``push``,
    ``fetch`` or ``ls-remote`` word and the word ``origin`` on one line);
  - build a ref from a literal ``origin/`` (``origin/$MAIN_BRANCH``,
    ``f"origin/{main_branch}"``, ``refs/remotes/origin/...``);
  - print a ``push_to_remote=false`` narration line that says ``origin``
    rather than the configured remote;
  - narrate anything else with ``origin`` in it (an ``echo`` or ``printf``
    line naming ``origin``: "pushed to origin", "deleted from origin",
    "Fetching origin and tags").

Comments, Python docstrings and the resolvers' own ``origin`` fallback are
not code lines for this purpose and are skipped. The fallback also appears
as a value rather than narration -- a resolved name compared with
``origin``, or ``origin`` echoed as a function's whole answer -- and those
two idioms are not narration either. A planted violation in a scratch copy
proves the scan fires.
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import tokenize

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

# Every file that names a project's remote for a release operation.
_FILES = (
    "scripts/lib/project_paths.sh",
    "scripts/lib/cm/release_finalize.sh",
    "scripts/lib/cm/release_squash.sh",
    "scripts/cm/open-rc.sh",
    "scripts/cm/push-watchdog.sh",
    "scripts/lib/overwatch-checks/check-push-lag.sh",
    "scripts/dashboard/git-status.sh",
    "scripts/dashboard/lib/version.sh",
    "scripts/init-project-git-repo.sh",
    "scripts/lib/cm/release_preflight.sh",
    "scripts/lib/cm/release_origin_summary.sh",
    "scripts/cm/finalize-release.sh",
    "scripts/cm/cancel-rc.sh",
    "pgai_agent_kanban/cm/ship.py",
    "pgai_agent_kanban/cm/cancel.py",
    "pgai_agent_kanban/cm/unwind.py",
    "pgai_agent_kanban/lib/released_version.py",
    "pgai_agent_kanban/lib/git_remote.py",
)

_RULES = (
    ("git network command or its narration names origin", re.compile(r"\b(push|fetch|ls-remote)\b.*\borigin\b|\borigin\b.*\b(push|fetch|ls-remote)\b")),
    ("ref built from a literal origin/", re.compile(r"\borigin/")),
    ("push_to_remote=false narration names origin", re.compile(r"push_to_remote=false.*\borigin\b")),
)

# Bash narration: an echo or printf line that names origin, checked with the
# fallback idioms below removed first.
_NARRATION_RULE = ("narration names origin", re.compile(r"\b(echo|printf)\b.*\borigin\b"))

# The resolver's fallback written as a value, not narration: a resolved name
# compared with origin (``[[ "$remote" == origin ]]``), or origin echoed as a
# function's whole answer (``echo "origin "``).
_FALLBACK_IDIOMS = re.compile(r'==\s*"?origin"?|\becho\s+"?origin\s*"?\s*$')


def _bash_code_lines(text: str) -> list[tuple[int, str]]:
    """Every line of a bash file except comment lines."""
    return [(n, line) for n, line in enumerate(text.splitlines(), 1) if not line.lstrip().startswith("#")]


def _python_code_lines(text: str) -> list[tuple[int, str]]:
    """Every line of a Python file with comments removed and docstrings dropped."""
    docstring_lines: set[int] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstring_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    lines = text.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            row, col = token.start
            lines[row - 1] = lines[row - 1][:col]
    return [(n, line) for n, line in enumerate(lines, 1) if n not in docstring_lines]


def _violations(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = _python_code_lines(text) if path.suffix == ".py" else _bash_code_lines(text)
    found = []
    for number, line in lines:
        for label, pattern in _RULES:
            if pattern.search(line):
                found.append(f"{path.name}:{number}: {label}: {line.strip()}")
        if path.suffix != ".py":
            label, pattern = _NARRATION_RULE
            if pattern.search(_FALLBACK_IDIOMS.sub("", line)):
                found.append(f"{path.name}:{number}: {label}: {line.strip()}")
    return found


@pytest.mark.parametrize("relative", _FILES)
def test_no_remote_operation_spells_origin(relative: str) -> None:
    path = _TEAM_DIR / relative
    assert path.is_file(), f"{relative} moved; update this test's file list"
    assert _violations(path) == [], (
        "name the project's remote through pp_git_remote_name / "
        "pgai_agent_kanban.lib.git_remote instead of a literal origin"
    )


def test_resolvers_keep_origin_as_their_one_fallback() -> None:
    bash = (_TEAM_DIR / "scripts/lib/project_paths.sh").read_text(encoding="utf-8")
    python = (_TEAM_DIR / "pgai_agent_kanban/lib/git_remote.py").read_text(encoding="utf-8")
    assert 'PP_GIT_REMOTE_NAME_DEFAULT="origin"' in bash
    assert 'DEFAULT_REMOTE_NAME = "origin"' in python


@pytest.mark.parametrize(
    ("suffix", "planted"),
    [
        (".sh", 'git -C "$REPO_ROOT" push origin "$MAIN_BRANCH"'),
        (".sh", 'git -C "$REPO_ROOT" merge --ff-only "origin/$MAIN_BRANCH"'),
        (".sh", 'echo "push_to_remote=false — skipping origin operations."'),
        (".sh", 'echo "[Step 1] Fetching origin and tags..."'),
        (".sh", 'echo "  - Tag $RELEASE_TAG created locally and pushed to origin"'),
        (".sh", 'echo "  - $RC_BRANCH deleted from origin; ${_local_part}"'),
        (".sh", 'printf \'%s\\n\' "Confirmed: $RC_BRANCH exists locally and on origin."'),
        (".py", 'git.step("fetch", "origin")'),
        (".py", 'ref = f"origin/{main_branch}"'),
    ],
)
def test_scan_fires_on_a_planted_literal(tmp_path: pathlib.Path, suffix: str, planted: str) -> None:
    clean = tmp_path / f"clean{suffix}"
    clean.write_text(f"# git push origin main -- a comment is not code\nx = 1\n", encoding="utf-8")
    assert _violations(clean) == []
    dirty = tmp_path / f"dirty{suffix}"
    dirty.write_text(f"x = 1\n{planted}\n" if suffix == ".py" else f"{planted}\n", encoding="utf-8")
    assert _violations(dirty), f"the scan missed: {planted}"


@pytest.mark.parametrize(
    "fallback",
    [
        'echo "    $([[ "$_fin_remote" == origin ]] || echo "GIT_REMOTE_NAME=${_fin_remote} ")bash finalize-release.sh"',
        '        echo "origin "',
        '    echo origin',
    ],
)
def test_the_fallback_written_as_a_value_is_not_narration(tmp_path: pathlib.Path, fallback: str) -> None:
    script = tmp_path / "fallback.sh"
    script.write_text(f"{fallback}\n", encoding="utf-8")
    assert _violations(script) == []

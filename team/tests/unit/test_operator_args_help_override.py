"""
test_operator_args_help_override.py
===================================
operator_args_render_help_for_flags (team/scripts/lib/operator_args.sh)
renders one line per declared flag from its built-in descriptions. A script
whose flag means something narrower than the canonical flag declares the
global associative array OPERATOR_FLAG_HELP, and its entry replaces the
built-in line for that flag only:

  - with no OPERATOR_FLAG_HELP declared, the built-in lines render unchanged;
  - an entry replaces exactly its own flag's line, in place, and every other
    flag keeps its built-in line;
  - an indexed (non-associative) array of that name is ignored;
  - close.sh's rendered --help uses the override: --state reads
    done|wont-do, never the task-state list.

Each accepted option is listed once, with a description:

  - the "h" entry a caller declares so that -h parses is shown only on the
    "--help, -h" line, never as a "  --h" line of its own;
  - a flag the caller's extra lines describe (an option-shaped line, or an
    "alias: --FLAG" mention) gets no second, bare line from the renderer;
  - a flag merely mentioned in prose or an example is not described, and a
    flag described nowhere still gets a "(no description)" line rather than
    vanishing;
  - the operator scripts that declare "h" render --help through the shipped
    entry point with no "  --h" line and every other declared flag described.

Every subprocess runs under pytest's tmp_path; nothing is written.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import BashResult, run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_OPERATOR_ARGS = _SCRIPTS_DIR / "lib" / "operator_args.sh"
_CLOSE = _SCRIPTS_DIR / "close.sh"

_BUILTIN_STATE = "  --state STATE      Task state: BACKLOG|WAITING|WORKING|BLOCKED|DONE|WONT-DO"
_BUILTIN_KEY = "  --key KEY          Arbitrary key or identifier"
_BUILTIN_HELP = "  --help, -h         Show this help and exit"
_UNDESCRIBED_NOTE = "  --note             (no description)"

_ARCHIVE_LINE = "  --archive PATH       Archive file to import (required)"

# A line that is nothing but a flag name: the shape a flag without a
# description used to render as.
_BARE_FLAG_LINE = re.compile(r"^\s*--[A-Za-z0-9][A-Za-z0-9_-]*\s*$")

# The operator scripts that declare "h" in OPERATOR_VALID_FLAGS so -h parses.
_SCRIPTS_DECLARING_H = (
    "add-project.sh",
    "create-project.sh",
    "export-kanban-config.sh",
    "export-project.sh",
    "import-kanban-config.sh",
    "import-project.sh",
    "init-project-git-repo.sh",
    "list-rejected.sh",
    "recover-rejected.sh",
    "regenerate-changelog.sh",
)


def _diagnostic(result: BashResult) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _render_flags(tmp_path: pathlib.Path, flags: str, prelude: str = "",
                  extra_lines: tuple[str, ...] = ()) -> list[str]:
    """Render help for FLAGS=(<flags>) with *extra_lines* after the flags block."""
    quoted_extras = " ".join("'" + line.replace("'", "'\\''") + "'" for line in extra_lines)
    result = run_bash(
        tmp_path,
        "set -euo pipefail\n"
        f"source '{_OPERATOR_ARGS}'\n"
        f"{prelude}"
        f"FLAGS=({flags})\n"
        f"operator_args_render_help_for_flags demo.sh 'Demo.' FLAGS {quoted_extras}\n",
    )
    assert result.returncode == 0, _diagnostic(result)
    return result.stdout.splitlines()


def _render(tmp_path: pathlib.Path, prelude: str = "") -> list[str]:
    return _render_flags(tmp_path, "key state note", prelude)


def _options(lines: list[str]) -> list[str]:
    return lines[lines.index("Options:") + 1:]


def _flag_token(flag: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_-])--{re.escape(flag)}(?![A-Za-z0-9_-])")


def _described_lines(lines: list[str], flag: str) -> list[str]:
    """Lines that name --FLAG and carry text beyond the flag name itself."""
    token = _flag_token(flag)
    return [
        line for line in lines
        if token.search(line) and not _BARE_FLAG_LINE.match(line)
        and "(no description)" not in line
    ]


def test_built_in_lines_render_unchanged_without_an_override(tmp_path: pathlib.Path) -> None:
    assert _options(_render(tmp_path)) == [_BUILTIN_KEY, _BUILTIN_STATE, _UNDESCRIBED_NOTE]


def test_an_override_replaces_only_its_own_flag_line(tmp_path: pathlib.Path) -> None:
    override = "  --state STATE      Closed state: done|wont-do (default: done)"
    prelude = f"declare -A OPERATOR_FLAG_HELP=([state]='{override}')\n"

    assert _options(_render(tmp_path, prelude)) == [_BUILTIN_KEY, override, _UNDESCRIBED_NOTE]


def test_an_indexed_array_of_that_name_is_ignored(tmp_path: pathlib.Path) -> None:
    prelude = "OPERATOR_FLAG_HELP=('  --state not used')\n"

    assert _options(_render(tmp_path, prelude)) == [_BUILTIN_KEY, _BUILTIN_STATE, _UNDESCRIBED_NOTE]


def test_close_help_state_line_names_the_closed_states(tmp_path: pathlib.Path) -> None:
    result = run_bash(tmp_path, f"bash '{_CLOSE}' --help\n")

    assert result.returncode == 0, result.stderr
    options = _options(result.stdout.splitlines())
    assert "  --state STATE      Closed state: done|wont-do (default: done)" in options
    assert _BUILTIN_STATE not in options


@pytest.mark.parametrize(
    "prelude,extra_lines",
    [
        ("", ("", _ARCHIVE_LINE)),
        (f"declare -A OPERATOR_FLAG_HELP=([archive]='{_ARCHIVE_LINE}')\n", ()),
    ],
    ids=["described-in-extra-line", "described-by-override"],
)
def test_h_alias_and_described_flags_render_once_with_a_description(
    tmp_path: pathlib.Path, prelude: str, extra_lines: tuple[str, ...]
) -> None:
    lines = _render_flags(tmp_path, "force help archive h", prelude, extra_lines)
    options = _options(lines)

    assert "  --h" not in lines, "\n".join(lines)
    assert not [line for line in options if _BARE_FLAG_LINE.match(line)], "\n".join(lines)
    assert _BUILTIN_HELP in options, "\n".join(lines)
    for flag in ("force", "help", "archive"):
        assert _described_lines(options, flag), (
            f"--{flag} is not listed with a description:\n" + "\n".join(lines)
        )
    assert options.count(_ARCHIVE_LINE) == 1, "\n".join(lines)
    assert len([line for line in options if _flag_token("archive").search(line)]) == 1, (
        "--archive must be listed exactly once:\n" + "\n".join(lines)
    )


def test_an_alias_named_on_another_flags_line_counts_as_described(tmp_path: pathlib.Path) -> None:
    dev_tree_line = "  --dev-tree PATH      dev_tree_path (alias: --dev-tree-path; default: empty)"
    lines = _render_flags(tmp_path, "dev-tree dev-tree-path help", extra_lines=("", dev_tree_line))
    options = _options(lines)

    assert options == [_BUILTIN_HELP, "", dev_tree_line], "\n".join(lines)


def test_a_flag_only_mentioned_in_prose_keeps_its_own_line(tmp_path: pathlib.Path) -> None:
    prose = (
        "",
        "  (an item another item subsumed closes as wont-do with a --note saying why)",
        "Example:",
        "  close.sh --key BUG-NNNN \\",
        "           --note 'subsumed by PRIORITY-NNNN'",
    )
    lines = _render_flags(tmp_path, "note help", extra_lines=prose)

    assert _options(lines)[:2] == [_UNDESCRIBED_NOTE, _BUILTIN_HELP], "\n".join(lines)


def _declared_flags(script: pathlib.Path) -> list[str]:
    match = re.search(r"^OPERATOR_VALID_FLAGS=\(([^)]*)\)", script.read_text(encoding="utf-8"), re.M)
    assert match, f"{script.name} declares no OPERATOR_VALID_FLAGS array"
    return match.group(1).split()


@pytest.mark.parametrize("script_name", _SCRIPTS_DECLARING_H)
def test_operator_script_help_lists_no_h_line_and_describes_every_flag(
    tmp_path: pathlib.Path, script_name: str
) -> None:
    script = _SCRIPTS_DIR / script_name
    flags = _declared_flags(script)
    assert "h" in flags, f"{script_name} no longer declares h; -h would stop parsing"

    root = tmp_path / "kanban_root"
    (root / "tmp").mkdir(parents=True)
    env = build_scrubbed_env(
        home_dir=root,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    foreign_cwd = tmp_path / "cwd"
    foreign_cwd.mkdir()
    result = run_bash(tmp_path, f"bash '{script}' --help\n", base_env=env, cwd=foreign_cwd)

    assert result.returncode == 0, _diagnostic(result)
    lines = (result.stdout + result.stderr).splitlines()
    assert "  --h" not in lines, _diagnostic(result)
    assert not [line for line in lines if _BARE_FLAG_LINE.match(line)], _diagnostic(result)
    for flag in flags:
        if flag == "h":
            continue
        assert _described_lines(lines, flag), (
            f"{script_name} --help lists --{flag} without a description\n" + _diagnostic(result)
        )


def test_import_project_help_lists_archive_once(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban_root"
    (root / "tmp").mkdir(parents=True)
    env = build_scrubbed_env(
        home_dir=root,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    result = run_bash(tmp_path, f"bash '{_SCRIPTS_DIR / 'import-project.sh'}' --help\n", base_env=env)

    assert result.returncode == 0, _diagnostic(result)
    lines = result.stdout.splitlines()
    archive_lines = [line for line in lines if _flag_token("archive").search(line)]
    assert len(archive_lines) == 1, _diagnostic(result)
    assert "Archive file to import" in archive_lines[0], _diagnostic(result)
    for bare in ("  --archive", "  --name", "  --register", "  --h"):
        assert bare not in lines, _diagnostic(result)

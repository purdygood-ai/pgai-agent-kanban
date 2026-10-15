"""
test_operator_help_describes_every_flag.py
==========================================
Every operator script whose --help is rendered by
operator_args_render_help_for_flags (team/scripts/lib/operator_args.sh)
describes every option it accepts.

The renderer prints "  --FLAG             (no description)" for a declared
flag that has no built-in line, no OPERATOR_FLAG_HELP entry and no
describing extra line. That fallback keeps an accepted option from vanishing
from help; it is a safety net, never a shipped help line. For each renderer
caller under team/scripts, run through its shipped entry point in a
throwaway root:

  - --help exits 0;
  - no output line contains "(no description)";
  - every flag in its OPERATOR_VALID_FLAGS array (other than "h", the -h
    alias shown on the "--help, -h" line) still appears as a --FLAG token,
    so describing a flag never came at the cost of dropping it.

Help goes to stdout for most scripts and to stderr for a few
(set-version-ceiling.sh), so both streams are read.

Every subprocess runs under pytest's tmp_path with a scrubbed environment
whose kanban root, temp dir and HOME all point inside it; nothing is
written outside it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import BashResult, run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_RENDERER_LIB = _SCRIPTS_DIR / "lib" / "operator_args.sh"

_FALLBACK_TEXT = "(no description)"

# A call to the renderer on a line that is not a comment.
_RENDERER_CALL = re.compile(r"^[^#\n]*\boperator_args_render_help_for_flags\b", re.M)
_DECLARED_FLAGS = re.compile(r"^\s*OPERATOR_VALID_FLAGS=\(([^)]*)\)", re.M)

# Known renderer callers, several declaring options outside the canonical
# vocabulary; the scan below must find each, so a broken discovery cannot
# pass by finding nothing.
_MUST_BE_DISCOVERED = (
    "wake-now.sh",
    "reset.sh",
    "set-version-ceiling.sh",
    "dashboard/show-queues.sh",
    "close.sh",
    "deep-verify-freshness-check.sh",
    "verdict-report.sh",
    "verify-rc-state.sh",
)


def _renderer_callers() -> list[str]:
    """Paths, relative to team/scripts, of every script calling the renderer."""
    # Intentional pattern-scan: the invariant is "no operator help screen
    # ships the renderer's fallback text", which must hold for every current
    # and future caller of the shared renderer, not a hand-kept list.
    callers = []
    for script in sorted(_SCRIPTS_DIR.rglob("*.sh")):
        if script == _RENDERER_LIB:
            continue
        if _RENDERER_CALL.search(script.read_text(encoding="utf-8", errors="replace")):
            callers.append(script.relative_to(_SCRIPTS_DIR).as_posix())
    return callers


_CALLERS = _renderer_callers()


def _declared_flags(script: pathlib.Path) -> list[str]:
    match = _DECLARED_FLAGS.search(script.read_text(encoding="utf-8"))
    assert match, (
        f"{script.relative_to(_TEAM_DIR)} calls operator_args_render_help_for_flags "
        "but declares no OPERATOR_VALID_FLAGS=(...) array on one line"
    )
    return match.group(1).split()


def _flag_token(flag: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_-])--{re.escape(flag)}(?![A-Za-z0-9_-])")


def _diagnostic(result: BashResult) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run_help(tmp_path: pathlib.Path, script: pathlib.Path) -> BashResult:
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
    return run_bash(tmp_path, f"bash '{script}' --help\n", base_env=env, cwd=foreign_cwd)


def test_discovery_finds_the_renderer_callers() -> None:
    missing = [name for name in _MUST_BE_DISCOVERED if name not in _CALLERS]
    assert not missing, (
        "the renderer-caller scan under team/scripts did not find "
        f"{missing}; found {len(_CALLERS)} caller(s): {_CALLERS}"
    )


@pytest.mark.parametrize("script_name", _CALLERS)
def test_operator_help_describes_every_declared_flag(
    tmp_path: pathlib.Path, script_name: str
) -> None:
    script = _SCRIPTS_DIR / script_name
    flags = _declared_flags(script)

    result = _run_help(tmp_path, script)

    assert result.returncode == 0, f"{script_name} --help exited {result.returncode}\n" + _diagnostic(result)
    lines = (result.stdout + result.stderr).splitlines()
    fallback_lines = [line for line in lines if _FALLBACK_TEXT in line]
    assert not fallback_lines, (
        f"{script_name} --help lists option(s) with no description: {fallback_lines}. "
        "Give each an OPERATOR_FLAG_HELP entry (see close.sh) or an option-shaped "
        "help line (rule 3 in the operator_args_render_help_for_flags header).\n"
        + _diagnostic(result)
    )
    for flag in flags:
        if flag == "h":
            continue
        assert any(_flag_token(flag).search(line) for line in lines), (
            f"{script_name} declares --{flag} in OPERATOR_VALID_FLAGS but its --help "
            "no longer lists it\n" + _diagnostic(result)
        )

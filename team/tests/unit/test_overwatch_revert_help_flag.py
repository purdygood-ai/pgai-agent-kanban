"""
test_overwatch_revert_help_flag.py
==================================
Behavioral unit tests for team/scripts/overwatch-revert.sh's help path.

Locks two contracts:

  1. --help and -h print the complete usage summary to stdout and exit 0,
     naming every flag the script accepts, without a --project or any
     backup on disk -- the doc-truth lint runs every documented command's
     --help and reads its exit status.
  2. A usage error (no arguments, an unknown option) still prints the
     usage summary to stderr and exits 1, so the help path cannot swallow
     bad input into a successful exit.

Every invocation runs with PGAI_AGENT_KANBAN_ROOT_PATH and KANBAN_ROOT
redirected to an empty tmp_path sandbox, so a regression that lets --help
fall through to a restore is contained to the sandbox.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_SCRIPT = "scripts/overwatch-revert.sh"

_EVERY_FLAG = ("--list", "--show", "--project", "--kanban-root", "--dry-run", "--help")


def _sandbox_env(root: pathlib.Path) -> dict[str, str]:
    return {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "temp"),
        "OVERWATCH_PROJECT": "",
    }


def _files_under(root: pathlib.Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_usage_to_stdout_and_exits_zero(tmp_path: pathlib.Path, flag: str) -> None:
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()
    before = _files_under(sandbox)

    result = run_bash(tmp_path, f"bash {_SCRIPT} {flag}", extra_env=_sandbox_env(sandbox))

    assert result.returncode == 0, f"{flag} exited {result.returncode}; stderr: {result.stderr!r}"
    assert result.stdout.startswith("Usage: overwatch-revert.sh"), result.stdout
    for name in _EVERY_FLAG:
        assert name in result.stdout, f"{flag} output does not describe {name}:\n{result.stdout}"
    assert _files_under(sandbox) - before <= {"temp"}, "the help path must not touch the kanban root"


@pytest.mark.parametrize("args", ["", "--no-such-flag", "--no-such-flag --help"])
def test_usage_error_prints_usage_to_stderr_and_exits_one(tmp_path: pathlib.Path, args: str) -> None:
    """No arguments, or an unknown option read before any --help, is a
    usage error: usage on stderr, nothing on stdout, exit 1."""
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()

    result = run_bash(tmp_path, f"bash {_SCRIPT} {args}", extra_env=_sandbox_env(sandbox))

    assert result.returncode == 1, f"exited {result.returncode}; stderr: {result.stderr!r}"
    assert "Usage: overwatch-revert.sh" in result.stderr
    assert result.stdout == ""

"""
test_argparse_bash.py
=====================
Behavioral unit tests for team/scripts/lib/argparse.sh.

Tests source the shell script and invoke argparse_parse / argparse_has /
argparse_missing / argparse_reset via the bash harness, asserting on the
ARGPARSE_FLAGS, ARGPARSE_POSITIONAL, and ARGPARSE_MISSING output arrays.

Key behavioral requirements:
  - argparse_parse: populates ARGPARSE_FLAGS, ARGPARSE_POSITIONAL, ARGPARSE_MISSING
  - --flag=value and --flag value produce identical ARGPARSE_FLAGS entries
  - Unknown flags are recorded, not rejected
  - Value-taking flags with no value go into ARGPARSE_MISSING
  - -- signals end of flags; remaining tokens are positional
  - Boolean flags (not in --value-flags) get value "1"
  - argparse_has FLAG: returns 0 when flag is present
  - argparse_missing FLAG: returns 0 when flag is in ARGPARSE_MISSING
  - argparse_reset: clears all three arrays
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/argparse.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources argparse.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


# ---------------------------------------------------------------------------
# argparse_parse — basic flag parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        (
            "argparse_parse --dry-run && echo ${ARGPARSE_FLAGS[dry-run]}",
            "1",
        ),  # standalone --flag (not in value-flags) gets value '1'
        (
            "argparse_parse --value-flags 'project' -- --project=myproject"
            " && echo ${ARGPARSE_FLAGS[project]}",
            "myproject",
        ),  # --flag=value form
        (
            "argparse_parse --value-flags 'project' -- --project myproject"
            " && echo ${ARGPARSE_FLAGS[project]}",
            "myproject",
        ),  # --flag value (space-separated) form
        (
            "argparse_parse --value-flags 'foo' -- --foo a=b"
            " && echo ${ARGPARSE_FLAGS[foo]}",
            "a=b",
        ),  # values containing '=' are preserved verbatim
        (
            "argparse_parse -- --some-unknown-flag"
            " && echo ${ARGPARSE_FLAGS[some-unknown-flag]:-MISSING}",
            "1",
        ),  # unknown flags are recorded, not rejected
        (
            "argparse_parse alpha beta gamma"
            " && echo ${ARGPARSE_POSITIONAL[0]} ${ARGPARSE_POSITIONAL[1]} ${ARGPARSE_POSITIONAL[2]}",
            "alpha beta gamma",
        ),  # non-flag tokens appear in ARGPARSE_POSITIONAL in input order
        (
            "argparse_parse --value-flags 'project' -- --project"
            " && echo ${ARGPARSE_MISSING[0]}",
            "project",
        ),  # value-taking flag with no value (end of argv) goes into ARGPARSE_MISSING
        (
            "argparse_parse --value-flags 'project color' -- --project --color red"
            " && echo ${ARGPARSE_MISSING[0]}",
            "project",
        ),  # value-taking flag whose next token is another flag goes into ARGPARSE_MISSING
        (
            "argparse_parse --value-flags 'project agent' -- --project foo --agent coder --verbose"
            " && echo ${ARGPARSE_FLAGS[project]} ${ARGPARSE_FLAGS[agent]} ${ARGPARSE_FLAGS[verbose]}",
            "foo coder 1",
        ),  # multiple flags in a single argparse_parse call are all recorded
        (
            "argparse_parse --dry-run"
            " && argparse_has dry-run && echo yes || echo no",
            "yes",
        ),  # argparse_has returns 0 (true) when the flag was present in argv
        (
            "argparse_parse --some-flag"
            " && argparse_has absent-flag && echo yes || echo no",
            "no",
        ),  # argparse_has returns 1 (false) when the flag was not in argv
        (
            "argparse_parse --value-flags 'project' -- --project"
            " && argparse_missing project && echo yes || echo no",
            "yes",
        ),  # argparse_missing returns 0 when the flag was declared value-taking but got no value
        (
            "argparse_parse --value-flags 'project' -- --project myproj"
            " && argparse_missing project && echo yes || echo no",
            "no",
        ),  # argparse_missing returns 1 when the flag was present with a value
    ],
)
def test_argparse_parse_behaviors(tmp_path: pathlib.Path, command: str, expected: str) -> None:
    """argparse_parse/has/missing behaviors across flag forms, unknown flags, positionals, and value presence."""
    result = run_bash(tmp_path, _source(command))
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_value_flag_equals_and_space_forms_produce_same_result(tmp_path: pathlib.Path) -> None:
    """--flag=value and --flag value produce identical ARGPARSE_FLAGS entries."""
    result_eq = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'dev-tree' -- --dev-tree=/path/to/tree"
            " && echo ${ARGPARSE_FLAGS[dev-tree]}"
        ),
    )
    result_sp = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'dev-tree' -- --dev-tree /path/to/tree"
            " && echo ${ARGPARSE_FLAGS[dev-tree]}"
        ),
    )
    assert result_eq.stdout.strip() == "/path/to/tree"
    assert result_sp.stdout.strip() == "/path/to/tree"


def test_double_dash_signals_end_of_flags(tmp_path: pathlib.Path) -> None:
    """Tokens after -- are treated as positional arguments, not flags."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse -- -- --not-a-flag pos1"
            " && echo ${ARGPARSE_POSITIONAL[0]} ${ARGPARSE_POSITIONAL[1]:-}"
        ),
    )
    assert result.returncode == 0
    out = result.stdout.strip()
    assert "--not-a-flag" in out
    assert "pos1" in out


def test_empty_value_via_equals_not_missing(tmp_path: pathlib.Path) -> None:
    """--flag= (explicit empty value via '=') is NOT placed into ARGPARSE_MISSING."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project' -- --project="
            " && echo MISSING:${#ARGPARSE_MISSING[@]} VAL:${ARGPARSE_FLAGS[project]:-NOKEY}"
        ),
    )
    assert result.returncode == 0
    out = result.stdout.strip()
    assert "MISSING:0" in out
    # The key should exist with an empty value (not "NOKEY")
    assert "VAL:" in out


# ---------------------------------------------------------------------------
# argparse_reset — clear all arrays
# ---------------------------------------------------------------------------


def test_argparse_reset_clears_flags(tmp_path: pathlib.Path) -> None:
    """argparse_reset clears ARGPARSE_FLAGS so a subsequent parse starts fresh."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --dry-run"
            " && argparse_reset"
            " && echo COUNT:${#ARGPARSE_FLAGS[@]}"
        ),
    )
    assert result.returncode == 0
    assert "COUNT:0" in result.stdout


def test_argparse_reset_clears_positional(tmp_path: pathlib.Path) -> None:
    """argparse_reset clears ARGPARSE_POSITIONAL."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse alpha beta"
            " && argparse_reset"
            " && echo COUNT:${#ARGPARSE_POSITIONAL[@]}"
        ),
    )
    assert result.returncode == 0
    assert "COUNT:0" in result.stdout

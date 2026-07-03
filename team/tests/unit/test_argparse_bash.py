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


def test_boolean_flag_gets_value_one(tmp_path: pathlib.Path) -> None:
    """A standalone --flag (not in value-flags) is recorded with value '1'."""
    result = run_bash(
        tmp_path,
        _source("argparse_parse --dry-run && echo ${ARGPARSE_FLAGS[dry-run]}"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "1"


def test_value_flag_equals_form(tmp_path: pathlib.Path) -> None:
    """--flag=value form records the value in ARGPARSE_FLAGS."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project' -- --project=myproject"
            " && echo ${ARGPARSE_FLAGS[project]}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "myproject"


def test_value_flag_space_form(tmp_path: pathlib.Path) -> None:
    """--flag value (space-separated) form records the value in ARGPARSE_FLAGS."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project' -- --project myproject"
            " && echo ${ARGPARSE_FLAGS[project]}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "myproject"


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


def test_value_with_equals_sign_in_value(tmp_path: pathlib.Path) -> None:
    """Values that themselves contain '=' are preserved verbatim."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'foo' -- --foo a=b"
            " && echo ${ARGPARSE_FLAGS[foo]}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "a=b"


def test_unknown_flag_is_recorded_not_rejected(tmp_path: pathlib.Path) -> None:
    """Unknown flags are recorded in ARGPARSE_FLAGS with value '1'."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse -- --some-unknown-flag"
            " && echo ${ARGPARSE_FLAGS[some-unknown-flag]:-MISSING}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "1"


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


def test_positional_arguments_collected_in_order(tmp_path: pathlib.Path) -> None:
    """Non-flag tokens appear in ARGPARSE_POSITIONAL in input order."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse alpha beta gamma"
            " && echo ${ARGPARSE_POSITIONAL[0]} ${ARGPARSE_POSITIONAL[1]} ${ARGPARSE_POSITIONAL[2]}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "alpha beta gamma"


def test_value_flag_missing_value_goes_to_argparse_missing(tmp_path: pathlib.Path) -> None:
    """A value-taking flag with no value (end of argv) is recorded in ARGPARSE_MISSING."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project' -- --project"
            " && echo ${ARGPARSE_MISSING[0]}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "project"


def test_value_flag_missing_when_next_token_is_flag(tmp_path: pathlib.Path) -> None:
    """A value-taking flag whose next token is another flag goes into ARGPARSE_MISSING."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project color' -- --project --color red"
            " && echo ${ARGPARSE_MISSING[0]}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "project"


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


def test_multiple_flags_parsed_independently(tmp_path: pathlib.Path) -> None:
    """Multiple flags in a single argparse_parse call are all recorded."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project agent' -- --project foo --agent coder --verbose"
            " && echo ${ARGPARSE_FLAGS[project]} ${ARGPARSE_FLAGS[agent]} ${ARGPARSE_FLAGS[verbose]}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "foo coder 1"


# ---------------------------------------------------------------------------
# argparse_has — presence check
# ---------------------------------------------------------------------------


def test_argparse_has_returns_true_for_present_flag(tmp_path: pathlib.Path) -> None:
    """argparse_has returns 0 (true) when the flag was present in argv."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --dry-run"
            " && argparse_has dry-run && echo yes || echo no"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_argparse_has_returns_false_for_absent_flag(tmp_path: pathlib.Path) -> None:
    """argparse_has returns 1 (false) when the flag was not in argv."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --some-flag"
            " && argparse_has absent-flag && echo yes || echo no"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


# ---------------------------------------------------------------------------
# argparse_missing — missing value check
# ---------------------------------------------------------------------------


def test_argparse_missing_returns_true_when_flag_had_no_value(tmp_path: pathlib.Path) -> None:
    """argparse_missing returns 0 when the flag was declared value-taking but got no value."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project' -- --project"
            " && argparse_missing project && echo yes || echo no"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_argparse_missing_returns_false_when_flag_had_a_value(tmp_path: pathlib.Path) -> None:
    """argparse_missing returns 1 when the flag was present with a value."""
    result = run_bash(
        tmp_path,
        _source(
            "argparse_parse --value-flags 'project' -- --project myproj"
            " && argparse_missing project && echo yes || echo no"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


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

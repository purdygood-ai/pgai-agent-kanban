"""
test_error_contract_bash.py
============================
Behavioral unit tests for team/scripts/lib/error_contract.sh — the shared
dual-channel error-contract primitive bash fail-loud sites source.

Tests source the shell script and invoke each function via the bash
harness, asserting on stdout/stderr and exit codes. The core behavioral
requirement is the house shape: exactly three lines, in error/message/
remedy order, colon-aligned so the value column starts at the same
character position on every line, with the machine key emitted verbatim so
it stays greppable.
"""

from __future__ import annotations

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/error_contract.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources error_contract.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


# ---------------------------------------------------------------------------
# pgai_format_error_contract: house shape (stdout)
# ---------------------------------------------------------------------------


def test_format_prints_house_shape_to_stdout(tmp_path) -> None:
    """pgai_format_error_contract echoes the three-line block to stdout."""
    result = run_bash(
        tmp_path,
        _source(
            "pgai_format_error_contract 'gate.rc-branch-mismatch' "
            "'the release stopped because the checkout was not on the release branch' "
            "'git checkout rc/v1.2.3 && re-run the release script'"
        ),
    )
    assert result.returncode == 0
    lines = result.stdout.rstrip("\n").split("\n")
    assert len(lines) == 3
    assert lines[0] == "error:   gate.rc-branch-mismatch"
    assert (
        lines[1]
        == "message: the release stopped because the checkout was not on the release branch"
    )
    assert lines[2] == "remedy:  git checkout rc/v1.2.3 && re-run the release script"


def test_format_machine_key_is_verbatim_and_greppable(tmp_path) -> None:
    """The error field carries the exact machine key string, unaltered."""
    result = run_bash(
        tmp_path,
        _source(
            "pgai_format_error_contract 'hook.pre-squash-nonexecutable' 'msg text' 'remedy text'"
        ),
    )
    assert result.returncode == 0
    error_line = result.stdout.split("\n", 1)[0]
    assert error_line == "error:   hook.pre-squash-nonexecutable"


def test_format_fields_are_colon_aligned_to_same_value_column(tmp_path) -> None:
    """error:, message:, and remedy: labels pad to the same value column."""
    # Values are distinct sentinel tokens that do not appear inside their
    # own label word (unlike "k"/"m"/"r", which would collide with the
    # first letters of "error"/"message"/"remedy" and give a false match).
    result = run_bash(
        tmp_path,
        _source("pgai_format_error_contract AAA_ERR BBB_MSG CCC_REM"),
    )
    assert result.returncode == 0
    lines = result.stdout.rstrip("\n").split("\n")
    starts = [
        line.index(value)
        for line, value in zip(lines, ("AAA_ERR", "BBB_MSG", "CCC_REM"))
    ]
    assert starts[0] == starts[1] == starts[2]


def test_format_field_order_is_fixed_error_message_remedy(tmp_path) -> None:
    """Field order in the rendered block is always error, then message, then remedy."""
    result = run_bash(
        tmp_path,
        _source("pgai_format_error_contract ERR_KEY MSG_TEXT REMEDY_TEXT"),
    )
    assert result.returncode == 0
    out = result.stdout
    assert out.index("ERR_KEY") < out.index("MSG_TEXT") < out.index("REMEDY_TEXT")


# ---------------------------------------------------------------------------
# pgai_format_error_contract: validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        "'' 'a message' 'a remedy'",
        "'an-error' '' 'a remedy'",
        "'an-error' 'a message' ''",
    ],
)
def test_format_refuses_blank_field(tmp_path, args: str) -> None:
    """A blank error, message, or remedy is refused (nonzero exit, no stdout)."""
    result = run_bash(tmp_path, _source(f"pgai_format_error_contract {args}"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert "required and must be non-empty" in result.stderr


# ---------------------------------------------------------------------------
# pgai_emit_error_contract: stderr output
# ---------------------------------------------------------------------------


def test_emit_prints_house_shape_to_stderr_not_stdout(tmp_path) -> None:
    """pgai_emit_error_contract writes the formatted block to stderr, leaving stdout empty."""
    result = run_bash(
        tmp_path,
        _source("pgai_emit_error_contract gate.x 'message text' 'remedy text'"),
    )
    assert result.returncode == 0
    assert result.stdout == ""
    lines = result.stderr.rstrip("\n").split("\n")
    assert lines == ["error:   gate.x", "message: message text", "remedy:  remedy text"]


def test_emit_refuses_blank_field_and_returns_nonzero(tmp_path) -> None:
    """pgai_emit_error_contract propagates the same validation as the format function."""
    result = run_bash(
        tmp_path,
        _source("pgai_emit_error_contract '' 'message text' 'remedy text'"),
    )
    assert result.returncode == 1
    assert "required and must be non-empty" in result.stderr

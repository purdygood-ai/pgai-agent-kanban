"""
test_error_contract.py
=======================
Behavioral unit tests for pgai_agent_kanban.lib.error_contract — the shared
dual-channel error-contract primitive Python fail-loud sites import.

Each test feeds real field values and asserts on the exact rendered output —
no did-not-crash checks. The core behavioral requirement is the house shape:
exactly three lines, in error/message/remedy order, colon-aligned so the
value column starts at the same character position on every line, with the
machine key emitted verbatim so it stays greppable.
"""

from __future__ import annotations

import io

import pytest

from pgai_agent_kanban.lib.error_contract import (
    emit_error_contract,
    format_error_contract,
)


# ---------------------------------------------------------------------------
# format_error_contract: house shape
# ---------------------------------------------------------------------------


def test_house_shape_has_exactly_three_lines_in_order() -> None:
    """The formatted block is exactly three lines: error, message, remedy."""
    result = format_error_contract(
        "gate.rc-branch-mismatch",
        "the release stopped because the checkout was not on the release branch",
        "git checkout rc/v1.2.3 && re-run the release script",
    )
    lines = result.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("error:")
    assert lines[1].startswith("message:")
    assert lines[2].startswith("remedy:")


def test_machine_key_is_emitted_verbatim_and_greppable() -> None:
    """The error field carries the exact machine key string, unaltered."""
    result = format_error_contract(
        "hook.pre-squash-nonexecutable",
        "the pre-squash hook file exists but is not marked executable",
        "chmod +x scripts/bump-version.sh",
    )
    assert "error:   hook.pre-squash-nonexecutable" in result
    # Greppable: the bare key appears as a standalone token on its line.
    error_line = result.split("\n")[0]
    assert error_line.split()[-1] == "hook.pre-squash-nonexecutable"


def test_fields_are_colon_aligned_to_the_same_value_column() -> None:
    """error:, message:, and remedy: labels pad to the same value column."""
    # Values are distinct sentinel tokens that do not appear inside their
    # own label word (unlike "k"/"m"/"r", which would collide with the
    # first letters of "error"/"message"/"remedy" and give a false match).
    result = format_error_contract("AAA_ERR", "BBB_MSG", "CCC_REM")
    lines = result.split("\n")
    starts = [
        line.index(value)
        for line, value in zip(lines, ("AAA_ERR", "BBB_MSG", "CCC_REM"))
    ]
    assert starts[0] == starts[1] == starts[2]


def test_field_order_is_fixed_error_message_remedy() -> None:
    """Swapping which text goes where still preserves error/message/remedy order."""
    result = format_error_contract("ERR_KEY", "MSG_TEXT", "REMEDY_TEXT")
    assert result.index("ERR_KEY") < result.index("MSG_TEXT") < result.index("REMEDY_TEXT")


def test_message_and_remedy_text_are_preserved_verbatim() -> None:
    """The message and remedy strings are not truncated, escaped, or altered."""
    message = "the version-bump step expected the checkout on the release branch, and it wasn't"
    remedy = "git checkout rc/v2.3.5 && bash scripts/cm/release.sh"
    result = format_error_contract("gate.checkout-branch", message, remedy)
    assert message in result
    assert remedy in result


# ---------------------------------------------------------------------------
# format_error_contract: validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error, message, remedy",
    [
        ("", "a message", "a remedy"),
        ("an-error", "", "a remedy"),
        ("an-error", "a message", ""),
        ("   ", "a message", "a remedy"),
        ("an-error", "   ", "a remedy"),
        ("an-error", "a message", "   "),
    ],
)
def test_blank_field_raises_value_error(error: str, message: str, remedy: str) -> None:
    """Any blank or whitespace-only field is refused, not silently emitted."""
    with pytest.raises(ValueError, match="error_contract"):
        format_error_contract(error, message, remedy)


def test_value_error_names_every_blank_field() -> None:
    """The validation error names each blank field so the caller can fix all at once."""
    with pytest.raises(ValueError) as excinfo:
        format_error_contract("", "a message", "")
    # The trailing "blank/missing: ..." list names exactly the blank fields
    # (error, remedy) and excludes the one field that was actually supplied
    # (message) — distinct from the fixed preamble, which always mentions
    # all three field names by design.
    blank_list = str(excinfo.value).rsplit("blank/missing:", 1)[-1]
    assert "error" in blank_list
    assert "remedy" in blank_list
    assert "message" not in blank_list


# ---------------------------------------------------------------------------
# emit_error_contract: stream output
# ---------------------------------------------------------------------------


def test_emit_prints_house_shape_to_given_stream() -> None:
    """emit_error_contract writes the formatted block, plus a trailing newline, to file."""
    buf = io.StringIO()
    emit_error_contract("gate.x", "message text", "remedy text", file=buf)
    output = buf.getvalue()
    assert output == format_error_contract("gate.x", "message text", "remedy text") + "\n"


def test_emit_defaults_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """With no file argument, emit_error_contract writes to stderr, not stdout."""
    emit_error_contract("gate.y", "message text", "remedy text")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:   gate.y" in captured.err


def test_emit_propagates_validation_error() -> None:
    """emit_error_contract refuses a blank field the same way format does."""
    buf = io.StringIO()
    with pytest.raises(ValueError, match="error_contract"):
        emit_error_contract("", "message text", "remedy text", file=buf)
    assert buf.getvalue() == ""

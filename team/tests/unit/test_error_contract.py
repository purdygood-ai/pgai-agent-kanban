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


# ---------------------------------------------------------------------------
# emit_error_contract: refusal-ledger wiring
# ---------------------------------------------------------------------------


def test_emit_with_project_records_a_refusal_ledger_entry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal emitted with project= in scope leaves a record naming the
    machine key, message, remedy, timestamp, and raising surface, readable
    through the library's one reader."""
    from pgai_agent_kanban.lib.refusal_ledger import (
        ledger_path_for_project,
        read_latest,
    )

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    buf = io.StringIO()

    emit_error_contract(
        "gate.ledger", "ledger message", "ledger remedy", file=buf, project="demo-project"
    )

    record = read_latest(ledger_path_for_project(str(tmp_path), "demo-project"))
    assert record is not None
    assert record["error"] == "gate.ledger"
    assert record["message"] == "ledger message"
    assert record["remedy"] == "ledger remedy"
    assert record["surface"].endswith(
        "test_error_contract.py:test_emit_with_project_records_a_refusal_ledger_entry"
    )


def test_second_emit_replaces_first_ledger_entry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second refusal for the same project REPLACES the first: the
    record still holds exactly one entry and it is the newer one."""
    from pgai_agent_kanban.lib.refusal_ledger import (
        ledger_path_for_project,
        read_latest,
    )

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))

    emit_error_contract(
        "gate.first", "first message", "first remedy",
        file=io.StringIO(), project="demo-project",
    )
    emit_error_contract(
        "gate.second", "second message", "second remedy",
        file=io.StringIO(), project="demo-project",
    )

    ledger_path = ledger_path_for_project(str(tmp_path), "demo-project")
    text = open(ledger_path, encoding="utf-8").read()
    assert text.count("## Refusal:") == 1
    assert "gate.first" not in text

    record = read_latest(ledger_path)
    assert record["error"] == "gate.second"


def test_emit_without_project_scope_records_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No explicit project= and no ambient PGAI_PROJECT_NAME: recording is
    skipped entirely -- no project directory is even created."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)

    emit_error_contract("gate.noproj", "no project message", "no project remedy", file=io.StringIO())

    assert not (tmp_path / "projects").exists()


def test_emit_with_unwritable_ledger_path_preserves_output(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the ledger path made unwritable, a refusal still prints its
    original stdout/stderr text unchanged and raises nothing -- the
    diagnostic never becomes a gate."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    projects_dir.chmod(0)
    try:
        buf = io.StringIO()
        emit_error_contract(
            "gate.unwritable", "unwritable message", "unwritable remedy",
            file=buf, project="locked-project",
        )
    finally:
        projects_dir.chmod(0o755)

    assert buf.getvalue() == (
        format_error_contract("gate.unwritable", "unwritable message", "unwritable remedy") + "\n"
    )


# ---------------------------------------------------------------------------
# dual_channel_from_text: the one refusal renderer a CLI and its REST twin share
# ---------------------------------------------------------------------------


def test_dual_channel_from_text_returns_the_parsed_triple_when_the_house_shape_is_present() -> None:
    from pgai_agent_kanban.lib.error_contract import dual_channel_from_text

    text = "release-state.sh: resolving...\n" + format_error_contract(
        "release-state.invalid-field",
        "release-state.md failed validation (Active RC='NOTAVERSION')",
        "Active RC: set '## Active RC' to one of: 'none' or vX.Y.Z, then re-run",
    )
    triple = dual_channel_from_text(text, "fallback.key", "fallback remedy")
    assert triple == {
        "error": "release-state.invalid-field",
        "message": "release-state.md failed validation (Active RC='NOTAVERSION')",
        "remedy": "Active RC: set '## Active RC' to one of: 'none' or vX.Y.Z, then re-run",
    }, "the raiser's own three fields win whenever the house shape is present"


def test_dual_channel_from_text_falls_back_to_the_shared_key_and_remedy_for_plain_text() -> None:
    from pgai_agent_kanban.lib.error_contract import dual_channel_from_text

    triple = dual_channel_from_text(
        "the HALT file is present", "ops.halted", "remove the HALT file, then retry"
    )
    assert triple == {
        "error": "ops.halted",
        "message": "the HALT file is present",
        "remedy": "remove the HALT file, then retry",
    }, "plain refusal text becomes the message; the remedy channel is never empty"
    # Re-rendered through the writer, the fallback triple is a complete
    # house-shape block -- the CLI half of the shared rendering path.
    block = format_error_contract(triple["error"], triple["message"], triple["remedy"])
    assert block.splitlines()[2] == "remedy:  remove the HALT file, then retry"

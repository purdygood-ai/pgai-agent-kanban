"""
test_cm_state_fields.py
=======================
Behavioral unit tests for cm/read_state_field.py and cm/write_rc_state.py.

Covers state-field read semantics (missing file, missing heading, horizontal
rule boundary, comment lines, empty content after heading) and the three
write_rc_state operations (write_open stdout output, write_ship / write_cancel
in-place merge semantics including missing / corrupt JSON inputs).

All tests use tmp_path; no live filesystem paths or env leakage.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from pgai_agent_kanban.cm.read_state_field import read_state_field, _is_horizontal_rule
from pgai_agent_kanban.cm.write_rc_state import write_open, write_ship, write_cancel


# ---------------------------------------------------------------------------
# _is_horizontal_rule helper
# ---------------------------------------------------------------------------


def test_horizontal_rule_matches_three_dashes() -> None:
    """_is_horizontal_rule returns True for exactly three dashes."""
    assert _is_horizontal_rule("---") is True


def test_horizontal_rule_matches_many_dashes() -> None:
    """_is_horizontal_rule returns True for four or more consecutive dashes."""
    assert _is_horizontal_rule("----") is True
    assert _is_horizontal_rule("----------") is True


def test_horizontal_rule_rejects_two_dashes() -> None:
    """_is_horizontal_rule returns False for only two dashes."""
    assert _is_horizontal_rule("--") is False


def test_horizontal_rule_rejects_non_dash_characters() -> None:
    """_is_horizontal_rule returns False when non-dash characters are present."""
    assert _is_horizontal_rule("---x") is False
    assert _is_horizontal_rule("abc") is False


def test_horizontal_rule_rejects_empty_string() -> None:
    """_is_horizontal_rule returns False for an empty string."""
    assert _is_horizontal_rule("") is False


# ---------------------------------------------------------------------------
# read_state_field — missing file and missing heading
# ---------------------------------------------------------------------------


def test_read_state_field_returns_none_when_file_absent(tmp_path: Path) -> None:
    """read_state_field returns 'none' when the file does not exist."""
    result = read_state_field(str(tmp_path / "nonexistent.md"), "Active RC")
    assert result == "none"


def test_read_state_field_returns_none_when_heading_absent(tmp_path: Path) -> None:
    """read_state_field returns 'none' when the heading is not in the file."""
    md = tmp_path / "state.md"
    md.write_text("# Release State\n\n## State\nIDLE\n", encoding="utf-8")
    result = read_state_field(str(md), "Active RC")
    assert result == "none"


def test_read_state_field_returns_value_for_present_heading(tmp_path: Path) -> None:
    """read_state_field returns the first non-blank value line after the heading."""
    md = tmp_path / "state.md"
    md.write_text(
        "# Release State\n\n## Active RC\nrc/v0.42.0\n\n## State\nIDLE\n",
        encoding="utf-8",
    )
    result = read_state_field(str(md), "Active RC")
    assert result == "rc/v0.42.0"


def test_read_state_field_skips_blank_lines_before_value(tmp_path: Path) -> None:
    """read_state_field skips blank lines between the heading and its value."""
    md = tmp_path / "state.md"
    md.write_text(
        "## Target\n\n\nmy-value\n",
        encoding="utf-8",
    )
    result = read_state_field(str(md), "Target")
    assert result == "my-value"


def test_read_state_field_skips_comment_lines_starting_with_hash(
    tmp_path: Path,
) -> None:
    """read_state_field skips lines starting with '#' (comments and headings) and continues scanning."""
    md = tmp_path / "state.md"
    # Comment and heading lines are skipped; scanning continues until a qualifying value
    md.write_text(
        "## Target\n# this is a comment\nactual-value\n",
        encoding="utf-8",
    )
    result = read_state_field(str(md), "Target")
    assert result == "actual-value"


def test_read_state_field_returns_none_when_only_comments_and_no_value(
    tmp_path: Path,
) -> None:
    """read_state_field returns 'none' when only comment lines follow the heading (no non-comment value)."""
    md = tmp_path / "state.md"
    md.write_text(
        "## Target\n# only a comment\n# another comment\n",
        encoding="utf-8",
    )
    result = read_state_field(str(md), "Target")
    assert result == "none"


def test_read_state_field_skips_heading_lines_and_returns_next_non_heading_value(
    tmp_path: Path,
) -> None:
    """read_state_field skips ## heading lines (they start with '#') and returns the first non-heading value."""
    md = tmp_path / "state.md"
    # The ## Next Section line starts with '#' so it is skipped, and 'value' is returned.
    # This is the as-is extraction behavior — headings are skipped, not treated as boundaries.
    md.write_text(
        "## Empty Heading\n## Next Section\nvalue\n",
        encoding="utf-8",
    )
    result = read_state_field(str(md), "Empty Heading")
    assert result == "value"


def test_read_state_field_treats_horizontal_rule_as_section_boundary(tmp_path: Path) -> None:
    """read_state_field treats '---' as a section boundary and returns 'none'."""
    md = tmp_path / "state.md"
    md.write_text(
        "## Active RC\n---\nsome-rc\n",
        encoding="utf-8",
    )
    result = read_state_field(str(md), "Active RC")
    assert result == "none"


def test_read_state_field_does_not_return_dashes_as_value(tmp_path: Path) -> None:
    """read_state_field never returns the '---' rule itself as the field value."""
    md = tmp_path / "state.md"
    md.write_text("## State\n---\n", encoding="utf-8")
    result = read_state_field(str(md), "State")
    assert result == "none"
    assert "---" not in result


def test_read_state_field_strips_surrounding_whitespace(tmp_path: Path) -> None:
    """read_state_field strips leading and trailing whitespace from the returned value."""
    md = tmp_path / "state.md"
    md.write_text("## Field\n  padded value  \n", encoding="utf-8")
    result = read_state_field(str(md), "Field")
    assert result == "padded value"


def test_read_state_field_returns_none_for_heading_at_eof_with_no_value(
    tmp_path: Path,
) -> None:
    """read_state_field returns 'none' when the heading is the last line in the file."""
    md = tmp_path / "state.md"
    md.write_text("## Dangling Heading\n", encoding="utf-8")
    result = read_state_field(str(md), "Dangling Heading")
    assert result == "none"


def test_read_state_field_returns_first_non_blank_value_ignores_subsequent_lines(
    tmp_path: Path,
) -> None:
    """read_state_field returns only the first qualifying value, ignoring subsequent lines."""
    md = tmp_path / "state.md"
    md.write_text("## Field\nfirst\nsecond\nthird\n", encoding="utf-8")
    result = read_state_field(str(md), "Field")
    assert result == "first"


# ---------------------------------------------------------------------------
# write_open — stdout format
# ---------------------------------------------------------------------------


def test_write_open_emits_valid_json_to_stdout(capsys: pytest.CaptureFixture) -> None:
    """write_open writes valid JSON to stdout."""
    write_open("v0.99.0", "2026-01-01T00:00:00Z")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert isinstance(payload, dict)


def test_write_open_json_contains_expected_keys(capsys: pytest.CaptureFixture) -> None:
    """write_open output includes rc, opened_at, closed_at, and outcome keys."""
    write_open("v0.99.0", "2026-01-01T00:00:00Z")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert set(payload.keys()) == {"rc", "opened_at", "closed_at", "outcome"}


def test_write_open_sets_rc_and_opened_at(capsys: pytest.CaptureFixture) -> None:
    """write_open records the supplied rc and opened_at values."""
    write_open("v0.77.3", "2026-06-15T12:00:00Z")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["rc"] == "v0.77.3"
    assert payload["opened_at"] == "2026-06-15T12:00:00Z"


def test_write_open_sets_closed_at_to_none(capsys: pytest.CaptureFixture) -> None:
    """write_open sets closed_at to null (None) in the emitted JSON."""
    write_open("v0.77.3", "2026-06-15T12:00:00Z")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["closed_at"] is None


def test_write_open_sets_outcome_to_in_progress(capsys: pytest.CaptureFixture) -> None:
    """write_open sets outcome to 'in_progress'."""
    write_open("v0.77.3", "2026-06-15T12:00:00Z")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["outcome"] == "in_progress"


def test_write_open_output_ends_with_newline(capsys: pytest.CaptureFixture) -> None:
    """write_open appends a trailing newline after the JSON block."""
    write_open("v0.1.0", "2026-01-01T00:00:00Z")
    captured = capsys.readouterr()
    assert captured.out.endswith("\n")


# ---------------------------------------------------------------------------
# write_ship — in-place merge semantics
# ---------------------------------------------------------------------------


def test_write_ship_creates_file_when_absent(tmp_path: Path) -> None:
    """write_ship creates the JSON file when it does not already exist."""
    state_file = tmp_path / "v0.1.0.json"
    write_ship(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    assert state_file.exists()


def test_write_ship_sets_outcome_shipped(tmp_path: Path) -> None:
    """write_ship sets outcome to 'shipped' in the written JSON."""
    state_file = tmp_path / "v0.1.0.json"
    write_ship(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["outcome"] == "shipped"


def test_write_ship_sets_closed_at(tmp_path: Path) -> None:
    """write_ship writes the supplied closed_at timestamp."""
    state_file = tmp_path / "v0.1.0.json"
    write_ship(str(state_file), "v0.1.0", "2026-06-01T10:30:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["closed_at"] == "2026-06-01T10:30:00Z"


def test_write_ship_preserves_opened_at_from_existing_file(tmp_path: Path) -> None:
    """write_ship preserves the existing opened_at value when the file already has it."""
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text(
        json.dumps(
            {"rc": "v0.1.0", "opened_at": "2026-05-01T00:00:00Z",
             "closed_at": None, "outcome": "in_progress"},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    write_ship(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["opened_at"] == "2026-05-01T00:00:00Z"


def test_write_ship_does_not_add_opened_at_when_absent_from_existing_file(
    tmp_path: Path,
) -> None:
    """write_ship does not inject opened_at when it is absent from the existing record."""
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text(
        json.dumps({"rc": "v0.1.0", "outcome": "in_progress"}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_ship(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert "opened_at" not in payload


def test_write_ship_does_not_overwrite_existing_rc(tmp_path: Path) -> None:
    """write_ship uses setdefault — rc is preserved from the existing file, not overwritten."""
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text(
        json.dumps({"rc": "v0.1.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_ship(str(state_file), "v-different", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    # rc is preserved because setdefault does not overwrite existing key
    assert payload["rc"] == "v0.1.0"


def test_write_ship_recovers_from_corrupt_json(tmp_path: Path) -> None:
    """write_ship starts from an empty dict when the existing file contains invalid JSON."""
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text("not json at all {{{{", encoding="utf-8")
    # Should not raise; should recover to a minimal valid record
    write_ship(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["outcome"] == "shipped"
    assert payload["rc"] == "v0.1.0"


# ---------------------------------------------------------------------------
# write_cancel — in-place merge semantics
# ---------------------------------------------------------------------------


def test_write_cancel_creates_file_when_absent(tmp_path: Path) -> None:
    """write_cancel creates the JSON file when it does not already exist."""
    state_file = tmp_path / "v0.2.0.json"
    write_cancel(str(state_file), "v0.2.0", "2026-06-02T00:00:00Z")
    assert state_file.exists()


def test_write_cancel_sets_outcome_cancelled(tmp_path: Path) -> None:
    """write_cancel sets outcome to 'cancelled' in the written JSON."""
    state_file = tmp_path / "v0.2.0.json"
    write_cancel(str(state_file), "v0.2.0", "2026-06-02T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["outcome"] == "cancelled"


def test_write_cancel_sets_closed_at(tmp_path: Path) -> None:
    """write_cancel records the supplied closed_at timestamp."""
    state_file = tmp_path / "v0.2.0.json"
    write_cancel(str(state_file), "v0.2.0", "2026-06-02T15:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["closed_at"] == "2026-06-02T15:00:00Z"


def test_write_cancel_preserves_opened_at_from_existing_file(tmp_path: Path) -> None:
    """write_cancel preserves the existing opened_at value."""
    state_file = tmp_path / "v0.2.0.json"
    state_file.write_text(
        json.dumps(
            {"rc": "v0.2.0", "opened_at": "2026-04-01T00:00:00Z",
             "closed_at": None, "outcome": "in_progress"},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    write_cancel(str(state_file), "v0.2.0", "2026-06-02T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["opened_at"] == "2026-04-01T00:00:00Z"


def test_write_cancel_does_not_add_opened_at_when_absent_from_existing_file(
    tmp_path: Path,
) -> None:
    """write_cancel does not inject opened_at when absent from the existing record."""
    state_file = tmp_path / "v0.2.0.json"
    state_file.write_text(
        json.dumps({"rc": "v0.2.0", "outcome": "in_progress"}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_cancel(str(state_file), "v0.2.0", "2026-06-02T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert "opened_at" not in payload


def test_write_cancel_recovers_from_corrupt_json(tmp_path: Path) -> None:
    """write_cancel starts from an empty dict when the existing file has invalid JSON."""
    state_file = tmp_path / "v0.2.0.json"
    state_file.write_text("{broken json}", encoding="utf-8")
    write_cancel(str(state_file), "v0.2.0", "2026-06-02T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["outcome"] == "cancelled"
    assert payload["rc"] == "v0.2.0"


def test_write_cancel_does_not_overwrite_existing_rc(tmp_path: Path) -> None:
    """write_cancel uses setdefault — rc from the existing file takes precedence."""
    state_file = tmp_path / "v0.2.0.json"
    state_file.write_text(
        json.dumps({"rc": "v0.2.0-original"}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_cancel(str(state_file), "v0.2.0-new", "2026-06-02T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["rc"] == "v0.2.0-original"

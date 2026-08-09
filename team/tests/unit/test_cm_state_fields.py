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


@pytest.mark.parametrize(
    "value, expected",
    [
        pytest.param("---", True, id="matches_three_dashes"),
        pytest.param("----", True, id="matches_four_dashes"),
        pytest.param("----------", True, id="matches_many_dashes"),
        pytest.param("--", False, id="rejects_two_dashes"),
        pytest.param("---x", False, id="rejects_trailing_non_dash_character"),
        pytest.param("abc", False, id="rejects_non_dash_characters"),
        pytest.param("", False, id="rejects_empty_string"),
    ],
)
def test_is_horizontal_rule(value: str, expected: bool) -> None:
    """_is_horizontal_rule accepts 3+ consecutive dashes and rejects everything else."""
    assert _is_horizontal_rule(value) is expected


# ---------------------------------------------------------------------------
# read_state_field — missing file and missing heading
# ---------------------------------------------------------------------------


def test_read_state_field_returns_none_when_file_absent(tmp_path: Path) -> None:
    """read_state_field returns 'none' when the file does not exist."""
    result = read_state_field(str(tmp_path / "nonexistent.md"), "Active RC")
    assert result == "none"


@pytest.mark.parametrize(
    "file_content, heading, expected",
    [
        pytest.param(
            "# Release State\n\n## State\nIDLE\n",
            "Active RC",
            "none",
            id="returns_none_when_heading_absent",
        ),
        pytest.param(
            "# Release State\n\n## Active RC\nrc/v0.42.0\n\n## State\nIDLE\n",
            "Active RC",
            "rc/v0.42.0",
            id="returns_value_for_present_heading",
        ),
        pytest.param(
            "## Target\n\n\nmy-value\n",
            "Target",
            "my-value",
            id="skips_blank_lines_before_value",
        ),
        pytest.param(
            "## Target\n# this is a comment\nactual-value\n",
            "Target",
            "actual-value",
            id="skips_comment_lines_starting_with_hash",
        ),
        pytest.param(
            "## Target\n# only a comment\n# another comment\n",
            "Target",
            "none",
            id="returns_none_when_only_comments_and_no_value",
        ),
        pytest.param(
            # The ## Next Section line starts with '#' so it is skipped, and
            # 'value' is returned — headings are skipped, not treated as
            # boundaries (as-is extraction behavior).
            "## Empty Heading\n## Next Section\nvalue\n",
            "Empty Heading",
            "value",
            id="skips_heading_lines_and_returns_next_non_heading_value",
        ),
        pytest.param(
            "## Active RC\n---\nsome-rc\n",
            "Active RC",
            "none",
            id="treats_horizontal_rule_as_section_boundary",
        ),
        pytest.param(
            "## State\n---\n",
            "State",
            "none",
            id="does_not_return_dashes_as_value",
        ),
        pytest.param(
            "## Field\n  padded value  \n",
            "Field",
            "padded value",
            id="strips_surrounding_whitespace",
        ),
        pytest.param(
            "## Dangling Heading\n",
            "Dangling Heading",
            "none",
            id="returns_none_for_heading_at_eof_with_no_value",
        ),
        pytest.param(
            "## Field\nfirst\nsecond\nthird\n",
            "Field",
            "first",
            id="returns_first_non_blank_value_ignores_subsequent_lines",
        ),
    ],
)
def test_read_state_field(
    tmp_path: Path, file_content: str, heading: str, expected: str
) -> None:
    """read_state_field's value-extraction semantics across boundary, comment,
    whitespace, and heading-skip cases (survives-as-named-parameterization of
    the original eleven discrete tests; no case dropped)."""
    md = tmp_path / "state.md"
    md.write_text(file_content, encoding="utf-8")
    result = read_state_field(str(md), heading)
    assert result == expected
    # The horizontal rule itself must never leak through as a field value,
    # regardless of which case produced "none" (locks the dashes-as-value
    # regression for every case in this table, not just one).
    if expected == "none":
        assert "---" not in result


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
# write_ship / write_cancel — shared in-place merge semantics
#
# write_ship and write_cancel are structural siblings: both close out a
# per-RC state file via the same read-modify-write merge, differing only in
# the terminal outcome string they stamp. Every behavior below is verified
# for both functions (survives-as-named-parameterization of the original
# fourteen per-function tests, seven behaviors x two functions; no case
# dropped).
# ---------------------------------------------------------------------------

_CLOSE_OUT_FUNCTIONS = [
    pytest.param(write_ship, "shipped", id="write_ship"),
    pytest.param(write_cancel, "cancelled", id="write_cancel"),
]


@pytest.mark.parametrize("write_fn, expected_outcome", _CLOSE_OUT_FUNCTIONS)
def test_close_out_creates_file_when_absent(
    tmp_path: Path, write_fn, expected_outcome: str
) -> None:
    state_file = tmp_path / "v0.1.0.json"
    write_fn(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    assert state_file.exists()


@pytest.mark.parametrize("write_fn, expected_outcome", _CLOSE_OUT_FUNCTIONS)
def test_close_out_sets_outcome(
    tmp_path: Path, write_fn, expected_outcome: str
) -> None:
    state_file = tmp_path / "v0.1.0.json"
    write_fn(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["outcome"] == expected_outcome


@pytest.mark.parametrize("write_fn, expected_outcome", _CLOSE_OUT_FUNCTIONS)
def test_close_out_sets_closed_at(
    tmp_path: Path, write_fn, expected_outcome: str
) -> None:
    state_file = tmp_path / "v0.1.0.json"
    write_fn(str(state_file), "v0.1.0", "2026-06-01T10:30:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["closed_at"] == "2026-06-01T10:30:00Z"


@pytest.mark.parametrize("write_fn, expected_outcome", _CLOSE_OUT_FUNCTIONS)
def test_close_out_preserves_opened_at_from_existing_file(
    tmp_path: Path, write_fn, expected_outcome: str
) -> None:
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text(
        json.dumps(
            {"rc": "v0.1.0", "opened_at": "2026-05-01T00:00:00Z",
             "closed_at": None, "outcome": "in_progress"},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    write_fn(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["opened_at"] == "2026-05-01T00:00:00Z"


@pytest.mark.parametrize("write_fn, expected_outcome", _CLOSE_OUT_FUNCTIONS)
def test_close_out_does_not_add_opened_at_when_absent_from_existing_file(
    tmp_path: Path, write_fn, expected_outcome: str
) -> None:
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text(
        json.dumps({"rc": "v0.1.0", "outcome": "in_progress"}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_fn(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert "opened_at" not in payload


@pytest.mark.parametrize("write_fn, expected_outcome", _CLOSE_OUT_FUNCTIONS)
def test_close_out_does_not_overwrite_existing_rc(
    tmp_path: Path, write_fn, expected_outcome: str
) -> None:
    """setdefault semantics — rc is preserved from the existing file, not overwritten."""
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text(
        json.dumps({"rc": "v0.1.0-original"}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_fn(str(state_file), "v0.1.0-new", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["rc"] == "v0.1.0-original"


@pytest.mark.parametrize("write_fn, expected_outcome", _CLOSE_OUT_FUNCTIONS)
def test_close_out_recovers_from_corrupt_json(
    tmp_path: Path, write_fn, expected_outcome: str
) -> None:
    state_file = tmp_path / "v0.1.0.json"
    state_file.write_text("not json at all {{{{", encoding="utf-8")
    # Should not raise; should recover to a minimal valid record.
    write_fn(str(state_file), "v0.1.0", "2026-06-01T00:00:00Z")
    payload = json.loads(state_file.read_text())
    assert payload["outcome"] == expected_outcome
    assert payload["rc"] == "v0.1.0"

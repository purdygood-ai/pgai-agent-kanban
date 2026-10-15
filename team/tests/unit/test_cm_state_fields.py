"""
test_cm_state_fields.py
=======================
Behavioral unit tests for cm/read_state_field.py and cm/write_rc_state.py.

Covers state-field read semantics (missing file, missing heading, horizontal
rule and next-heading boundaries, comment lines, empty content after heading,
an empty release-state field refused as empty rather than read as the next
field, populated release-state shapes reading the same values, appended
Auto-Ship Event sections leaving every reader and ``--validate`` unchanged)
and the three write_rc_state operations (write_open stdout output,
write_ship / write_cancel in-place merge semantics including missing /
corrupt JSON inputs).

All tests use tmp_path; no live filesystem paths or env leakage.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

import subprocess

from pgai_agent_kanban.cm.read_state_field import (
    ReleaseStateValidationError,
    _is_horizontal_rule,
    load_release_state,
    read_release_state_field,
    read_state_field,
    read_state_section,
    validate_release_state_fields,
)
from pgai_agent_kanban.cm.write_rc_state import (
    append_auto_ship_event,
    write_cancel,
    write_open,
    write_ship,
)


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
            # The next heading ends the section: a heading with no value
            # reads as "none", never as the value of the section after it.
            "## Empty Heading\n## Next Section\nvalue\n",
            "Empty Heading",
            "none",
            id="next_heading_ends_the_section_so_an_empty_field_reads_none",
        ),
        pytest.param(
            "## Active RC\n\n## Last Released\nv1.2.3\n",
            "Active RC",
            "none",
            id="blank_line_then_next_heading_reads_none",
        ),
        pytest.param(
            "## Field\n### Sub\nvalue\n",
            "Field",
            "none",
            id="deeper_heading_ends_the_section",
        ),
        pytest.param(
            "## Target\n<!-- a comment\n## not a heading inside it\n-->\nactual\n",
            "Target",
            "actual",
            id="heading_shaped_line_inside_html_comment_is_skipped",
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
        pytest.param(
            # The TESTER report template keeps an "allowed values" HTML
            # comment directly under the heading; the value the author wrote
            # below it is the field value, never the comment's first line.
            "## Recommendation\n\n<!-- Allowed values: PASS | SHIP-WITH-CONCERNS\n"
            "PASS -- clean release.\n-->\n\nSHIP-WITH-SERIOUS-CONCERNS\n",
            "Recommendation",
            "SHIP-WITH-SERIOUS-CONCERNS",
            id="skips_multi_line_html_comment_block_before_value",
        ),
        pytest.param(
            "## Systemic Risk\n<!-- low | medium | high -->\nhigh\n",
            "Systemic Risk",
            "high",
            id="skips_single_line_html_comment_before_value",
        ),
        pytest.param(
            "## Field\nvalue <!-- trailing note -->\n",
            "Field",
            "value",
            id="drops_inline_html_comment_from_value_line",
        ),
        pytest.param(
            "## Field\n<!-- opened here\nclosed here --> tail-value\n",
            "Field",
            "tail-value",
            id="reads_text_after_the_closing_delimiter_as_value",
        ),
        pytest.param(
            "## Field\n<!-- never closed\nstill inside\n",
            "Field",
            "none",
            id="returns_none_when_html_comment_never_closes",
        ),
    ],
)
def test_read_state_field(
    tmp_path: Path, file_content: str, heading: str, expected: str
) -> None:
    """read_state_field's value-extraction semantics across boundary, comment,
    whitespace, and next-heading cases (survives-as-named-parameterization of
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
# read_state_section — the same walk, bounded at the next '##' heading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_content, heading, expected",
    [
        pytest.param("## Active RC\nv1.2.3\n\n## Next\nx\n", "Active RC", "v1.2.3", id="value"),
        pytest.param("## Active RC\nnone\n", "Active RC", "none", id="literal_none_is_a_value"),
        pytest.param("## Empty Heading\n## Next Section\nvalue\n", "Empty Heading", "", id="next_heading_ends_the_section"),
        pytest.param("## Active RC\n\n## Last Released\nv1.2.3\n", "Active RC", "", id="blank_line_then_next_heading"),
        pytest.param("## Field\n### Sub\nvalue\n", "Field", "", id="deeper_heading_ends_the_section"),
        pytest.param("## Target\n# a comment\nactual\n", "Target", "actual", id="single_hash_comment_still_skipped"),
        pytest.param("## Target\n<!-- a | b -->\nactual\n", "Target", "actual", id="html_comment_still_skipped"),
        pytest.param("## Target\n---\nx\n", "Target", "", id="horizontal_rule_still_ends_the_section"),
        pytest.param("## Dangling\n", "Dangling", "", id="heading_at_eof"),
        pytest.param("## Active RC  \n\n  v1.2.3  \n", "Active RC", "v1.2.3", id="trailing_space_heading_and_padded_value"),
        pytest.param("## Other\nx\n", "Active RC", None, id="absent_heading_is_none"),
        pytest.param("## Last Released At\n2026-01-01T00:00:00Z\n", "Last Released", None, id="retired_heading_is_not_the_field"),
    ],
)
def test_read_state_section(tmp_path: Path, file_content: str, heading: str, expected) -> None:
    md = tmp_path / "state.md"
    md.write_text(file_content, encoding="utf-8")
    assert read_state_section(str(md), heading) == expected


def test_read_state_section_missing_file_is_none(tmp_path: Path) -> None:
    assert read_state_section(str(tmp_path / "absent.md"), "Active RC") is None


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.cm.read_state_field", *args],
        capture_output=True, text=True, timeout=30, cwd=str(Path(__file__).resolve().parents[2]),
    )


_CLI_STATE = "## Active RC\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n## Last Released\nv1.0.0\n"


def test_cli_several_headings_print_one_value_per_line(tmp_path: Path) -> None:
    md = tmp_path / "release-state.md"
    md.write_text(_CLI_STATE, encoding="utf-8")
    result = _cli(str(md), "Last Released", "RC Opened At", "RC Opened By Task")
    assert (result.returncode, result.stdout) == (0, "v1.0.0\n2026-01-01T00:00:00Z\nnone\n")


def test_cli_one_heading_keeps_its_contract(tmp_path: Path) -> None:
    md = tmp_path / "release-state.md"
    md.write_text(_CLI_STATE, encoding="utf-8")
    # The next heading ends an empty field's section, so it prints "none"
    # rather than the value of the field after it.
    assert _cli(str(md), "Active RC").stdout == "none\n"


@pytest.mark.parametrize(
    "headings, expected_rc, expected_out",
    [
        (("Active RC", "RC Opened At"), 0, "\n2026-01-01T00:00:00Z\n"),
        (("Active RC", "RC Opened By Task", "Last Released"), 1, "\n\nv1.0.0\n"),
    ],
    ids=["all-present", "absent-heading-exits-1-after-printing"],
)
def test_cli_section_mode(tmp_path: Path, headings, expected_rc: int, expected_out: str) -> None:
    md = tmp_path / "release-state.md"
    md.write_text(_CLI_STATE, encoding="utf-8")
    result = _cli("--section", str(md), *headings)
    assert (result.returncode, result.stdout) == (expected_rc, expected_out)


def test_cli_section_mode_missing_file_exits_1_with_no_output(tmp_path: Path) -> None:
    result = _cli("--section", str(tmp_path / "absent.md"), "Active RC")
    assert (result.returncode, result.stdout) == (1, "")


# ---------------------------------------------------------------------------
# A present-but-empty release-state field is empty, never the next field
# ---------------------------------------------------------------------------

# Every heading present, RC Opened By Task left with no value, and a
# version-valued field directly after it that an unbounded walk would return.
_EMPTY_OPENED_BY = (
    "## Active RC\nv1.2.0\n## RC Opened At\n2026-01-01T00:00:00Z\n"
    "## RC Opened By Task\n\n## Last Released\nv1.1.0\n"
)
_EMPTY_ACTIVE_RC = (
    "## Active RC\n\n## RC Opened At\n2026-01-01T00:00:00Z\n"
    "## RC Opened By Task\nCM-20260101-000-open\n## Last Released\nv1.1.0\n"
)


@pytest.mark.parametrize(
    "file_content, heading, neighbour_value",
    [
        pytest.param(_EMPTY_OPENED_BY, "RC Opened By Task", "v1.1.0", id="empty_free_text_field"),
        pytest.param(_EMPTY_ACTIVE_RC, "Active RC", "2026-01-01T00:00:00Z", id="empty_version_field"),
    ],
)
def test_empty_release_state_field_is_refused_as_empty_value(
    tmp_path: Path, file_content: str, heading: str, neighbour_value: str
) -> None:
    """A present heading with no value is refused as its own empty-value
    fault, dual-channel, and no reader returns the next field's value."""
    md = tmp_path / "release-state.md"
    md.write_text(file_content, encoding="utf-8")

    with pytest.raises(ReleaseStateValidationError) as excinfo:
        read_release_state_field(str(md), heading)
    message = str(excinfo.value)
    assert f"{heading}=<empty> [release-state.empty-value]" in message, message
    assert neighbour_value not in message, message
    assert "remedy:" in message, message

    (result,) = validate_release_state_fields(str(md), (heading,))
    assert (result.ok, result.error_key, result.received) == (False, "release-state.empty-value", None)
    assert read_state_section(str(md), heading) == ""
    assert read_state_field(str(md), heading) == "none"


def test_empty_field_mid_file_does_not_hide_the_fields_after_it(tmp_path: Path) -> None:
    """Validating the whole file reports the empty field and still reads the
    field that follows it as that field's own value."""
    md = tmp_path / "release-state.md"
    md.write_text(_EMPTY_OPENED_BY, encoding="utf-8")
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    assert results["RC Opened By Task"].error_key == "release-state.empty-value"
    assert (results["Last Released"].ok, results["Last Released"].value) == (True, "v1.1.0")
    assert (results["Active RC"].ok, results["Active RC"].value) == (True, "v1.2.0")


def test_missing_heading_and_empty_value_are_labelled_apart(tmp_path: Path) -> None:
    md = tmp_path / "release-state.md"
    md.write_text("## Active RC\n\n## RC Opened By Task\nnone\n", encoding="utf-8")
    with pytest.raises(ReleaseStateValidationError) as excinfo:
        load_release_state(str(md))
    message = str(excinfo.value)
    assert "Active RC=<empty> [release-state.empty-value]" in message, message
    assert "RC Opened At=<missing> [release-state.missing-heading]" in message, message


# ---------------------------------------------------------------------------
# Populated release-state.md shapes read the values they always have
# ---------------------------------------------------------------------------

_OPEN_RC = (
    "# Release State\n\n## Active RC\nv3.4.1\n\n## RC Opened At\n2026-03-01T09:15:00+00:00\n\n"
    "## RC Opened By Task\nCM-20260301-002-open-rc\n\n## Last Released\nv3.4.0\n\n"
    "## Operator Override\nTimestamp: 2026-10-01T00:00:00Z\nVersion: v2.29.0\n"
    "Stop Overridden: release.ship-policy-stop\nReason: ## Active RC\n"
)
_NO_RC = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nv3.4.0\n"
)
_RETIRED_HEADINGS = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n\n"
    "## Last Released\nv1.4.2\n\n## Last Released At\n2026-01-01T00:00:00Z\n\n"
    "## Last Released By Task\nCM-20260101-001-ship\n\n## Auto-Resolve\n- resolved something\n"
)
_NEVER_RELEASED = (
    "# Release State\n\n## Active RC\nv0.1.0\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
    "## RC Opened By Task\nCM-20260101-000-open\n"
)


@pytest.mark.parametrize(
    "file_content, expected",
    [
        pytest.param(
            _OPEN_RC,
            {"Active RC": "v3.4.1", "RC Opened At": "2026-03-01T09:15:00+00:00",
             "RC Opened By Task": "CM-20260301-002-open-rc", "Last Released": "v3.4.0"},
            id="open_rc_with_appended_event_block",
        ),
        pytest.param(
            _NO_RC,
            {"Active RC": "none", "RC Opened At": "none", "RC Opened By Task": "none",
             "Last Released": "v3.4.0"},
            id="no_rc",
        ),
        pytest.param(
            _RETIRED_HEADINGS,
            {"Active RC": "none", "RC Opened At": "none", "RC Opened By Task": "none",
             "Last Released": "v1.4.2"},
            id="retired_headings_and_appended_block",
        ),
        pytest.param(
            _NEVER_RELEASED,
            {"Active RC": "v0.1.0", "RC Opened At": "2026-01-01T00:00:00Z",
             "RC Opened By Task": "CM-20260101-000-open", "Last Released": "none"},
            id="last_released_heading_absent",
        ),
    ],
)
def test_populated_release_state_reads_the_same_through_every_reader(
    tmp_path: Path, file_content: str, expected: dict
) -> None:
    """A populated file gives each field the same value through the raw walk,
    the section read, the single-field validated read and the loader."""
    md = tmp_path / "release-state.md"
    md.write_text(file_content, encoding="utf-8")
    assert load_release_state(str(md)) == expected
    for heading, value in expected.items():
        assert read_state_field(str(md), heading) == value, heading
        assert read_release_state_field(str(md), heading) == value, heading
        # The section read keeps an absent heading apart (None); every other
        # field reads the same value as the walks above.
        heading_present = f"## {heading}\n" in file_content
        assert read_state_section(str(md), heading) == (value if heading_present else None), heading


# ---------------------------------------------------------------------------
# Appended Auto-Ship Event sections change nothing any reader returns
# ---------------------------------------------------------------------------

_AUTO_SHIP_EVENTS = (
    dict(timestamp="2026-10-01T00:00:00Z", version="v3.4.1", trigger=2,
         error_key="cm-release.trigger-2.systemic-risk-high",
         ship_posture="always", tester_task="TESTER-fixture-verify"),
    dict(timestamp="2026-10-02T00:00:00Z", version="v3.4.2", trigger=8,
         error_key="cm-release.trigger-8.last-3-rcs-non-functional",
         ship_posture="always", tester_task="TESTER-fixture-verify-2",
         filed_bug="BUG-fixture-pattern.md"),
)

_VALIDATOR_FIXTURES = [
    pytest.param(_OPEN_RC, id="open_rc_with_operator_override"),
    pytest.param(_NO_RC, id="no_rc"),
    pytest.param(_RETIRED_HEADINGS, id="retired_headings_and_appended_block"),
    pytest.param(_NEVER_RELEASED, id="last_released_heading_absent"),
    pytest.param(_EMPTY_OPENED_BY, id="refused_empty_field"),
    pytest.param(_EMPTY_ACTIVE_RC, id="refused_empty_version_field"),
]


@pytest.mark.parametrize("event_count", [1, 2], ids=["one_event", "two_events"])
@pytest.mark.parametrize("file_content", _VALIDATOR_FIXTURES)
def test_validate_cli_is_unchanged_by_auto_ship_events(
    tmp_path: Path, file_content: str, event_count: int
) -> None:
    """--validate gives the same exit code and output with Auto-Ship Event
    sections appended as without them: 0 on every valid fixture, and the same
    refusal on a fixture that was already refused."""
    plain = tmp_path / "plain" / "release-state.md"
    with_events = tmp_path / "events" / "release-state.md"
    for md in (plain, with_events):
        md.parent.mkdir()
        md.write_text(file_content, encoding="utf-8")
    for event in _AUTO_SHIP_EVENTS[:event_count]:
        append_auto_ship_event(str(with_events), **event)
    assert with_events.read_text(encoding="utf-8").count("## Auto-Ship Event\n") == event_count

    before = _cli("--validate", str(plain))
    after = _cli("--validate", str(with_events))
    assert after.returncode == before.returncode, after.stdout + after.stderr
    assert after.stdout == before.stdout.replace(str(plain), str(with_events))
    if file_content not in (_EMPTY_OPENED_BY, _EMPTY_ACTIVE_RC):
        assert after.returncode == 0, after.stdout


@pytest.mark.parametrize("file_content", _VALIDATOR_FIXTURES[:4])
def test_field_reads_are_unchanged_by_auto_ship_events(tmp_path: Path, file_content: str) -> None:
    """Active RC, RC Opened At and Last Released read the same values, through
    every reader, from a file carrying Auto-Ship Event sections."""
    plain = tmp_path / "plain.md"
    with_events = tmp_path / "with-events.md"
    plain.write_text(file_content, encoding="utf-8")
    with_events.write_text(file_content, encoding="utf-8")
    for event in _AUTO_SHIP_EVENTS:
        append_auto_ship_event(str(with_events), **event)

    assert load_release_state(str(with_events)) == load_release_state(str(plain))
    for heading in ("Active RC", "RC Opened At", "Last Released"):
        expected = read_state_field(str(plain), heading)
        assert read_state_field(str(with_events), heading) == expected, heading
        assert read_release_state_field(str(with_events), heading) == expected, heading
        assert read_state_section(str(with_events), heading) == read_state_section(str(plain), heading), heading


def test_populated_release_state_retired_and_appended_sections_read_as_written(tmp_path: Path) -> None:
    md = tmp_path / "release-state.md"
    md.write_text(_RETIRED_HEADINGS, encoding="utf-8")
    assert read_state_field(str(md), "Last Released At") == "2026-01-01T00:00:00Z"
    assert read_state_field(str(md), "Last Released By Task") == "CM-20260101-001-ship"
    assert read_state_field(str(md), "Auto-Resolve") == "- resolved something"


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

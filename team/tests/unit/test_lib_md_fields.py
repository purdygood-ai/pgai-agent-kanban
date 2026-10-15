"""
test_lib_md_fields.py
=====================
Behavioural tests for pgai_agent_kanban.lib.md_fields: reading a ``## Heading``
value or section body from README, intake and bundle files across the
variants the writers produce — inline value, blank lines after the heading,
bold value, trailing text — and a missing heading.

Every fixture lives under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.lib.md_fields import (
    EXIT_FIELD_ABSENT,
    field_value,
    main,
    read_body,
    read_field,
    section_body,
    set_status_text,
    set_value_line,
)


# --- the five writer variants -------------------------------------------------


def test_inline_value_on_the_heading_line() -> None:
    text = "# Bug\n\n## Status: open\n\n## Severity\nlow\n"
    assert field_value(text, "Status") == "open"
    assert field_value(text, "Severity") == "low"


def test_inline_value_with_spaces_before_the_colon() -> None:
    assert field_value("## PM Task :  PM-20260101-001-x\n", "PM Task") == "PM-20260101-001-x"


def test_blank_lines_after_the_heading() -> None:
    text = "## Owner\n\n\n   \nClaude\n\n## Role\nCODER\n"
    assert field_value(text, "Owner") == "Claude"


@pytest.mark.parametrize("value", ["**high**", "__high__", "  **high**  "])
def test_bold_value_reads_as_the_text_inside(value: str) -> None:
    assert field_value(f"## Severity\n{value}\n", "Severity") == "high"


def test_bold_inline_value_reads_as_the_text_inside() -> None:
    assert field_value("## Severity: **medium**\n", "Severity") == "medium"


def test_trailing_text_on_the_value_line_is_kept() -> None:
    text = "## Severity\nmedium (workaround exists)\n\n## Symptom\nx\n"
    assert field_value(text, "Severity") == "medium (workaround exists)"


def test_trailing_whitespace_on_the_heading_line() -> None:
    assert field_value("## Role \t \nWRITER\n", "Role") == "WRITER"


def test_missing_heading_reads_empty() -> None:
    text = "## Owner\nClaude\n"
    assert field_value(text, "PM Task") == ""
    assert section_body(text, "PM Task") is None


# --- section boundaries -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "## PM Task\n\n## Status\nopen\n",
        "## PM Task\n## Status\nopen\n",
        "## Status\nopen\n\n## PM Task\n",
        "## Status\nopen\n\n## PM Task",
    ],
    ids=["blank-then-next", "next-directly", "last-section", "last-line-no-newline"],
)
def test_empty_section_reads_empty_never_the_next_section(text: str) -> None:
    assert field_value(text, "PM Task") == ""
    assert section_body(text, "PM Task") == ""


def test_heading_is_not_matched_by_a_longer_heading() -> None:
    text = "## Severity Rationale\nbecause\n\n## Severity\nlow\n"
    assert field_value(text, "Severity") == "low"


def test_heading_matches_case_insensitively() -> None:
    assert field_value("## target version\nv2.28.0\n", "Target Version") == "v2.28.0"


def test_crlf_line_endings_read_like_lf() -> None:
    text = "## Owner\r\n\r\nClaude\r\n\r\n## Role\r\nCODER\r\n"
    assert field_value(text, "Owner") == "Claude"
    assert section_body(text, "Role") == "CODER"


def test_section_body_returns_every_line_up_to_the_next_heading() -> None:
    text = "## Goal\nFirst line.\n\nSecond paragraph.\n### Detail\nkept\n## Inputs\n- a\n"
    assert section_body(text, "Goal") == "First line.\n\nSecond paragraph."


def test_section_body_puts_an_inline_value_first() -> None:
    assert section_body("## Notes: first\nsecond\n", "Notes") == "first\nsecond"


def test_first_matching_heading_wins() -> None:
    assert field_value("## Status\nopen\n\n## Status\ndone\n", "Status") == "open"


# --- file readers -------------------------------------------------------------


def test_read_field_and_read_body_from_a_file(tmp_path: pathlib.Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Task\n\n## Role\nCODER\n\n## Goal\nDo it.\nWell.\n", encoding="utf-8")
    assert read_field(readme, "Role") == "CODER"
    assert read_body(readme, "Goal") == "Do it.\nWell."


def test_file_readers_on_a_missing_file(tmp_path: pathlib.Path) -> None:
    assert read_field(tmp_path / "absent.md", "Role") == ""
    assert read_body(tmp_path / "absent.md", "Role") is None


def test_file_readers_on_an_undecodable_file(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"## Role\n\xff\xfe\n")
    assert read_field(bad, "Role") == ""


# --- set_status_text -----------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("# B\n\n## Status\nopen\n\n## Category\nwake\n", "# B\n\n## Status\nrunning\n\n## Category\nwake\n"),
        ("# B\n\n## Status\n\nopen\n\n## PM Task\n\nnone\n", "# B\n\n## Status\nrunning\n\n## PM Task\n\nnone\n"),
        ("# B\n\n## Status\n\n## Category\nwake\n", "# B\n\n## Status\nrunning\n\n## Category\nwake\n"),
        ("# B\n\n## Status: open\n\n## Category\nwake\n", "# B\n\n## Status\nrunning\n\n## Category\nwake\n"),
        ("# B\n\n## Status\nopen", "# B\n\n## Status\nrunning\n"),
    ],
    ids=["two-line", "bundle-blank-line", "empty-body", "inline-value", "last-no-newline"],
)
def test_set_status_text_rewrites_a_present_status(before: str, after: str) -> None:
    assert set_status_text(before, " running ") == after


def test_set_status_text_inserts_an_absent_status_after_the_title() -> None:
    assert set_status_text("# B\n\n## Category\nwake\n", "open") == "# B\n\n## Status\nopen\n\n## Category\nwake\n"


def test_set_status_text_inserts_at_the_start_without_a_title() -> None:
    assert set_status_text("## Category\nwake\n", "open") == "\n## Status\nopen\n## Category\nwake\n"


def test_set_status_text_leaves_status_notes_alone() -> None:
    text = "# B\n\n## Status Notes\nkeep\n"
    assert set_status_text(text, "done") == "# B\n\n## Status\ndone\n\n## Status Notes\nkeep\n"


# --- set_value_line ------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("## PM Task\n\nnone\n\n## Notes\nn\n", "## PM Task\n\nPM-1\n\n## Notes\nn\n"),
        ("## PM Task\nnone\n## Notes\nn\n", "## PM Task\nPM-1\n## Notes\nn\n"),
        ("## PM Task\n\n  none  \nmore\n\n## Notes\n", "## PM Task\n\n  PM-1\nmore\n\n## Notes\n"),
        ("## PM Task\n\nnone", "## PM Task\n\nPM-1\n"),
        ("## PM Task\n\n## Notes\nn\n", "## PM Task\nPM-1\n\n## Notes\nn\n"),
        ("## PM Task: none\n\n## Notes\n", "## PM Task\nPM-1\n\n## Notes\n"),
    ],
    ids=["blank-line-kept", "two-line", "indent-kept-rest-kept", "eof-gains-newline", "empty-body", "inline-value"],
)
def test_set_value_line_replaces_only_the_value_line(before: str, after: str) -> None:
    assert set_value_line(before, "PM Task", "PM-1") == after


def test_set_value_line_inserts_before_the_first_heading_after_status() -> None:
    text = "# R\n\n## Status\n\nopen\n\n## Target Version\n\nv1\n"
    assert set_value_line(text, "PM Task", "PM-1") == (
        "# R\n\n## Status\n\nopen\n\n## PM Task\n\nPM-1\n\n## Target Version\n\nv1\n"
    )


@pytest.mark.parametrize("text", ["# R\n\n## Status\n\nopen\n", "# R\n\n## Status\n\nopen"])
def test_set_value_line_appends_when_status_is_the_only_heading(text: str) -> None:
    assert set_value_line(text, "PM Task", "PM-1") == "# R\n\n## Status\n\nopen\n\n## PM Task\n\nPM-1\n"


# --- command line ---------------------------------------------------------------


def test_cli_read_prints_the_value(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "b.md"
    path.write_text("## Status\n\n**running**\n", encoding="utf-8")
    assert main(["read", str(path), "Status"]) == 0
    assert capsys.readouterr().out == "running\n"


def test_cli_read_empty_field_prints_an_empty_line(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "b.md"
    path.write_text("## PM Task\n\n## Notes\nn\n", encoding="utf-8")
    assert main(["read", str(path), "PM Task"]) == 0
    assert capsys.readouterr().out == "\n"


def test_cli_read_absent_heading_or_file_exits_3_silently(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "b.md"
    path.write_text("## Notes\nn\n", encoding="utf-8")
    assert main(["read", str(path), "Status"]) == EXIT_FIELD_ABSENT == 3
    assert main(["read", str(tmp_path / "absent.md"), "Status"]) == 3
    assert capsys.readouterr() == ("", "")


def test_cli_read_tolerates_undecodable_bytes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "b.md"
    path.write_bytes(b"## Notes\n\xff\n\n## Status\ndone\n")
    assert main(["read", str(path), "Status"]) == 0
    assert capsys.readouterr().out == "done\n"


def test_cli_render_commands_print_and_write_nothing(
    tmp_path: pathlib.Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    path = tmp_path / "b.md"
    text = "# R — bundle\n\n## Status\n\nopen\n\n## PM Task\n\nnone\n"
    path.write_text(text, encoding="utf-8")
    assert main(["render-status", str(path), "running"]) == 0
    assert capsysbinary.readouterr().out == set_status_text(text, "running").encode("utf-8")
    assert main(["render-value", str(path), "PM Task", "PM-1"]) == 0
    assert capsysbinary.readouterr().out == set_value_line(text, "PM Task", "PM-1").encode("utf-8")
    assert path.read_text(encoding="utf-8") == text


def test_cli_render_unreadable_file_fails_plainly(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["render-status", str(tmp_path / "absent.md"), "open"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("cannot read ")


def test_cli_refuses_bad_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert main(["read", "only-one-arg"]) == 1
    assert "usage:" in capsys.readouterr().err

"""
test_lib_text.py
================
Behavioural tests for pgai_agent_kanban.lib.text: the three ways a text is
flattened onto one line, and that each keeps exactly the spacing its
docstring promises.
"""

from __future__ import annotations

import pytest

from pgai_agent_kanban.lib.text import breaks_to_spaces, join_lines, one_line


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "plain"),
        ("a\nb", "a b"),
        ("a\r\nb", "a b"),
        ("  lead and trail  ", "lead and trail"),
        ("tabs\tand   runs", "tabs and runs"),
        ("\n\n", ""),
        ("", ""),
    ],
)
def test_one_line_collapses_every_whitespace_run(value: str, expected: str) -> None:
    assert one_line(value) == expected


def test_one_line_reads_any_value_through_str() -> None:
    assert one_line(ValueError("bad\n  input")) == "bad input"
    assert one_line(42) == "42"
    assert one_line(None) == "None"


def test_one_line_of_a_missing_value_is_empty_when_the_caller_says_so() -> None:
    # The documented spelling for callers where a missing value is no text.
    assert one_line(None or "") == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("why  it  stopped", "why  it  stopped"),
        ("first line  \n   second", "first line second"),
        ("a\r\n\r\nb", "a b"),
        ("  \nedge\n  ", "edge"),
        ("tab\tkept", "tab\tkept"),
    ],
)
def test_join_lines_replaces_breaks_and_keeps_inline_spacing(value: str, expected: str) -> None:
    assert join_lines(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/path/with  two spaces", "/path/with  two spaces"),
        (" a\r\nb ", " a  b "),
        ("one\ntwo", "one two"),
        ("\t", "\t"),
    ],
)
def test_breaks_to_spaces_changes_only_cr_and_lf(value: str, expected: str) -> None:
    assert breaks_to_spaces(value) == expected


@pytest.mark.parametrize("flatten", [one_line, join_lines, breaks_to_spaces])
def test_no_flattening_leaves_a_line_break(flatten) -> None:
    assert not set(flatten("x\ny\r\nz\rw")) & {"\r", "\n"}

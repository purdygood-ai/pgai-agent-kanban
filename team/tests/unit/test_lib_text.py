"""
test_lib_text.py
================
Behavioural tests for pgai_agent_kanban.lib.text: the three ways a text is
flattened onto one line, and that each keeps exactly the spacing its
docstring promises -- and that the bash flattener overwatch_flatten_field
(scripts/lib/overwatch_protocol.sh) returns the same bytes as one_line for
a value holding a tab, CR or LF, whatever the locale bash runs under.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.text import breaks_to_spaces, join_lines, one_line
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]


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


# ---------------------------------------------------------------------------
# One rule in two languages: overwatch_flatten_field agrees with one_line
# ---------------------------------------------------------------------------

#: Non-ASCII spaces, each combined below with a tab, a CR and a LF.
_UNICODE_SPACES = ("\u00a0", "\u2003", "\u3000")
_BREAKS = ("\t", "\r", "\n")

#: Every fixture value holds one of the spaces above next to one of the breaks.
_FLATTEN_FIXTURES = [
    shape.format(s=space, b=brk)
    for space in _UNICODE_SPACES
    for brk in _BREAKS
    for shape in ("a{s}b{b}c", "{s}{b}lead", "trail{b}{s}", "x{s}{b}{s}y", "{s}{s}{b}{s}")
]

#: Every character Python's str.split() treats as whitespace.
_PYTHON_WHITESPACE = [chr(c) for c in range(sys.maxunicode + 1) if chr(c).isspace()]

_LOCALES = ("C", "C.UTF-8")


def _bash_flatten(tmp_path: pathlib.Path, values: list[str], locale: str) -> list[bytes]:
    """Each of *values* through overwatch_flatten_field, as the raw bytes it
    assigned, from one ``bash -c`` call under ``LC_ALL=<locale>``."""
    script = (
        "source scripts/lib/overwatch_protocol.sh || exit 9\n"
        'for v in "$@"; do overwatch_flatten_field out "$v"; printf \'%s\\0\' "$out"; done\n'
    )
    env = build_scrubbed_env(home_dir=tmp_path / "home", lang=locale, extra_env={"LC_ALL": locale})
    result = subprocess.run(
        ["bash", "-c", script, "flatten", *(v.encode() for v in values)],
        capture_output=True,
        cwd=_TEAM_ROOT,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, f"bash exited {result.returncode} under LC_ALL={locale}: {result.stderr!r}"
    assert result.stdout.endswith(b"\0"), f"no output under LC_ALL={locale}: {result.stderr!r}"
    return result.stdout[:-1].split(b"\0")


@pytest.mark.parametrize("locale", _LOCALES)
def test_bash_flattener_returns_one_line_bytes_for_unicode_spaces_beside_breaks(
    tmp_path: pathlib.Path, locale: str
) -> None:
    got = _bash_flatten(tmp_path, _FLATTEN_FIXTURES, locale)
    expected = [one_line(v).encode() for v in _FLATTEN_FIXTURES]
    mismatches = [(v, g, e) for v, g, e in zip(_FLATTEN_FIXTURES, got, expected, strict=True) if g != e]
    assert not mismatches, f"bash and one_line disagree under LC_ALL={locale}: {mismatches!r}"


@pytest.mark.parametrize("locale", _LOCALES)
def test_bash_flattener_collapses_every_character_str_split_treats_as_whitespace(
    tmp_path: pathlib.Path, locale: str
) -> None:
    values = [f"a{ch}b\n" for ch in _PYTHON_WHITESPACE]
    got = _bash_flatten(tmp_path, values, locale)
    kept = [f"U+{ord(ch):04X}" for ch, g in zip(_PYTHON_WHITESPACE, got, strict=True) if g != b"a b"]
    assert not kept, f"bash kept these str.split() separators under LC_ALL={locale}: {kept}"


@pytest.mark.parametrize("locale", _LOCALES)
def test_bash_flattener_passes_a_value_without_tab_cr_or_lf_through_unchanged(
    tmp_path: pathlib.Path, locale: str
) -> None:
    values = ["a\u00a0b", " lead and  trail ", "x\u3000\u2003y", "\u00a0"]
    assert _bash_flatten(tmp_path, values, locale) == [v.encode() for v in values]

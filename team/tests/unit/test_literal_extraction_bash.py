"""
test_literal_extraction_bash.py
================================
Behavioral unit tests for team/scripts/lib/literal-extraction.sh, with a
focus on literal_grep_recorded_output — the recorded-output fixture search
that runs alongside literal_grep_tests's assertion-context search. A
recorded golden carries no assertion syntax, so it is invisible to the
assertion-context search by construction; this second search closes that
blind spot without touching the first.

Tests source the shell script and invoke each function via the bash harness,
asserting on stdout and the file:line:content shape both grep functions
share. A dedicated test proves the two searches stay distinct: a bare
golden-shaped line that literal_grep_recorded_output finds is NOT found by
literal_grep_tests, which is the whole reason the recorded-output search
exists as its own function rather than a relaxed assertion pattern.
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/literal-extraction.sh"


def _source(func_call: str) -> str:
    return f"source {_LIB} && {func_call}"


# ---------------------------------------------------------------------------
# literal_grep_recorded_output: bare matches, no assertion-context constraint
# ---------------------------------------------------------------------------


def test_finds_bare_match_in_recorded_output_fixture(tmp_path: pathlib.Path) -> None:
    """A recorded-output file with no assertion syntax at all is still a hit."""
    fixture_dir = tmp_path / "fixtures" / "engine-baseline"
    fixture_dir.mkdir(parents=True)
    fixture_file = fixture_dir / "board-render.txt"
    fixture_file.write_text(
        "=== CODER ===\n"
        "[dashboard-snapshot] column-render CODER: no fresh snapshot "
        "(missing, unreadable, or older than 5s) -- rendering directly\n",
        encoding="utf-8",
    )

    result = run_bash(
        tmp_path,
        _source(
            f'literal_grep_recorded_output '
            f'\'"no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly"\' '
            f"{fixture_dir!s}"
        ),
    )
    assert result.returncode == 0
    assert str(fixture_file) in result.stdout
    # file:line:content shape, matching literal_grep_tests's output shape.
    assert f"{fixture_file}:2:" in result.stdout


def test_returns_empty_when_literal_is_absent(tmp_path: pathlib.Path) -> None:
    """No occurrence anywhere under the recorded-output dir: empty output."""
    fixture_dir = tmp_path / "fixtures" / "golden"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "summary.json").write_text('{"status": "ok"}\n', encoding="utf-8")

    result = run_bash(
        tmp_path,
        _source(f"literal_grep_recorded_output '\"nonexistent-value\"' {fixture_dir!s}"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_skips_a_nonexistent_directory_without_error(tmp_path: pathlib.Path) -> None:
    """A candidate recorded-output dir that doesn't exist is silently skipped."""
    missing_dir = tmp_path / "fixtures" / "does-not-exist"

    result = run_bash(
        tmp_path,
        _source(f"literal_grep_recorded_output '\"some-value\"' {missing_dir!s}"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_aggregates_hits_across_multiple_recorded_output_dirs(
    tmp_path: pathlib.Path,
) -> None:
    """Passing several directories reports a hit from each that has one."""
    engine_baseline = tmp_path / "fixtures" / "engine-baseline"
    golden = tmp_path / "fixtures" / "golden"
    engine_baseline.mkdir(parents=True)
    golden.mkdir(parents=True)

    (engine_baseline / "board-render.txt").write_text(
        "shared-marker-value\n", encoding="utf-8"
    )
    (golden / "summary.json").write_text(
        '{"note": "shared-marker-value"}\n', encoding="utf-8"
    )

    result = run_bash(
        tmp_path,
        _source(
            f"literal_grep_recorded_output 'shared-marker-value' "
            f"{engine_baseline!s} {golden!s}"
        ),
    )
    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 2
    assert any(str(engine_baseline / "board-render.txt") in line for line in lines)
    assert any(str(golden / "summary.json") in line for line in lines)


def test_binary_fixture_does_not_crash_the_search(tmp_path: pathlib.Path) -> None:
    """A non-text file in the recorded-output dir is skipped, not fatal."""
    fixture_dir = tmp_path / "fixtures" / "engine-baseline"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "binary.dat").write_bytes(b"\x00\x01\x02marker-value\x00\x03")

    result = run_bash(
        tmp_path,
        _source(f"literal_grep_recorded_output 'marker-value' {fixture_dir!s}"),
    )
    assert result.returncode == 0
    # --binary-files=without-match means the binary file never surfaces a hit.
    assert "Binary file" not in result.stdout
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# The two searches stay distinct — literal_grep_tests never sees a bare
# recorded-output line, by construction (no assertion syntax to anchor on).
# ---------------------------------------------------------------------------


def test_grep_tests_still_cannot_see_a_bare_recorded_output_line(
    tmp_path: pathlib.Path,
) -> None:
    """literal_grep_tests's assertion-context constraint is untouched: a bare
    golden line with no assertion keyword is invisible to it, by design
    -- only literal_grep_recorded_output covers that case,
    on a caller-selected directory list, never by relaxing this pattern."""
    fixture_dir = tmp_path / "fixtures" / "engine-baseline"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "board-render.txt").write_text(
        "no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly\n",
        encoding="utf-8",
    )

    result = run_bash(
        tmp_path,
        _source(
            'literal_grep_tests '
            '\'"no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly"\' '
            f"{fixture_dir!s}"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_grep_recorded_output_finds_what_grep_tests_misses(
    tmp_path: pathlib.Path,
) -> None:
    """Same fixture, same literal: literal_grep_tests reports nothing while
    literal_grep_recorded_output reports the hit -- proving the new search
    surface reaches a bare recorded-output line the assertion-context
    search cannot, without widening that existing search."""
    fixture_dir = tmp_path / "fixtures" / "engine-baseline"
    fixture_dir.mkdir(parents=True)
    literal_line = "no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly"
    (fixture_dir / "board-render.txt").write_text(literal_line + "\n", encoding="utf-8")

    quoted_literal = f'\'"{literal_line}"\''

    tests_result = run_bash(
        tmp_path,
        _source(f"literal_grep_tests {quoted_literal} {fixture_dir!s}"),
    )
    recorded_result = run_bash(
        tmp_path,
        _source(f"literal_grep_recorded_output {quoted_literal} {fixture_dir!s}"),
    )

    assert tests_result.stdout.strip() == ""
    assert "board-render.txt:1:" in recorded_result.stdout

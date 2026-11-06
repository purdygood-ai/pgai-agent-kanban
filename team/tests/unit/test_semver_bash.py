"""
test_semver_bash.py
===================
Behavioral unit tests for team/scripts/lib/semver.sh.

Tests source the shell script and invoke each function via the bash harness,
asserting on stdout and exit codes.  No full wake cycle is invoked.

All semver comparisons use sort -V (GNU version sort), which handles numeric
ordering across decade boundaries (e.g. v0.9.x < v0.10.x).

Key behavioral requirements:
  - semver_compare: echoes -1, 0, or 1
  - semver_lt/lte/gt/gte/eq: returns 0 (true) or 1 (false) via exit code
  - semver_from_filename: extracts first vX.Y.Z token from filename basename
  - All functions accept versions with or without a leading 'v' prefix
  - The bash helpers and pgai_agent_kanban.lib.semver give the same answer
    for the same versions and filenames
"""

from __future__ import annotations

import itertools
import pathlib

import pytest

from pgai_agent_kanban.lib import semver
from tests.unit.shell_harness import run_bash

# Path to the script under test, relative to the team/ directory where pytest runs.
# pytest's working directory is the team/ subdirectory (set by run-unit-tests.sh).
_LIB = "scripts/lib/semver.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources semver.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


# ---------------------------------------------------------------------------
# semver_compare: numeric ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        # semver_compare: numeric ordering, echoes -1/0/1
        ("semver_compare v1.2.3 v1.2.3", "0"),  # identical versions
        ("semver_compare v0.9.7 v0.17.1", "-1"),  # first strictly less
        ("semver_compare v0.17.1 v0.9.7", "1"),  # first strictly greater
        ("semver_compare v2.0.0 v1.99.99", "1"),  # major dominates minor
        ("semver_compare v0.17.0 v0.17.1", "-1"),  # patch breaks tie at same major.minor
        ("semver_compare 1.2.3 v1.2.3", "0"),  # leading 'v' prefix stripped
        ("semver_compare V1.2.3 v1.2.3", "0"),  # uppercase 'V' prefix stripped
        ("semver_compare v0.0.0 v0.0.1", "-1"),  # v0.0.0 is the minimum version
        ("semver_compare v0.102.0 v0.101.9", "1"),  # large minor version numbers
        # semver_lt/lte/gt/gte/eq: exit 0 (true) -> echoes 'yes', else 'no'
        ("semver_lt v0.9.7 v0.17.1 && echo yes || echo no", "yes"),
        ("semver_lt v1.0.0 v1.0.0 && echo yes || echo no", "no"),
        ("semver_lt v0.17.1 v0.9.7 && echo yes || echo no", "no"),
        ("semver_lte v1.2.3 v1.2.3 && echo yes || echo no", "yes"),
        ("semver_lte v0.1.0 v0.2.0 && echo yes || echo no", "yes"),
        ("semver_lte v2.0.0 v1.99.0 && echo yes || echo no", "no"),
        ("semver_gt v0.17.1 v0.9.7 && echo yes || echo no", "yes"),
        ("semver_gt v1.0.0 v1.0.0 && echo yes || echo no", "no"),
        ("semver_gt v0.9.7 v0.17.1 && echo yes || echo no", "no"),
        ("semver_gte v1.5.0 v1.5.0 && echo yes || echo no", "yes"),
        ("semver_gte v1.6.0 v1.5.9 && echo yes || echo no", "yes"),
        ("semver_gte v0.0.1 v0.1.0 && echo yes || echo no", "no"),
        ("semver_eq v1.2.3 v1.2.3 && echo yes || echo no", "yes"),
        ("semver_eq v1.2.3 v1.2.4 && echo yes || echo no", "no"),
        ("semver_eq 1.2.3 v1.2.3 && echo yes || echo no", "yes"),  # prefix mismatch treated as equal
        # semver_from_filename: extract first vX.Y.Z token from basename
        ("semver_from_filename 'changelog-v0.17.1.md'", "v0.17.1"),
        ("semver_from_filename '/releases/v1.0.0/notes-v0.17.1.txt'", "v0.17.1"),  # basename only
        ("semver_from_filename 'README.md'", ""),  # no version token present
        ("semver_from_filename 'migrate-v0.9.7-to-v0.17.1.sql'", "v0.9.7"),  # first token only
        (
            "semver_from_filename '/srv/v1.0.0/artifacts/release-v2.3.4.tar.gz'",
            "v2.3.4",
        ),  # skips version in directory path components
    ],
)
def test_semver_bash_behaviors(tmp_path: pathlib.Path, command: str, expected: str) -> None:
    """semver_compare/lt/lte/gt/gte/eq/from_filename behaviors across ordering, prefix, and filename shapes."""
    result = run_bash(tmp_path, _source(command))
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_compare_numeric_ordering_not_lexicographic(tmp_path: pathlib.Path) -> None:
    """semver_compare uses numeric tuple ordering, not string ordering.

    Lexicographic ordering would incorrectly report v0.9.7 > v0.17.1 because
    '9' > '1'.  Numeric ordering correctly reports v0.9.7 < v0.17.1.
    """
    result = run_bash(tmp_path, _source("semver_compare v0.9.7 v0.17.1"))
    assert result.returncode == 0
    assert result.stdout.strip() == "-1", "v0.9.7 must be less than v0.17.1 under numeric ordering"


def test_from_filename_result_starts_with_v(tmp_path: pathlib.Path) -> None:
    """semver_from_filename always returns a 'v'-prefixed token."""
    result = run_bash(
        tmp_path,
        _source("semver_from_filename 'release-v2.0.0-notes.txt'"),
    )
    assert result.returncode == 0
    output = result.stdout.strip()
    assert output.startswith("v"), f"Expected output starting with 'v'; got {output!r}"


# ---------------------------------------------------------------------------
# Agreement with pgai_agent_kanban.lib.semver: both runtimes read the same
# versions and filenames and must give the same verdict for each.
# ---------------------------------------------------------------------------

# Every ordered pair of these is compared on both sides: the text-order trap
# (v0.9.7 / v0.17.1), cross-decade components, prefix forms and leading zeros.
_AGREEMENT_VERSIONS = (
    "v0.9.7",
    "v0.17.1",
    "0.17.1",
    "V0.17.1",
    "v0.10.0",
    "v0.9.99",
    "v1.02.3",
    "v1.2.3",
    "v10.0.0",
    "v9.9.9",
)

_AGREEMENT_FILENAMES = (
    "v0.17.0-bugfix-cascade.md",
    "migrate-v0.9.7-to-v0.17.1.sql",
    "/srv/v1.0.0/artifacts/release-v2.3.4.tar.gz",
    "release-v10.0.0.md",
    "no-version-here.md",
    "v1.2-short.md",
)

# One bash process answers every query: "pair A B" prints semver_compare and
# the lt/lte/gt/gte/eq verdicts, "file NAME" prints semver_from_filename.
_AGREEMENT_DRIVER = r"""
source scripts/lib/semver.sh
verdict() { if "$@"; then echo yes; else echo no; fi; }
while IFS=$'\t' read -r kind a b; do
    if [[ "$kind" == pair ]]; then
        echo "pair $a $b: $(semver_compare "$a" "$b") $(verdict semver_lt "$a" "$b")" \
            "$(verdict semver_lte "$a" "$b") $(verdict semver_gt "$a" "$b")" \
            "$(verdict semver_gte "$a" "$b") $(verdict semver_eq "$a" "$b")"
    else
        echo "file $a: $(semver_from_filename "$a")"
    fi
done
"""


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _python_answers(pairs: list[tuple[str, str]], filenames: tuple[str, ...]) -> list[str]:
    """The driver's output lines as lib/semver.py answers the same queries."""
    lines = []
    for a, b in pairs:
        verdicts = " ".join(map(_yes, (semver.lt(a, b), semver.le(a, b), semver.gt(a, b), semver.ge(a, b))))
        lines.append(f"pair {a} {b}: {semver.compare(a, b)} {verdicts} {_yes(semver.eq(a, b))}")
    lines.extend(f"file {name}: {semver.from_filename(name)}" for name in filenames)
    return lines


def test_bash_and_python_semver_agree_on_every_pair_and_filename(tmp_path: pathlib.Path) -> None:
    """semver.sh and lib/semver.py order every version pair and read every filename alike."""
    pairs = list(itertools.product(_AGREEMENT_VERSIONS, repeat=2))
    stdin = "".join(f"pair\t{a}\t{b}\n" for a, b in pairs)
    stdin += "".join(f"file\t{name}\n" for name in _AGREEMENT_FILENAMES)

    result = run_bash(tmp_path, _AGREEMENT_DRIVER, stdin=stdin, timeout=120)

    assert result.returncode == 0, result.stderr
    expected = _python_answers(pairs, _AGREEMENT_FILENAMES)
    assert result.stdout.splitlines() == expected
    assert "pair v0.9.7 v0.17.1: -1 yes yes no no no" in expected

"""
test_discovery_defer_gate.py
=============================
Unit tests for team/scripts/lib/discovery_defer.sh's pure, individually
testable primitives:

  - _disc_bug_severity          — reads the **Severity:** field from a bug
                                   file, or "" when absent/unreadable.
  - _disc_severity_rank         — the severity word's numeric rank on the
                                   low < medium < high < critical scale.
  - _disc_severity_defer_decision — the severity-vs-posture decision:
                                   "deferred" | "kept" | "unparseable".

These are unit tests: no project context, no kanban root, no discovery
pipeline. The full bug-step behaviour (bundling, the hygiene sweep, the
requirements-step untouched guarantee) is covered at the integration layer
in team/tests/integration/test_discovery_bug_deferral_gate.py, matching the
pattern test_defer_posture_resolver.py uses for defer_posture.sh: invoke
the real production shell code via subprocess (bash -c "source ... && ..."),
never a reimplementation of it.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DISCOVERY_DEFER = _TEAM_DIR / "scripts" / "lib" / "discovery_defer.sh"


def _run_bash(script: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_bug_file(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    bug_file = tmp_path / "BUG-0001-test.md"
    bug_file.write_text(body, encoding="utf-8")
    return bug_file


def _severity_of(bug_file: pathlib.Path) -> subprocess.CompletedProcess:
    script = f"source {str(_DISCOVERY_DEFER)!r} && _disc_bug_severity {str(bug_file)!r}"
    return _run_bash(script)


def _decision(severity_raw: str, level: str) -> subprocess.CompletedProcess:
    script = (
        f"source {str(_DISCOVERY_DEFER)!r} && "
        f"_disc_severity_defer_decision {severity_raw!r} {level!r}"
    )
    return _run_bash(script)


def _rank(value: str) -> subprocess.CompletedProcess:
    script = f"source {str(_DISCOVERY_DEFER)!r} && _disc_severity_rank {value!r}"
    return _run_bash(script)


# ---------------------------------------------------------------------------
# _disc_bug_severity
# ---------------------------------------------------------------------------


class TestBugSeverityExtraction:
    def test_reads_the_severity_field_verbatim(self, tmp_path: pathlib.Path) -> None:
        bug_file = _write_bug_file(
            tmp_path,
            "# BUG-0001: test\n\n**Severity:** medium\n\n## Status\nopen\n",
        )
        result = _severity_of(bug_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "medium"

    def test_is_tolerant_of_extra_whitespace_around_the_value(
        self, tmp_path: pathlib.Path
    ) -> None:
        bug_file = _write_bug_file(
            tmp_path,
            "# BUG-0001: test\n\n**Severity:**    high   \n\n## Status\nopen\n",
        )
        result = _severity_of(bug_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "high"

    def test_missing_field_yields_empty_string(self, tmp_path: pathlib.Path) -> None:
        bug_file = _write_bug_file(
            tmp_path, "# BUG-0001: test\n\n## Status\nopen\n"
        )
        result = _severity_of(bug_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_unreadable_file_yields_empty_string_rather_than_an_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        missing = tmp_path / "does-not-exist.md"
        result = _severity_of(missing)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_is_case_insensitive_on_the_field_label(self, tmp_path: pathlib.Path) -> None:
        bug_file = _write_bug_file(
            tmp_path,
            "# BUG-0001: test\n\n**severity:** low\n\n## Status\nopen\n",
        )
        result = _severity_of(bug_file)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "low"


# ---------------------------------------------------------------------------
# _disc_severity_rank
# ---------------------------------------------------------------------------


class TestSeverityRank:
    @pytest.mark.parametrize(
        "value,expected_rank",
        [
            ("low", 1),
            ("medium", 2),
            ("high", 3),
            ("critical", 4),
        ],
    )
    def test_ranks_each_recognised_rung(self, value: str, expected_rank: int) -> None:
        result = _rank(value)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(expected_rank)

    def test_critical_ranks_strictly_above_high(self) -> None:
        high = _rank("high")
        critical = _rank("critical")
        assert high.returncode == 0, high.stderr
        assert critical.returncode == 0, critical.stderr
        assert int(critical.stdout.strip()) > int(high.stdout.strip())

    def test_is_case_insensitive_for_critical(self) -> None:
        result = _rank("CRITICAL")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "4"

    def test_tolerates_surrounding_whitespace_and_trailing_period_for_critical(
        self,
    ) -> None:
        result = _rank(" critical. ")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "4"

    def test_unrecognised_value_yields_no_rank(self) -> None:
        result = _rank("urgent")
        assert result.returncode == 1
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# _disc_severity_defer_decision
# ---------------------------------------------------------------------------


class TestSeverityDeferDecision:
    @pytest.mark.parametrize(
        "severity,level,expected",
        [
            # At-or-below-the-level rule on the low < medium < high <
            # critical scale. deferred_bug_level only ever resolves to
            # low|medium|high (critical is not a settable level -- see
            # _DISC_SEVERITY_VOCAB in discovery_defer.sh), so critical is
            # exercised against all three accepted levels below and never
            # against itself as a level.
            ("low", "low", "deferred"),
            ("low", "medium", "deferred"),
            ("low", "high", "deferred"),
            ("medium", "low", "kept"),
            ("medium", "medium", "deferred"),
            ("medium", "high", "deferred"),
            ("high", "low", "kept"),
            ("high", "medium", "kept"),
            ("high", "high", "deferred"),
            ("critical", "low", "kept"),
            ("critical", "medium", "kept"),
            ("critical", "high", "kept"),
        ],
    )
    def test_at_or_below_the_level_is_deferred(
        self, severity: str, level: str, expected: str
    ) -> None:
        result = _decision(severity, level)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    def test_is_case_insensitive(self) -> None:
        result = _decision("HIGH", "High")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "deferred"

    def test_tolerates_surrounding_whitespace_and_trailing_period(self) -> None:
        result = _decision(" low. ", "low")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "deferred"

    def test_empty_severity_is_unparseable_never_silently_deferred(self) -> None:
        result = _decision("", "low")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "unparseable"

    def test_severity_outside_the_recognised_vocabulary_is_unparseable(self) -> None:
        # "urgent" is neither one of the three settable deferred_bug_level
        # rungs nor "critical" (which _DISC_SEVERITY_VOCAB does recognise
        # as a rung above high) -- it is a genuinely unrecognised value.
        result = _decision("urgent", "high")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "unparseable"

    def test_critical_is_never_unparseable(self) -> None:
        # The guarded regression: critical is a real rung, not
        # an error-path fallback, so it must never be reported the same
        # way as a typo or an absent field.
        result = _decision("critical", "high")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "kept"

    def test_typo_in_severity_is_unparseable(self) -> None:
        result = _decision("lwo", "high")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "unparseable"

    def test_unrecognised_level_is_unparseable_rather_than_a_guessed_comparison(
        self,
    ) -> None:
        # Defensive: resolve_defer_posture always validates the level to
        # low|medium|high, but the decision function is unit-tested directly
        # with arbitrary strings and must never guess a comparison.
        result = _decision("low", "extreme")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "unparseable"

    def test_always_returns_zero_a_query_not_a_guard(self) -> None:
        result = _decision("bogus", "bogus")
        assert result.returncode == 0, result.stderr

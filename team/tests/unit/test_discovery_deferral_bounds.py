"""
test_discovery_deferral_bounds.py
==================================
Unit tests for the deferral bounds' two building blocks:

  - pgai_agent_kanban.discovery.items bug_filed_date / bug_ages, and its
    ``bug-ages`` command -- a bug file's ``**Date:**`` header read as a
    calendar date and an age in whole days.
  - _disc_deferral_bound_exceeded in team/scripts/lib/discovery_defer.sh --
    whether deferred bugs have waited past deferred_max_count or
    deferred_max_age_days, narrating the bound that fired.

The bash function runs as production code via subprocess
(bash -c "source ... && ..."), with a log() shim writing to stderr as
discovery.sh's own does. The full discovery pass (bundling, the
requirements queue, the hygiene sweep) is covered at the integration layer
in team/tests/integration/test_discovery_bug_deferral_bounds.py.

Naming: function names describe behaviour under test (roles/TESTER.md
"Test Authoring Guidelines").
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.discovery import items  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_DISCOVERY_DEFER = _TEAM_DIR / "scripts" / "lib" / "discovery_defer.sh"
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"


def _bug(directory: pathlib.Path, name: str, date_line: str | None) -> pathlib.Path:
    """Write a minimal bug file; *date_line* None omits the Date header."""
    path = directory / name
    header = f"**Date:** {date_line}\n" if date_line is not None else ""
    path.write_text(
        f"# {name}\n\n**Filed By:** tester\n{header}**Severity:** low\n\n## Status\nopen\n",
        encoding="utf-8",
    )
    return path


def _env(tmp_path: pathlib.Path) -> dict[str, str]:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch)},
    )


def _bound_exceeded(
    tmp_path: pathlib.Path, files: list[pathlib.Path], max_count: str, max_age_days: str
) -> subprocess.CompletedProcess:
    deferred = "\n".join(str(f) for f in files)
    script = (
        'log() { echo "$*" >&2; }\n'
        f'source "{_DISCOVERY_DEFER}"\n'
        '_disc_deferral_bound_exceeded "$1" "$2" "$3" "$4"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "bounds", deferred, str(len(files)), max_count, max_age_days],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        cwd=str(tmp_path),
        timeout=60,
    )


def _today() -> datetime.date:
    return datetime.datetime.now(datetime.UTC).date()


# ---------------------------------------------------------------------------
# The Date reader
# ---------------------------------------------------------------------------


class TestBugFiledDate:
    @pytest.mark.parametrize(
        "date_line, expected",
        [
            pytest.param("2026-03-04", datetime.date(2026, 3, 4), id="date"),
            pytest.param("2026-03-04T10:00:00+00:00", datetime.date(2026, 3, 4), id="iso-timestamp"),
            pytest.param("  2026-03-04   ", datetime.date(2026, 3, 4), id="padded"),
        ],
    )
    def test_reads_the_leading_calendar_date(
        self, tmp_path: pathlib.Path, date_line: str, expected: datetime.date
    ) -> None:
        bug = _bug(tmp_path, "BUG-9001-a.md", date_line)
        filed, raw = items.bug_filed_date(str(bug))
        assert filed == expected
        assert raw == date_line.strip()

    @pytest.mark.parametrize(
        "date_line, raw",
        [
            pytest.param(None, "", id="no-header"),
            pytest.param("YYYY-MM-DD", "YYYY-MM-DD", id="template-placeholder"),
            pytest.param("2026-02-30", "2026-02-30", id="not-a-calendar-date"),
            pytest.param("last tuesday", "last tuesday", id="words"),
        ],
    )
    def test_an_unparseable_or_missing_date_reads_as_none_with_its_raw_text(
        self, tmp_path: pathlib.Path, date_line: str | None, raw: str
    ) -> None:
        bug = _bug(tmp_path, "BUG-9001-a.md", date_line)
        assert items.bug_filed_date(str(bug)) == (None, raw)

    def test_an_unreadable_file_reads_as_none(self, tmp_path: pathlib.Path) -> None:
        assert items.bug_filed_date(str(tmp_path / "BUG-9009-missing.md")) == (None, "")

    def test_ages_count_whole_days_to_the_given_today(self, tmp_path: pathlib.Path) -> None:
        old = _bug(tmp_path, "BUG-9001-old.md", "2026-01-01")
        undated = _bug(tmp_path, "BUG-9002-undated.md", None)
        ages = items.bug_ages([str(old), "", str(undated)], today=datetime.date(2026, 1, 31))
        assert ages == [(str(old), 30, "2026-01-01"), (str(undated), None, "")]

    def test_bug_ages_command_prints_a_dash_for_an_unknown_age(self, tmp_path: pathlib.Path) -> None:
        dated = _bug(tmp_path, "BUG-9001-a.md", _today().isoformat())
        undated = _bug(tmp_path, "BUG-9002-b.md", "soon")
        result = subprocess.run(
            ["bash", str(_PP_RUN_OPS), "pgai_agent_kanban.discovery.items", "bug-ages"],
            input=f"{dated}\n{undated}\n",
            capture_output=True,
            text=True,
            env=_env(tmp_path),
            cwd=str(tmp_path),
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            f"{dated}\t0\t{_today().isoformat()}",
            f"{undated}\t-\tsoon",
        ]


# ---------------------------------------------------------------------------
# _disc_deferral_bound_exceeded
# ---------------------------------------------------------------------------


class TestDeferralBoundExceeded:
    def test_unset_bounds_never_fire_and_read_no_file(self, tmp_path: pathlib.Path) -> None:
        missing = [tmp_path / f"BUG-900{i}-gone.md" for i in range(1, 8)]
        result = _bound_exceeded(tmp_path, missing, "", "")
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == ""

    def test_count_bound_fires_only_when_strictly_exceeded(self, tmp_path: pathlib.Path) -> None:
        bugs = [_bug(tmp_path, f"BUG-900{i}-b.md", _today().isoformat()) for i in range(1, 7)]

        over = _bound_exceeded(tmp_path, bugs, "5", "")
        assert over.returncode == 0, over.stderr
        assert over.stdout == ""
        assert "deferral bound deferred_max_count=5 exceeded -- 6 bug(s) deferred" in over.stderr

        at = _bound_exceeded(tmp_path, bugs[:5], "5", "")
        assert at.returncode == 1
        assert "deferred_max_count" not in at.stderr

    def test_zero_count_bound_fires_on_a_single_deferred_bug(self, tmp_path: pathlib.Path) -> None:
        bug = _bug(tmp_path, "BUG-9001-b.md", _today().isoformat())
        result = _bound_exceeded(tmp_path, [bug], "0", "")
        assert result.returncode == 0, result.stderr

    def test_age_bound_fires_naming_the_oldest_bug(self, tmp_path: pathlib.Path) -> None:
        recent = _bug(tmp_path, "BUG-9001-recent.md", _today().isoformat())
        oldest = _bug(tmp_path, "BUG-9002-oldest.md", "2020-01-01")
        older = _bug(tmp_path, "BUG-9003-older.md", "2021-06-01")
        age = (_today() - datetime.date(2020, 1, 1)).days

        result = _bound_exceeded(tmp_path, [recent, oldest, older], "", "30")

        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert (
            f"deferral bound deferred_max_age_days=30 exceeded -- oldest deferred bug "
            f"BUG-9002-oldest dated 2020-01-01 is {age} day(s) old"
        ) in result.stderr

    def test_age_bound_holds_while_every_date_is_inside_it(self, tmp_path: pathlib.Path) -> None:
        yesterday = (_today() - datetime.timedelta(days=1)).isoformat()
        bugs = [
            _bug(tmp_path, "BUG-9001-a.md", _today().isoformat()),
            _bug(tmp_path, "BUG-9002-b.md", yesterday),
        ]
        result = _bound_exceeded(tmp_path, bugs, "", "1")
        assert result.returncode == 1
        assert "exceeded" not in result.stderr

    def test_a_bug_without_a_parseable_date_is_named_and_never_fires_the_age_bound(
        self, tmp_path: pathlib.Path
    ) -> None:
        undated = _bug(tmp_path, "BUG-9001-undated.md", None)
        placeholder = _bug(tmp_path, "BUG-9002-placeholder.md", "YYYY-MM-DD")

        result = _bound_exceeded(tmp_path, [undated, placeholder], "", "0")

        assert result.returncode == 1
        assert "Date field in BUG-9001-undated.md is missing or unparseable ('')" in result.stderr
        assert "Date field in BUG-9002-placeholder.md is missing or unparseable ('YYYY-MM-DD')" in result.stderr
        assert "exceeded" not in result.stderr

    def test_both_bounds_narrate_when_both_fire(self, tmp_path: pathlib.Path) -> None:
        bugs = [_bug(tmp_path, f"BUG-900{i}-b.md", "2020-01-01") for i in range(1, 4)]
        result = _bound_exceeded(tmp_path, bugs, "2", "30")
        assert result.returncode == 0, result.stderr
        assert "deferred_max_count=2 exceeded" in result.stderr
        assert "deferred_max_age_days=30 exceeded" in result.stderr

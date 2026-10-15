"""
test_schedule_window_bands_acceptance.py
==========================================
Fixture-driven acceptance coverage for the schedule window's three bands
(roster, status, event) exercised together, as one window, against seeded
fixtures rather than one renderer at a time.

WHY THIS EXISTS
---------------
next-cron-firings.sh (roster), status-band.sh (status), and event-band.sh
(event) each already carry their own per-renderer unit-test module
(test_schedule_roster_completeness.py, test_status_band_renderer.py,
test_event_band_renderer.py). Those modules stay as they are and remain
the deep coverage for each renderer's own internals. This module instead
locks in the redesign's own stated acceptance behaviour: what an operator
actually sees when the three bands render side by side in the schedule
window, at both the geometry the window normally has (default) and the
geometry a narrow terminal forces (narrow) -- so a later edit that
reintroduces boilerplate into the event stream, drops a roster entry at
default geometry, or blanks the empty-event-band message fails loudly
here even if it slips past a single-renderer test.

FIXTURE STRATEGY
-----------------
- **Crontab**: installed through the crontab-sandbox stub every test in
  this tree gets from the autouse `_sandbox_crontab` fixture in
  team/tests/conftest.py (`crontab -` writes to $HOME/.fake_crontab;
  never the operator's real crontab).
- **Kanban root**: a fresh `tmp_path`-scoped directory per test, never the
  operator's real kanban root and never a bare `/tmp` path -- pytest's
  `tmp_path` already resolves under the framework temp root via this
  tree's own `pytest_configure` redirect (team/tests/conftest.py).
- **Deterministic pane geometry**: a local `tmux` stub answers
  `tmux display-message -p -t <pane> '#{pane_height}'` /
  `'#{pane_width}'` with fixed values set via
  PGAI_TEST_PANE_HEIGHT / PGAI_TEST_PANE_WIDTH -- the same technique
  test_schedule_roster_completeness.py uses. "Default geometry" in this
  module means the stub is never installed at all: TMUX_PANE stays
  unset, so the renderer's own pane-height/width queries degrade to
  "unqueryable" exactly as they do outside tmux, which is how the roster
  band actually behaves when nothing constrains it. "Narrow geometry"
  installs the stub with a pane width too narrow for the columnar band to
  pack the roster (forcing the vertical fallback) and a pane height too
  short to hold the whole fallback list (forcing the "+N more" marker),
  the same width/height pairing already exercised behaviorally in that
  module's own short-viewport tests.
- Status-band.sh and event-band.sh render the same regardless of pane
  geometry (neither queries tmux), so only the roster-band assertions are
  repeated across both geometries; the status- and event-band assertions
  each appear once.

ACCEPTANCE CRITERIA COVERED
----------------------------
  1. Default geometry: the roster band shows every roster entry with no
     "+N more" marker, and the status band shows one row per roster
     agent (TestDefaultGeometryShowsFullRosterInBothBands).
  2. A fixture seeding one idle wake cycle and one dispatch wake cycle:
     the event band renders only the dispatch cycle's lines
     (TestEventBandFiltersIdleCycleKeepsDispatchCycle).
  3. A fixture root with no wake events for the current day: the event
     band prints the literal "no wake events yet today"
     (TestEventBandEmptyStateNarratesExplicitly).
  4. Narrow geometry: the roster band falls back to the vertical list and
     emits a "+N more" marker naming the correct dropped count
     (TestNarrowGeometryRosterBandFallsBackWithAccurateOverflowMarker).
"""

from __future__ import annotations

import datetime as _dt
import os
import pathlib
import re
import stat
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location, matching every
# other dashboard test module (never the directory pytest happened to be
# launched from).
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_DASHBOARD_DIR = _TEAM_DIR / "scripts" / "dashboard"
_ROSTER_BAND_SH = _DASHBOARD_DIR / "next-cron-firings.sh"
_STATUS_BAND_SH = _DASHBOARD_DIR / "status-band.sh"
_EVENT_BAND_SH = _DASHBOARD_DIR / "event-band.sh"

_TIMEOUT_SECONDS = 20

# The current seven-entry roster, in fixed roster order (pm first, then
# crontab order) -- the same crontab shape
# test_schedule_roster_completeness.py uses for its own full-roster
# fixtures. Sub-hourly rows fire every minute with a distinct --sleep=N
# stagger; weekly rows use a distinct day/hour.
_FULL_ROSTER_CRONTAB = (
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm     --sleep=0\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder  --sleep=12\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=writer --sleep=24\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester --sleep=36\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm     --sleep=48\n"
    "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n"
    "0 4 * * 0 $KANBAN_ROOT/scripts/cleanup.sh\n"
)
_ROSTER_ORDER = ["PM", "CODER", "WRITER", "TESTER", "CM", "OVERWATCH", "CLEANUP"]


def _today_iso(hour: int, minute: int, second: int) -> str:
    """An ISO-8601 timestamp for "today" at the given local time, in the
    same shape team/scripts/lib/wake_bootstrap.sh's log() function writes
    (`date -Iseconds`) -- date, "T", time, and the host's local UTC
    offset.
    """
    now = _dt.datetime.now().astimezone()
    stamped = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return stamped.isoformat(timespec="seconds")


def _write_log(path: pathlib.Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _install_crontab(text: str) -> None:
    """Install a synthetic crontab through the autouse crontab-sandbox stub
    (team/tests/conftest.py's `_sandbox_crontab`) -- writes to
    $HOME/.fake_crontab, never the real operator crontab."""
    result = subprocess.run(
        ["crontab", "-"],
        input=text,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=10,
    )
    assert result.returncode == 0, _diagnostic(result)


def _run(args: list, kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIMEOUT_SECONDS,
    )


def _run_roster_band(kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    return _run(
        ["bash", str(_ROSTER_BAND_SH), "--kanban-root", str(kanban_root)], kanban_root
    )


def _run_status_band(kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    return _run(
        ["bash", str(_STATUS_BAND_SH), "--kanban-root", str(kanban_root)], kanban_root
    )


def _run_event_band(kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    return _run(
        ["bash", str(_EVENT_BAND_SH), "--kanban-root", str(kanban_root)], kanban_root
    )


# An entry token is matched as a whole word anywhere in stdout -- the roster
# band's packed columnar row can carry several entries separated by
# PGAI_ROSTER_BAND_SEPARATOR, and the vertical fallback carries one per
# line, so a word-boundary search covers both shapes without assuming which
# one rendered.
def _entry_present(stdout: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", stdout) is not None


_MARKER_RE = re.compile(r"\+(\d+)\s+more")


def _marker_count(stdout: str):
    m = _MARKER_RE.search(stdout)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Narrow-pane stub -- shares the technique test_schedule_roster_completeness.py
# uses: a fake `tmux` binary answering a pane-height or pane-width query with
# a fixed value, so the roster band's width-triggered vertical fallback and
# height-based "+N more" narration can be exercised without a live tmux
# session. Only the roster band ever queries tmux; status-band.sh and
# event-band.sh render identically regardless.
# ---------------------------------------------------------------------------
_TMUX_STUB = """\
#!/usr/bin/env bash
# Test-only stub (test_schedule_window_bands_acceptance.py): answers a tmux
# pane-height OR pane-width query -- whichever format string the caller
# requested -- with a fixed value (PGAI_TEST_PANE_HEIGHT /
# PGAI_TEST_PANE_WIDTH respectively). Either dimension a test never sets
# degrades to "unqueryable" (non-zero exit), matching a real query failing
# outside tmux, never a guessed value. Any other tmux subcommand is refused.
if [[ "$1" == "display-message" ]]; then
  fmt="${@: -1}"
  if [[ "$fmt" == *pane_height* ]]; then
    printf '%s\\n' "${PGAI_TEST_PANE_HEIGHT:?PGAI_TEST_PANE_HEIGHT must be set}"
    exit 0
  elif [[ "$fmt" == *pane_width* ]]; then
    printf '%s\\n' "${PGAI_TEST_PANE_WIDTH:?PGAI_TEST_PANE_WIDTH must be set}"
    exit 0
  fi
fi
exit 1
"""


def _install_narrow_pane(tmp_path: pathlib.Path, monkeypatch, width: int, height: int) -> None:
    stub_dir = tmp_path / "_tmux_stub_bin"
    stub_dir.mkdir(exist_ok=True)
    stub_path = stub_dir / "tmux"
    stub_path.write_text(_TMUX_STUB, encoding="utf-8")
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setenv("PGAI_TEST_PANE_WIDTH", str(width))
    monkeypatch.setenv("PGAI_TEST_PANE_HEIGHT", str(height))


# ---------------------------------------------------------------------------
# AC1 -- default geometry: the roster band shows every roster entry with no
# overflow marker, and the status band shows one row per roster agent.
# Neither renderer has a tmux stub installed here -- pane height/width stay
# unqueryable, which is the roster band's actual behaviour outside tmux.
# ---------------------------------------------------------------------------


class TestDefaultGeometryShowsFullRosterInBothBands:
    def test_roster_band_renders_every_entry_no_overflow_marker(
        self, tmp_path: pathlib.Path
    ) -> None:
        kanban_root = tmp_path / "kanroot"
        (kanban_root / "logs").mkdir(parents=True)
        _install_crontab(_FULL_ROSTER_CRONTAB)

        result = _run_roster_band(kanban_root)
        assert result.returncode == 0, _diagnostic(result)

        shown = [tok for tok in _ROSTER_ORDER if _entry_present(result.stdout, tok)]
        assert shown == _ROSTER_ORDER, (
            f"expected all seven roster entries to render at default geometry; "
            f"got {shown}\n{_diagnostic(result)}"
        )
        assert _marker_count(result.stdout) is None, (
            f"no overflow marker expected when the whole roster fits\n"
            f"{_diagnostic(result)}"
        )

    def test_status_band_shows_one_row_per_roster_agent(
        self, tmp_path: pathlib.Path
    ) -> None:
        kanban_root = tmp_path / "kanroot"
        (kanban_root / "logs").mkdir(parents=True)
        _install_crontab(_FULL_ROSTER_CRONTAB)

        result = _run_status_band(kanban_root)
        assert result.returncode == 0, _diagnostic(result)

        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        data_rows = lines[1:]  # first line is the AGENT/LAST FIRED/... header
        assert len(data_rows) == len(_ROSTER_ORDER), (
            f"expected one status row per roster agent ({len(_ROSTER_ORDER)}); "
            f"got {len(data_rows)} data row(s)\n{_diagnostic(result)}"
        )
        for token in _ROSTER_ORDER:
            assert any(row.startswith(token) for row in data_rows), (
                f"expected a status row starting with {token!r}; "
                f"rows={data_rows}\n{_diagnostic(result)}"
            )


# ---------------------------------------------------------------------------
# AC2 -- a fixture seeding one idle wake cycle and one dispatch wake cycle:
# the event band renders only the dispatch cycle's lines. Line shapes are
# the literal wake_driver.sh boilerplate/signal text this window's event
# band is built to tell apart.
# ---------------------------------------------------------------------------


class TestEventBandFiltersIdleCycleKeepsDispatchCycle:
    def test_only_the_dispatch_cycles_lines_render(self, tmp_path: pathlib.Path) -> None:
        kanban_root = tmp_path / "kanroot"
        logs_dir = kanban_root / "logs"

        idle_lines = [
            f"[{_today_iso(8, 0, 0)}] wake(writer): "
            "re-checking WAITING tasks for satisfied prerequisites...",
            f"[{_today_iso(8, 0, 1)}] wake(writer): no WAITING tasks to re-check",
            f"[{_today_iso(8, 0, 2)}] wake(writer): "
            "discovery_run_pipeline: no work pending, idle exit",
            f"[{_today_iso(8, 0, 3)}] wake(writer): "
            "sleeping 21s before work (--sleep stagger)",
            f"[{_today_iso(8, 0, 4)}] wake(writer): "
            "timing: work_begin=1785107882 work_end=1785107882 work_delta=0s",
            f"[{_today_iso(8, 0, 5)}] wake(writer): project demo: "
            'done: completed=0 project_elapsed=3s batch_elapsed=3s '
            'reason="no more BACKLOG tasks in writer queue"',
        ]
        dispatch_lines = [
            f"[{_today_iso(9, 30, 0)}] wake(tester): task TESTER-example: "
            "transitioned to WORKING [A]",
            f"[{_today_iso(9, 31, 0)}] wake(tester): "
            "finished task TESTER-example with state DONE",
        ]
        _write_log(logs_dir / "cron-writer.log", idle_lines)
        _write_log(logs_dir / "cron-tester.log", dispatch_lines)

        result = _run_event_band(kanban_root)
        assert result.returncode == 0, _diagnostic(result)

        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert len(lines) == 2, (
            f"expected only the two dispatch-cycle lines to render, got: "
            f"{lines!r}\n{_diagnostic(result)}"
        )
        assert "transitioned to WORKING" in lines[0], _diagnostic(result)
        assert "finished task TESTER-example with state DONE" in lines[1], _diagnostic(
            result
        )

        for marker in ("re-checking", "idle exit", "sleeping", "timing:"):
            assert marker not in result.stdout, (
                f"idle-cycle boilerplate marker {marker!r} leaked into the "
                f"signal-only event band\n{_diagnostic(result)}"
            )


# ---------------------------------------------------------------------------
# AC3 -- a fixture root with no wake events for the current day prints the
# literal empty-state message rather than rendering blank.
# ---------------------------------------------------------------------------


class TestEventBandEmptyStateNarratesExplicitly:
    def test_no_wake_events_today_prints_literal_message(
        self, tmp_path: pathlib.Path
    ) -> None:
        kanban_root = tmp_path / "kanroot"
        (kanban_root / "logs").mkdir(parents=True)

        result = _run_event_band(kanban_root)
        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "no wake events yet today", _diagnostic(result)


# ---------------------------------------------------------------------------
# AC4 -- narrow geometry: the roster band falls back to the vertical list
# and narrates the drop with a "+N more" marker naming the correct count.
# Width 20 is well below any single roster entry's own length (forcing the
# vertical fallback rather than the columnar band); height 4 leaves a
# one-row budget after the fallback's own reserved header/footer rows --
# the same width/height pairing test_schedule_roster_completeness.py
# exercises for this renderer's own short-viewport behaviour.
# ---------------------------------------------------------------------------

_NARROW_PANE_WIDTH = 20
_NARROW_PANE_HEIGHT = 4


class TestNarrowGeometryRosterBandFallsBackWithAccurateOverflowMarker:
    def test_roster_band_falls_back_to_vertical_list_with_correct_dropped_count(
        self, tmp_path: pathlib.Path, monkeypatch
    ) -> None:
        kanban_root = tmp_path / "kanroot"
        (kanban_root / "logs").mkdir(parents=True)
        _install_crontab(_FULL_ROSTER_CRONTAB)
        _install_narrow_pane(
            tmp_path, monkeypatch, width=_NARROW_PANE_WIDTH, height=_NARROW_PANE_HEIGHT
        )

        result = _run_roster_band(kanban_root)
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        assert _entry_present(stdout, "PM"), (
            f"the first roster entry (pm) must still print in the vertical "
            f"fallback\n{_diagnostic(result)}"
        )
        shown = [tok for tok in _ROSTER_ORDER if _entry_present(stdout, tok)]
        assert shown == ["PM"], (
            f"expected the narrow-pane vertical fallback to show exactly one "
            f"entry (PM) at this height; got {shown}\n{_diagnostic(result)}"
        )
        dropped = _marker_count(stdout)
        expected_dropped = len(_ROSTER_ORDER) - len(shown)
        assert dropped == expected_dropped, (
            f"expected '+{expected_dropped} more' (a {len(_ROSTER_ORDER)}-entry "
            f"roster with {len(shown)} shown); got {dropped}\n{_diagnostic(result)}"
        )

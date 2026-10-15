"""
test_event_band_renderer.py
=============================
Behavioral unit tests for team/scripts/dashboard/event-band.sh, the
schedule window's merged, chronologic, agent-tagged, signal-only event
stream that replaces reading five separate raw per-agent cron tails.

WHY THIS EXISTS
---------------
The event band must merge every agent's cron-<agent>.log into one
timestamp-ordered stream (not five per-agent columns concatenated), keep
only the SIGNAL-class lines the shared classifier
(lib/event_signal_patterns.sh) recognizes, and say so plainly
("no wake events yet today") rather than rendering blank when nothing
signal-worthy has happened yet today. This module locks in that
behavior end-to-end against the real script, not a shortcut around it.

COVERAGE
--------
  - Merge ordering: two agents' logs with interleaved timestamps render
    as one ascending-timestamp stream, each line carrying its agent tag
    — never concatenated per agent.
  - Idle-versus-dispatch filtering: a fixture seeding one idle wake
    cycle (re-checking / no tasks / idle exit / sleeping-stagger /
    timing / zero-completed) and one dispatch wake cycle (dispatch +
    finished-with-state) renders only the dispatch cycle's lines.
  - Empty-state: no cron logs at all, an empty (but present) logs
    directory, and a log whose only events are from a prior day all
    print the literal line "no wake events yet today".
  - `--help` exits 0 and documents `--kanban-root`.
  - Delegated classification: the script sources
    lib/event_signal_patterns.sh and defines no pattern list of its own.
  - Bounded read against production-shaped TODAY volume: a log carrying
    both a large pre-today body (far more lines than a bash per-line
    classifier could walk from the start of the file within a render's
    time budget) AND thousands of today-dated SIGNAL lines spanning at
    least 100 distinct line texts across every signal class the library
    declares (dual-channel error:/message:/remedy: blocks, per-project
    HALT lines, dispatch and finished-task lines, sweep-census skips,
    and the rest) still renders correctly -- every row, count, and
    first-seen time asserted and gating -- and the render's wall-clock
    time is measured against the pane's own refresh interval and
    NARRATED, never asserted: a wall-clock threshold is a CONVERT-class
    check (SOP-core "Blocking Severity: only real damage blocks"), so an
    over-budget render prints one line and raises a warning the run's
    summary shows, and the suite's verdict stays independent of host
    load. The render itself runs under a hang guard that is equally
    independent of host load: it fails a render that stops making
    progress, or one that keeps burning CPU far past anything a
    terminating render of this fixture costs, never one that is merely
    slow. The fixture is production-shaped in today volume and
    DIVERSITY, not merely in the log's total historical size or in a
    count of repeated copies of one line -- a fixture large only in
    count, not in content diversity, would look fast without
    exercising the per-line cost that actually scales with production's
    traffic.
  - Collapse folding: a production-shaped fixture (~1,800 byte-identical
    sweep-census skip lines spread across all five agents, interleaved
    with ~60 distinct events) still renders every distinct event, and
    represents the repeated line as one row carrying its whole-day
    occurrence count and first-seen time -- a row count that does NOT
    grow with the repetition count. The same fixture reduced to one line
    per signal class renders every non-collapsing class byte-for-byte
    unchanged; only the class that declares a collapse identity differs.
  - Tab-bearing collapse identity: two sweep-census identities that both
    carry a literal embedded tab, and diverge only in the text after that
    tab, render as two separate rows with their own counts, each printing
    its full source text once with the tab intact.
  - Unstamped continuation-line timestamp inheritance: a bare
    error:/message: continuation line with no leading "[ISO8601] " of its
    own inherits its immediately preceding timestamped parent line's
    stamp, stays adjacent to it in source order, never carries a
    synthesized render-time "now", is dropped (not stamped) when no
    parent precedes it, and two renders of identical static input
    produce byte-identical full stdout.
"""

from __future__ import annotations

import datetime as _dt
import os
import pathlib
import re
import subprocess
import time
import warnings

import pytest

from tests.fixtures.process_progress import require_procfs, run_until_exit

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPT = _TEAM_DIR / "scripts" / "dashboard" / "event-band.sh"
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"

_TIMEOUT_SECONDS = 15

# CPU-time ceiling for the one render that walks a production-shaped
# volume. That render runs under run_until_exit's stall guard rather than
# a wall-clock timeout, because elapsed time moves with host load (the
# same render measures about 4s idle and about 30s at a load average of
# 10 on 8 cores) while the CPU it consumes barely does (about 4s and 5s
# on those same two runs). A stall guard alone never fires on a renderer
# stuck in a loop that keeps burning CPU, so this ceiling catches that
# case. It sits far above the CPU of any render of this fixture that
# terminates -- roughly 60 times the measured cost, and several times
# the ~57s an unbounded per-line read would take (see
# _LARGE_PRE_TODAY_LINE_COUNT) -- so it is a hang guard, deliberately
# NOT a performance budget: that is _RENDER_TIME_BUDGET_SECONDS below,
# which is narrated rather than asserted.
_LARGE_RENDER_CPU_CEILING_SECONDS = 300


def _pane_refresh_interval_seconds() -> float:
    """Read the schedule pane's own default refresh interval straight out
    of create.sh's `_DEFAULT_REFRESH=<N>` assignment rather than
    duplicating that number as a second literal here -- the band should
    render inside THIS interval, not an arbitrary test-side constant
    that could drift from it unnoticed."""
    match = re.search(
        r"^_DEFAULT_REFRESH=(\d+)\s*$", _CREATE_SH.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match, (
        f"expected a '_DEFAULT_REFRESH=<N>' assignment in {_CREATE_SH} -- "
        "the pane refresh default this test's render-time budget must track"
    )
    return float(match.group(1))


def _today_iso(hour: int, minute: int, second: int) -> str:
    """An ISO-8601 timestamp for "today" at the given local time, in the
    same shape team/scripts/lib/wake_bootstrap.sh's log() function writes
    (`date -Iseconds`) -- date, "T", time, and the host's local UTC
    offset.
    """
    now = _dt.datetime.now().astimezone()
    stamped = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return stamped.isoformat(timespec="seconds")


def _write_log(path: pathlib.Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _band_command(kanban_root: pathlib.Path) -> tuple[list[str], dict[str, str]]:
    """The argv and environment that run event-band.sh standalone (no
    tmux, no color) against a fixture root."""
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return ["bash", str(_SCRIPT), "--kanban-root", str(kanban_root)], env


def _run(kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    """Run event-band.sh standalone against a small fixture root.

    The timeout is a hang guard, not a performance assertion. A render of
    a production-shaped volume goes through _run_large_render instead,
    whose guard does not depend on elapsed time at all.
    """
    argv, env = _band_command(kanban_root)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        env=env,
    )


def _run_large_render(kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    """Run event-band.sh against a production-shaped fixture root to
    completion, however long a loaded host takes.

    The render fails as hung only when its process tree makes no
    observable progress for the stall guard's span, or as spinning once
    it has consumed _LARGE_RENDER_CPU_CEILING_SECONDS of CPU; both bound
    what the renderer does rather than how busy the host is. The render
    gets its own process group, so a hung render's descendants are
    killed with it rather than outliving the test.
    """
    argv, env = _band_command(kanban_root)
    return run_until_exit(
        argv,
        env=env,
        own_process_group=True,
        cpu_seconds_ceiling=_LARGE_RENDER_CPU_CEILING_SECONDS,
    )


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_documents_kanban_root_and_exits_zero() -> None:
    result = subprocess.run(
        ["bash", str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    assert "--kanban-root" in result.stdout
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Merge ordering
# ---------------------------------------------------------------------------


def test_merges_multiple_agent_logs_in_ascending_timestamp_order_with_agent_tags(
    tmp_path: pathlib.Path,
) -> None:
    """Two agents' logs, interleaved in real time, render as ONE merged
    stream in ascending timestamp order -- never each agent's lines
    concatenated as its own block."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    _write_log(
        logs_dir / "cron-pm.log",
        [
            f"[{_today_iso(9, 0, 5)}] wake(pm): re-check complete: "
            "promoted 1 task(s) from WAITING to BACKLOG",
            f"[{_today_iso(9, 0, 40)}] wake(pm): project demo: "
            'done: completed=1 project_elapsed=10s batch_elapsed=10s '
            'reason="no more BACKLOG tasks in pm queue"',
        ],
    )
    _write_log(
        logs_dir / "cron-coder.log",
        [
            f"[{_today_iso(9, 0, 20)}] wake(coder): task CODER-example: "
            "transitioned to WORKING [A]",
            f"[{_today_iso(9, 1, 0)}] wake(coder): "
            "finished task CODER-example with state DONE",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 4, f"expected 4 merged lines, got: {lines!r}"

    # Ascending timestamp order ACROSS agents (pm, coder, pm, coder) --
    # a per-agent concatenation would group both pm lines first instead.
    assert lines[0].startswith("[pm 09:00:05]"), lines[0]
    assert lines[1].startswith("[coder 09:00:20]"), lines[1]
    assert lines[2].startswith("[pm 09:00:40]"), lines[2]
    assert lines[3].startswith("[coder 09:01:00]"), lines[3]


# ---------------------------------------------------------------------------
# Unstamped continuation-line timestamp inheritance
# ---------------------------------------------------------------------------


def test_unstamped_continuation_lines_inherit_parent_timestamp_and_stay_adjacent(
    tmp_path: pathlib.Path,
) -> None:
    """A timestamped parent line followed by bare error:/message:
    continuation lines (the wake log's dual-channel refusal shape,
    emitted with no leading "[ISO8601] " of their own) renders all three
    rows carrying the PARENT's own HH:MM:SS, immediately after it and in
    source order -- never the time the render happened to run."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    parent_time = _today_iso(10, 15, 30)
    next_parent_time = _today_iso(10, 16, 0)
    # error/message keys deliberately avoid lock.provenance.contended and
    # lock.stale_threshold.floor_enforced: event-band.sh folds those two
    # specific keys' error:/message:/remedy: triples into one row
    # (see the dedicated
    # TestLockNarrationEventBandFolding coverage below), which is a
    # different behaviour than the one this test pins. A generic,
    # non-folded key keeps this test's own three-separate-rows assertion
    # independent of that feature.
    _write_log(
        logs_dir / "cron-coder.log",
        [
            f"[{parent_time}] wake(coder): "
            "finished task CODER-example with state BLOCKED",
            "error:   gate.refusal.example",
            "message: could not acquire the release lock",
            f"[{next_parent_time}] wake(coder): task CODER-next: "
            "transitioned to WORKING [A]",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 4, f"expected 4 merged lines, got: {lines!r}"

    assert lines[0].startswith("[coder 10:15:30]"), lines[0]
    assert "finished task CODER-example" in lines[0]
    # The two continuation lines carry the PARENT's stamp, not a
    # render-time "now", and stay adjacent to it in their original
    # source order (error: before message:).
    assert lines[1] == "[coder 10:15:30] error:   gate.refusal.example", lines[1]
    assert (
        lines[2] == "[coder 10:15:30] message: could not acquire the release lock"
    ), lines[2]
    assert lines[3].startswith("[coder 10:16:00]"), lines[3]


def test_unstamped_continuation_line_with_no_prior_parent_is_dropped(
    tmp_path: pathlib.Path,
) -> None:
    """A bare continuation line with no timestamped parent line before it
    anywhere in its own agent's candidate set has no timestamp to
    inherit. It is dropped rather than stamped with the render's own
    wall clock -- the documented, deliberate choice for this case (see
    the fallback branch's comment in event-band.sh) -- so it never
    contributes a synthesized "now" row."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    parent_time = _today_iso(11, 0, 0)
    _write_log(
        logs_dir / "cron-coder.log",
        [
            "error:   orphaned continuation with no parent above it",
            f"[{parent_time}] wake(coder): task CODER-example: "
            "transitioned to WORKING [A]",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected the orphan line dropped, got: {lines!r}"
    assert lines[0].startswith("[coder 11:00:00]"), lines[0]
    assert "orphaned continuation" not in result.stdout


def test_render_is_deterministic_across_repeated_invocations(
    tmp_path: pathlib.Path,
) -> None:
    """Two renders of identical static input produce byte-identical
    stdout, asserted on the FULL output rather than a substring -- the
    band's output is a pure function of its input, never reshuffled by
    the wall clock the render happens to run at."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    _write_log(
        logs_dir / "cron-coder.log",
        [
            f"[{_today_iso(12, 0, 0)}] wake(coder): "
            "finished task CODER-a with state BLOCKED",
            "error:   lock.provenance.contended",
            "message: could not acquire the release lock",
            f"[{_today_iso(12, 0, 0)}] wake(coder): task CODER-b: "
            "transitioned to WORKING [A]",
        ],
    )
    _write_log(
        logs_dir / "cron-pm.log",
        [
            f"[{_today_iso(12, 0, 0)}] wake(pm): re-check complete: "
            "promoted 1 task(s) from WAITING to BACKLOG",
            "error:   a second agent's bare continuation line",
        ],
    )

    first = _run(kanban_root)
    second = _run(kanban_root)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout, (
        f"renders diverged on identical input:\nfirst:\n{first.stdout!r}\n"
        f"second:\n{second.stdout!r}"
    )


def test_no_rendered_timestamp_is_absent_from_the_input_logs(
    tmp_path: pathlib.Path,
) -> None:
    """Every HH:MM:SS this renders is one that actually appears in the
    source logs -- never the render's own wall clock -- proving the
    fix removed the synthesized "now" stamp rather than merely hiding
    its non-determinism."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    parent_time = _today_iso(13, 45, 10)
    log_lines = [
        f"[{parent_time}] wake(coder): "
        "finished task CODER-example with state BLOCKED",
        "error:   lock.provenance.contended",
        "message: could not acquire the release lock",
    ]
    _write_log(logs_dir / "cron-coder.log", log_lines)

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    raw_input_text = "\n".join(log_lines)
    rendered_times = re.findall(r"\b(\d{2}:\d{2}:\d{2})\b", result.stdout)
    assert rendered_times, f"expected at least one rendered row, got: {result.stdout!r}"
    for rendered_time in rendered_times:
        assert rendered_time in raw_input_text, (
            f"rendered timestamp {rendered_time!r} does not appear anywhere "
            f"in the input logs:\n{raw_input_text}"
        )


# ---------------------------------------------------------------------------
# Idle-versus-dispatch filtering
# ---------------------------------------------------------------------------


def test_idle_cycle_filtered_dispatch_cycle_survives(tmp_path: pathlib.Path) -> None:
    """An idle wake cycle's boilerplate lines never render; a dispatch
    cycle's signal lines always do."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    idle_lines = [
        f"[{_today_iso(8, 0, 0)}] wake(writer): "
        "re-checking WAITING tasks for satisfied prerequisites...",
        f"[{_today_iso(8, 0, 1)}] wake(writer): no WAITING tasks to re-check",
        f"[{_today_iso(8, 0, 2)}] wake(writer): "
        "re-checking BLOCKED tasks for cleared blockers...",
        f"[{_today_iso(8, 0, 3)}] wake(writer): no BLOCKED tasks to re-check",
        f"[{_today_iso(8, 0, 4)}] wake(writer): "
        "discovery_run_pipeline: no work pending, idle exit",
        f"[{_today_iso(8, 0, 5)}] wake(writer): "
        "sleeping 21s before work (--sleep stagger)",
        f"[{_today_iso(8, 0, 6)}] wake(writer): "
        "timing: work_begin=1785107882 work_end=1785107882 work_delta=0s",
        f"[{_today_iso(8, 0, 7)}] wake(writer): project demo: "
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

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2, f"expected only the 2 dispatch-cycle lines, got: {lines!r}"
    assert "transitioned to WORKING [A]" in lines[0]
    assert "finished task TESTER-example with state DONE" in lines[1]

    boilerplate_markers = (
        "re-checking",
        "no WAITING tasks",
        "no BLOCKED tasks",
        "idle exit",
        "sleeping",
        "stagger",
        "timing:",
    )
    for marker in boilerplate_markers:
        assert marker not in result.stdout, (
            f"boilerplate marker {marker!r} leaked into the signal-only band: "
            f"{result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Empty-state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("build_logs_dir", [True, False], ids=["empty-dir", "no-dir"])
def test_no_events_prints_literal_empty_state(
    tmp_path: pathlib.Path, build_logs_dir: bool
) -> None:
    kanban_root = tmp_path / "kanroot"
    if build_logs_dir:
        (kanban_root / "logs").mkdir(parents=True)

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no wake events yet today"


def test_only_prior_day_events_prints_literal_empty_state(
    tmp_path: pathlib.Path,
) -> None:
    """A log carrying only a signal-class line from a prior day is still
    an empty TODAY's band -- rendering yesterday's dispatch would be
    stale, not signal."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    yesterday = (
        (_dt.datetime.now().astimezone() - _dt.timedelta(days=1))
        .replace(hour=12, minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
    )
    _write_log(
        logs_dir / "cron-cm.log",
        [f"[{yesterday}] wake(cm): task CM-example: transitioned to WORKING [A]"],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no wake events yet today"


# ---------------------------------------------------------------------------
# Delegated classification -- no pattern list of its own
# ---------------------------------------------------------------------------


def test_sources_shared_signal_library_and_defines_no_pattern_list_of_its_own() -> None:
    grep = subprocess.run(
        ["grep", "-n", "event_signal_patterns.sh", str(_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert grep.returncode == 0, (
        "expected event-band.sh to source lib/event_signal_patterns.sh"
    )
    assert "source" in grep.stdout, grep.stdout

    script_text = _SCRIPT.read_text(encoding="utf-8")
    assert "EVENT_SIGNAL_PATTERNS=" not in script_text, (
        "event-band.sh must delegate all line classification to "
        "lib/event_signal_patterns.sh -- it must not define its own "
        "pattern list"
    )


# ---------------------------------------------------------------------------
# Bounded read -- a large pre-today body must not slow the render
# ---------------------------------------------------------------------------

# A bash `while read` loop running the full thirteen-pattern classifier
# against every line, unbounded, measures roughly 5,300 lines/second on
# this hardware. A pre-today body of 300,000 lines would cost such an
# unbounded read about 57 seconds by itself -- far outside this test's
# budget -- so a render the narration below reports as within budget is
# only possible when the read is actually bounded by today's volume
# rather than by the log's total historical size.
_LARGE_PRE_TODAY_LINE_COUNT = 300_000

# The budget itself is the pane's own refresh interval (create.sh's
# _DEFAULT_REFRESH), not a value chosen for this test -- see
# _pane_refresh_interval_seconds above. It is what the render-time
# narration compares against; no assertion in this module gates on it.
_RENDER_TIME_BUDGET_SECONDS = _pane_refresh_interval_seconds()


class RenderTimeOverBudgetWarning(UserWarning):
    """One event-band render took at least the pane's refresh interval.

    Raised by _narrate_render_time so an over-budget render surfaces in
    pytest's warnings summary -- visible to whoever reads the run, never
    a failure. The measurement is wall-clock, so it moves with host load
    as much as with the renderer; a reader weighs it against the load
    average the same narration line carries.
    """


def _narrate_render_time(elapsed: float, meta: dict) -> None:
    """Report one render's wall-clock time against the pane's refresh
    interval without gating on it.

    Prints a single line (shown under ``-s``/``-rA`` and in any failure's
    captured output) naming the elapsed time, the budget, the fixture's
    size and diversity, and the host's 1/5/15-minute load averages and
    core count -- everything a reader needs to tell a slower renderer
    from a busier host. When the render is at or over budget the same
    text is also raised as a RenderTimeOverBudgetWarning so it reaches
    the run's warnings summary. Exceeding the budget never fails the
    test: a wall-clock threshold is CONVERT-class under SOP-core
    "Blocking Severity: only real damage blocks", and no requirement has
    authorized it to block.
    """
    load_1m, load_5m, load_15m = os.getloadavg()
    verdict = "within" if elapsed < _RENDER_TIME_BUDGET_SECONDS else "OVER"
    line = (
        f"event-band render time: {elapsed:.2f}s ({verdict} the "
        f"{_RENDER_TIME_BUDGET_SECONDS:g}s pane refresh interval) against a "
        f"{_LARGE_PRE_TODAY_LINE_COUNT}-line pre-today body plus "
        f"{meta['total_line_count']} today-dated signal lines spanning "
        f"{meta['distinct_text_count']} distinct texts; host load average "
        f"{load_1m:.2f} {load_5m:.2f} {load_15m:.2f} on {os.cpu_count()} "
        "cores -- narrated, non-gating"
    )
    print(line)
    if elapsed >= _RENDER_TIME_BUDGET_SECONDS:
        warnings.warn(line, RenderTimeOverBudgetWarning, stacklevel=2)

# A fixture carrying a large pre-today body but only TWO today-dated
# lines passes the time bound while production, whose cost scales with
# TODAY's signal volume rather than the historical body, keeps climbing
# past it. Today's actual measured volume against the operator's own
# five agent logs, at the shipped default read bound, is roughly 29,000
# surviving signal lines -- up from roughly 7,600 measured against the
# same logs the day before, growth that outgrew an earlier fix's
# constant-factor speedup. This fixture is sized against TODAY's
# volume, not yesterday's, so a future day's growth is the only thing
# that can make it stale again, not a stale literal left over from the
# last time this test was updated.
#
# A fixture that is large in COUNT but uniform in CONTENT (N copies of
# one identical line) dodges the actual per-line cost -- it lets the
# collapse-key cache hit on every occurrence after the first and the
# classifier's fast path match every line on its first comparison,
# neither of which happens against production's diversity (a live
# root's surviving signal lines span on the order of 100-150 DISTINCT
# texts, not one). _DIVERSE_TODAY_DISTINCT_TEXT_COUNT and
# _build_diverse_today_volume_fixture below rebuild this test's
# today-volume from that many distinct texts spanning every signal
# class the library declares, not repeated copies of one line. The
# resulting total_line_count (computed by the builder from each class's
# own weight, not hardcoded here) lands at 29,310 -- production's own
# measured order of magnitude, comfortably past the 25,000-line floor
# the fixture-volume assertion below checks.
_DIVERSE_TODAY_DISTINCT_TEXT_COUNT = 150


def _build_large_pre_today_log(agent: str) -> list[str]:
    """A log whose pre-today body cycles through the same idle-cycle
    boilerplate shapes real wake logs emit, interspersed with occasional
    prior-day SIGNAL-class lines -- so the fixture also proves stale
    signal content stays excluded once the read is bounded, not just
    that the read is fast."""
    boilerplate_templates = [
        "wake({agent}): re-checking WAITING tasks for satisfied prerequisites...",
        "wake({agent}): no WAITING tasks to re-check",
        "wake({agent}): re-checking BLOCKED tasks for cleared blockers...",
        "wake({agent}): no BLOCKED tasks to re-check",
        "wake({agent}): discovery_run_pipeline: no work pending, idle exit",
        "wake({agent}): sleeping 21s before work (--sleep stagger)",
        'wake({agent}): project demo: done: completed=0 project_elapsed=3s '
        'batch_elapsed=3s reason="no more BACKLOG tasks in {agent} queue"',
    ]
    # Anchored at yesterday noon rather than counting forward from "now":
    # the whole body must land safely before today's date regardless of
    # what wall-clock hour this test happens to run at, so every
    # generated line -- even the newest one -- keeps a comfortable
    # margin before midnight.
    anchor = (
        (_dt.datetime.now().astimezone() - _dt.timedelta(days=1))
        .replace(hour=12, minute=0, second=0, microsecond=0)
    )
    n = _LARGE_PRE_TODAY_LINE_COUNT
    lines: list[str] = []
    for i in range(n):
        # i=0 is the oldest line, i=n-1 the newest (closest to the
        # anchor) -- ascending order, matching how a real append-only
        # log is written, so tail -n bounding reads the newest lines.
        ts = (anchor - _dt.timedelta(minutes=2 * (n - 1 - i))).isoformat(
            timespec="seconds"
        )
        if i % 5000 == 0:
            # A prior-day SIGNAL-class line every 5000 cycles: must be
            # excluded by the TODAY filter even though it survives the
            # signal pre-filter -- bounding the read must never leak
            # stale signal content into today's band.
            lines.append(
                f"[{ts}] wake({agent}): finished task {agent.upper()}-stale "
                "with state DONE"
            )
        else:
            tmpl = boilerplate_templates[i % len(boilerplate_templates)]
            lines.append(f"[{ts}] {tmpl.format(agent=agent)}")
    return lines


# One entry per EVENT_SIGNAL_PATTERNS class in lib/event_signal_patterns.sh
# (same order), each a (weight, bare, formatter) triple:
#   weight    -- how many of _DIVERSE_TODAY_DISTINCT_TEXT_COUNT's distinct
#                identities land in this class per 13-identity cycle,
#                relative frequency this class's occurrences carry (see
#                _build_diverse_today_volume_fixture) -- sized so the
#                classes a live root's daily traffic is actually
#                dominated by today (sweep-census skips, and the
#                dual-channel error:/remedy: pair) dominate this
#                fixture's volume too, exactly as they dominate
#                production's, rather than spreading 29,310 lines evenly
#                across thirteen classes that do not actually occur
#                evenly.
#   bare      -- True for a line the wake log emits with NO leading
#                "[ISO8601] " of its own (a dual-channel error:/message:/
#                remedy: continuation line, emitted directly under its
#                own timestamped parent). This shape is what event-band.sh's
#                per-agent timestamp-inheritance branch (see the fallback
#                branch in its per-line loop) exists to handle, and a
#                fixture with none of these lines would never exercise
#                that branch at all.
#   formatter -- (agent, distinct_id) -> line text (never bracket-wrapped
#                here; the caller adds "[<iso>] " for a non-bare line).
#                distinct_id seeds the text itself (a bundle name, a task
#                id, a lock probe number...) so every one of
#                _DIVERSE_TODAY_DISTINCT_TEXT_COUNT identities is a
#                genuinely distinct literal string, so the traffic is
#                diverse in content -- never the same text merely
#                reformatted.
# The three heavy weights below (sweep-census, error:, remedy:) are the
# classes today's live root's daily traffic is actually dominated by;
# 840 is not a round number chosen for its own sake, it is what makes
# this fixture's total_line_count land at production's own measured
# order of magnitude (29,310, against a live root's own ~29,000) once
# spread across each class's own share of the 150 distinct identities --
# see the module-level comment above _DIVERSE_TODAY_DISTINCT_TEXT_COUNT.
_DIVERSE_DOMINANT_CLASS_WEIGHT = 840

_DIVERSE_SIGNAL_CLASSES = (
    # 0: sweep-census skip -- the one class that declares a collapse
    # identity (EVENT_SIGNAL_COLLAPSE_KEYS in the library), so every
    # occurrence of the SAME distinct_id here folds to one output row.
    (_DIVERSE_DOMINANT_CLASS_WEIGHT, False, lambda agent, i: (
        f"wake({agent}): sweep-census: skipped — "
        f"v2.24.{i}-bugfix-bundle-diverse.md: finalize=tag — not report"
    )),
    # 1: finished task -- a one-shot event per task, never repeats.
    (1, False, lambda agent, i: (
        f"wake({agent}): finished task {agent.upper()}-repro-{i:03d} "
        "with state DONE"
    )),
    # 2: dispatch -- likewise one-shot.
    (1, False, lambda agent, i: (
        f"wake({agent}): task {agent.upper()}-repro-{i:03d}: "
        "transitioned to WORKING [A]"
    )),
    # 3: non-zero completed-task count.
    (3, False, lambda agent, i: (
        f'wake({agent}): project demo: done: completed=1 '
        f'project_elapsed={i}s batch_elapsed={i}s '
        f'reason="no more BACKLOG tasks in {agent} queue"'
    )),
    # 4: HALT flag detected.
    (2, False, lambda agent, i: (
        f"wake({agent}): HALT flag detected, exiting (probe {i})"
    )),
    # 5: per-project HALT present -- repeats each wake while a project
    # stays halted, so a moderate weight.
    (4, False, lambda agent, i: (
        f"wake({agent}): project proj-{i}: per-project HALT present, "
        "skipping"
    )),
    # 6: non-zero WAITING-to-BACKLOG promotion count.
    (2, False, lambda agent, i: (
        f"wake({agent}): re-check complete: promoted {(i % 9) + 1} "
        f"task(s) from WAITING to BACKLOG (batch {i})"
    )),
    # 7: one task's own prerequisites clearing -- one-shot.
    (1, False, lambda agent, i: (
        f"wake({agent}): task {agent.upper()}-repro-{i:03d}: "
        "prerequisites satisfied, promoting to BACKLOG"
    )),
    # 8: an abandoned worktree reap -- one-shot.
    (1, False, lambda agent, i: (
        f"wake({agent}): tearing down prior worktree for task "
        f"{agent.upper()}-repro-{i:03d} (interrupted wake)"
    )),
    # 9: stale-active reconciliation.
    (2, False, lambda agent, i: (
        f"wake({agent}): stale-active reconciliation: reset "
        f"{(i % 9) + 1} orphaned task(s) to BACKLOG (batch {i})"
    )),
    # 10: dual-channel error: continuation -- bare (no leading
    # timestamp), and one of the classes measured as dominant in a live
    # root, so weighted heavily.
    (_DIVERSE_DOMINANT_CLASS_WEIGHT, True, lambda agent, i: (
        f"error:   lock.provenance.contended (holder pid {9000 + i})"
    )),
    # 11: dual-channel message: continuation -- bare.
    (50, True, lambda agent, i: (
        "message: pgai_lock_stale_age_threshold_seconds: configured "
        f"value {i}s is below the max-chain-budget floor"
    )),
    # 12: dual-channel remedy: continuation -- bare, the other class
    # measured as dominant in a live root. Carries a literal apostrophe
    # ("project.cfg's") immediately followed by a bracketed section name
    # ("[locks]") -- the exact punctuation shape a live root's own
    # dominant repeated remedy: text carries, and the shape that once
    # broke the collapse-key cache's hit test (an odd number of single
    # quotes in the cached string defeats bash's `-v arr[$key]`, which
    # re-lexes its own subscript argument for quote removal): every
    # occurrence of this identity beyond the first must still be a cache
    # HIT for this fixture to stay inside the render-time budget the
    # narration below reports, so a future regression of that hit test
    # shows up in this test's narrated timing too, not just in a value
    # it happens to compute.
    (_DIVERSE_DOMINANT_CLASS_WEIGHT, True, lambda agent, i: (
        "remedy:  No action needed -- raise it in kanban.cfg's [locks] "
        f"section instead -- probe {i}."
    )),
)

# The single collapsing class index above -- kept as one named constant
# rather than repeated as a literal 0 everywhere it is compared against,
# since it is what couples this fixture to the library's current
# collapse contract (only the sweep-census pattern declares one today).
_DIVERSE_COLLAPSING_CLASS_INDEX = 0


def _build_diverse_today_volume_fixture(
    distinct_text_count: int, stagger_seconds: int = 1
) -> tuple[dict[str, list[str]], dict]:
    """Build one production-shaped today-volume fixture spanning
    distinct_text_count DISTINCT line texts across every signal class
    _DIVERSE_SIGNAL_CLASSES declares (round-robined across all five
    agent logs), each repeated according to its class's own weight --
    the dominant classes (sweep-census skips, and the dual-channel
    error:/remedy: pair) repeating often, one-shot classes (a task's own
    dispatch or finish) exactly once, matching how these events actually
    behave in a live root rather than repeating everything evenly.

    Returns (per_agent_lines, meta), where meta carries:
      - "distinct_text_count": the number of actually-distinct texts
        generated (verified, not merely assumed from the parameter);
      - "total_line_count": total lines generated across all agents;
      - "occurrences": {distinct_id: occurrence_count};
      - "expected_output_row_count": how many rows the renderer must
        print for this fixture -- one row per occurrence for every
        non-collapsing identity, one row total (not one per occurrence)
        for each collapsing identity;
      - "sample": {class_index: (agent, distinct_id, text)} for one
        representative identity per class, so a caller can assert
        specific rendered content without hardcoding it twice.
    """
    per_agent_lines: dict[str, list[str]] = {a: [] for a in _FIXTURE_AGENT_ORDER}
    occurrences: dict[int, int] = {}
    texts_by_id: dict[int, str] = {}
    agent_by_id: dict[int, str] = {}
    class_by_id: dict[int, int] = {}
    sample: dict[int, tuple[str, int, str]] = {}

    for distinct_id in range(distinct_text_count):
        class_index = distinct_id % len(_DIVERSE_SIGNAL_CLASSES)
        weight, bare, formatter = _DIVERSE_SIGNAL_CLASSES[class_index]
        agent = _FIXTURE_AGENT_ORDER[distinct_id % len(_FIXTURE_AGENT_ORDER)]
        text = formatter(agent, distinct_id)
        texts_by_id[distinct_id] = text
        agent_by_id[distinct_id] = agent
        class_by_id[distinct_id] = class_index
        occurrences[distinct_id] = weight
        sample.setdefault(class_index, (agent, distinct_id, text))

    total_line_count = sum(occurrences.values())

    # Emit occurrences round-robined across identities (not all of one
    # identity's occurrences back to back) -- the chronologic merge
    # already scatters a repeated identity across the day in production
    # (five agents interleaving), and this keeps the fixture's shape
    # honest to that rather than writing one identity's whole run
    # contiguously.
    remaining = dict(occurrences)
    offset = 0
    active_ids = [i for i in range(distinct_text_count) if remaining[i] > 0]
    while active_ids:
        next_active: list[int] = []
        for distinct_id in active_ids:
            _, bare, _ = _DIVERSE_SIGNAL_CLASSES[class_by_id[distinct_id]]
            agent = agent_by_id[distinct_id]
            text = texts_by_id[distinct_id]
            if bare:
                per_agent_lines[agent].append(text)
            else:
                iso, _hms = _stamp(8, 0, 0, offset)
                per_agent_lines[agent].append(f"[{iso}] {text}")
            offset += stagger_seconds
            remaining[distinct_id] -= 1
            if remaining[distinct_id] > 0:
                next_active.append(distinct_id)
        active_ids = next_active

    expected_output_row_count = sum(
        1 if class_by_id[i] == _DIVERSE_COLLAPSING_CLASS_INDEX else occurrences[i]
        for i in range(distinct_text_count)
    )

    meta = {
        "distinct_text_count": len(set(texts_by_id.values())),
        "total_line_count": total_line_count,
        "occurrences": occurrences,
        "expected_output_row_count": expected_output_row_count,
        "sample": sample,
    }
    return per_agent_lines, meta


def test_large_pre_today_body_renders_correctly_and_narrates_render_time(
    tmp_path: pathlib.Path,
) -> None:
    """A fixture combining a 300,000-line pre-today body on one agent's
    log with thousands of today-dated SIGNAL lines spread across all
    five agents' logs (production-shaped, not a fixture-only shortcut --
    see _build_diverse_today_volume_fixture) still renders correctly:
    the row count, every sampled identity's occurrence count, the
    collapsed row's count and first-seen time, and the exclusion of
    prior-day signal content are all asserted and gate this test.

    The render's wall-clock time against the pane's own refresh interval
    is a narrated, non-gating measurement (see _narrate_render_time): it
    is printed with the host's load average and raised as a warning when
    over budget, and never fails the test. A wall-clock threshold is a
    CONVERT-class check under SOP-core "Blocking Severity: only real
    damage blocks", no requirement has authorized this one to block, and
    the same render measures anywhere from about half the budget on an
    idle host to twice it at a load average of 5-7 on 8 cores -- a
    verdict that moved with host load, not with the renderer. For the
    same reason the render runs under _run_large_render's guard, which
    fails a hung or spinning render but never a slow one.

    The render's remaining cost after the read bound tracks TODAY's
    signal volume, not the historical body's size, so a fixture that
    only makes the historical body large -- while leaving today's volume
    at two lines -- would look fast without exercising the cost that
    actually grows in production; this fixture carries both."""
    require_procfs("large pre-today body render")
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    # Today's production-shaped volume: _DIVERSE_TODAY_DISTINCT_TEXT_COUNT
    # distinct texts spanning every signal class the library declares
    # (dual-channel error:/message:/remedy: blocks, per-project HALT
    # lines, dispatch and finished-task lines, sweep-census skips, and
    # the rest), round-robined across all five agents at their own
    # class's realistic repetition weight -- never repeated copies of
    # one line. A stagger_seconds of 1 keeps every occurrence inside one
    # day from the 08:00:00 start.
    per_agent_today, meta = _build_diverse_today_volume_fixture(
        _DIVERSE_TODAY_DISTINCT_TEXT_COUNT, stagger_seconds=1
    )

    # Guard against a fixture-composition regression: a future edit that
    # makes this fixture uniform again (or only lightly diverse) would
    # still look fast in the render-time narration below without
    # exercising the per-line cost production actually pays, exactly as
    # an earlier uniform fixture did.
    # Asserting the fixture's OWN measured diversity -- not merely that
    # a diversity-capable helper exists -- is what closes that hole.
    assert meta["distinct_text_count"] >= 140, (
        f"expected the today-volume fixture to carry at least 140 "
        f"distinct line texts (production-shaped diversity at today's "
        f"~150-distinct-text volume, not just line count), got "
        f"{meta['distinct_text_count']}"
    )
    assert meta["total_line_count"] >= 25000, (
        f"expected at least 25000 today-dated signal lines -- a live "
        f"root's own measured order of magnitude is ~29,000 -- got "
        f"{meta['total_line_count']}"
    )

    pre_today_lines = _build_large_pre_today_log("coder")
    _write_log(logs_dir / "cron-coder.log", pre_today_lines + per_agent_today["coder"])
    for agent in _FIXTURE_AGENT_ORDER:
        if agent == "coder":
            continue
        _write_log(logs_dir / f"cron-{agent}.log", per_agent_today[agent])

    start = time.monotonic()
    result = _run_large_render(kanban_root)
    elapsed = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    _narrate_render_time(elapsed, meta)

    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # One row per occurrence for every non-collapsing identity, one row
    # total (never one per occurrence) for the collapsing sweep-census
    # identities -- computed from the fixture's own generation, not a
    # hand-counted literal, so this assertion tracks whatever the
    # fixture actually built.
    assert len(out_lines) == meta["expected_output_row_count"], (
        f"expected {meta['expected_output_row_count']} rows "
        f"(one per non-collapsing occurrence, one per collapsing "
        f"identity), got {len(out_lines)}"
    )

    # Spot-check one representative identity from each signal class --
    # proves the diverse fixture's content survives the render correctly,
    # not just that the render finished.
    sweep_agent, sweep_id, sweep_text = meta["sample"][_DIVERSE_COLLAPSING_CLASS_INDEX]
    sweep_rows = [ln for ln in out_lines if sweep_text in ln]
    assert len(sweep_rows) == 1, (
        f"the collapsing sweep-census identity must fold to exactly one "
        f"row, got {len(sweep_rows)}: {sweep_rows!r}"
    )
    assert sweep_rows[0].startswith(f"[{sweep_agent} "), sweep_rows[0]
    assert (
        f"(x{meta['occurrences'][sweep_id]}, first seen" in sweep_rows[0]
    ), sweep_rows[0]

    for class_index, (agent, distinct_id, text) in meta["sample"].items():
        if class_index == _DIVERSE_COLLAPSING_CLASS_INDEX:
            continue
        matches = [ln for ln in out_lines if text in ln]
        assert len(matches) == meta["occurrences"][distinct_id], (
            f"class {class_index} identity {distinct_id} ({text!r}): "
            f"expected {meta['occurrences'][distinct_id]} rows, got "
            f"{len(matches)}"
        )

    assert "CODER-stale" not in result.stdout, (
        "a prior-day SIGNAL-class line leaked into today's band"
    )


# ---------------------------------------------------------------------------
# Collapse folding -- a repeated signal class folds into a bounded number
# of rows, everything else is untouched
# ---------------------------------------------------------------------------

# The five agents whose logs the band merges, in the same roster order
# event-band.sh's own AGENT_ORDER uses -- round-robining the repeated
# line across this order (rather than writing 1,800 lines to one log)
# is what makes the fixture production-shaped: after the chronologic
# merge, identical lines are almost never adjacent, so a renderer that
# only folded consecutive runs would leave this wall of filler intact.
_FIXTURE_AGENT_ORDER = ["pm", "coder", "writer", "tester", "cm"]

# Sized against a real live-root measurement where over 96% of a day's
# signal-class lines (1,804 of 1,865) were this one byte-identical
# sweep-census line: "roughly 1,800" identical lines against "roughly
# 60" distinct ones is the same shape, not a coincidence.
_REPEATED_LINE_COUNT = 1800
_DISTINCT_EVENT_COUNT = 60

# A sweep-census line, byte-identical apart from timestamp and agent --
# this is the collapse identity EVENT_SIGNAL_COLLAPSE_KEYS captures for
# the 'sweep-census:.*skipped' pattern (see lib/event_signal_patterns.sh).
_SWEEP_CENSUS_SUFFIX = (
    "sweep-census: skipped — /tmp/kanban/pgai_agent_kanban/projects/"
    "pgai-agent-kanban/requirements/v2.24.1-bugfix-bundle-20260918.md: "
    "finalize=tag — not report"
)


def _stamp(base_hour: int, base_minute: int, base_second: int, offset_seconds: int):
    """Return (iso_timestamp, "HH:MM:SS") for "today" at
    base_hour:base_minute:base_second plus offset_seconds, in the same
    local-offset ISO-8601 shape _today_iso produces."""
    now = _dt.datetime.now().astimezone()
    base = now.replace(
        hour=base_hour, minute=base_minute, second=base_second, microsecond=0
    )
    stamped = base + _dt.timedelta(seconds=offset_seconds)
    return stamped.isoformat(timespec="seconds"), stamped.strftime("%H:%M:%S")


def _build_repetition_fixture(
    repeated_line_count: int, distinct_event_count: int, stagger_seconds: int = 12
) -> dict[str, list[str]]:
    """Build one production-shaped fixture: repeated_line_count
    byte-identical (apart from timestamp/agent) sweep-census skip lines
    round-robined across all five agents starting at the earliest
    timestamp, followed by distinct_event_count distinct dispatch events
    round-robined across the same roster at later timestamps. Returns
    {agent: [log lines]}. The very first line written (agent index 0,
    offset 0) is deterministically the whole day's earliest sweep-census
    occurrence, which the tests below rely on to know the expected
    first-seen agent and time without re-deriving it from the output.

    stagger_seconds defaults to 12 (roughly once a minute per agent,
    matching the production pacing this fixture is shaped against); a
    caller seeding many more repetitions than that default supports
    within one day before rolling past midnight (and out of the
    renderer's TODAY filter) passes a smaller value to keep every
    generated timestamp on the same day.
    """
    per_agent_lines: dict[str, list[str]] = {a: [] for a in _FIXTURE_AGENT_ORDER}

    offset = 0
    for i in range(repeated_line_count):
        agent = _FIXTURE_AGENT_ORDER[i % len(_FIXTURE_AGENT_ORDER)]
        iso, _hms = _stamp(8, 0, 0, offset)
        per_agent_lines[agent].append(f"[{iso}] wake({agent}): {_SWEEP_CENSUS_SUFFIX}")
        offset += stagger_seconds

    for i in range(distinct_event_count):
        agent = _FIXTURE_AGENT_ORDER[i % len(_FIXTURE_AGENT_ORDER)]
        iso, _hms = _stamp(8, 0, 0, offset)
        per_agent_lines[agent].append(
            f"[{iso}] wake({agent}): finished task "
            f"{agent.upper()}-repro-{i:03d} with state DONE"
        )
        offset += 30

    return per_agent_lines


def test_repeated_signal_class_folds_to_one_row_with_count_and_first_seen(
    tmp_path: pathlib.Path,
) -> None:
    """Against a fixture seeded with ~1,800 identical sweep-census skip
    lines spread across all five agent logs plus ~60 distinct event
    lines, the band prints every distinct event and represents the
    repeated line as a single row stating its whole-day occurrence
    count and first-seen time."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    per_agent_lines = _build_repetition_fixture(
        _REPEATED_LINE_COUNT, _DISTINCT_EVENT_COUNT
    )
    for agent, lines in per_agent_lines.items():
        _write_log(logs_dir / f"cron-{agent}.log", lines)

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]

    # One row per distinct event, plus exactly one folded row for the
    # repeated identity -- never one row per occurrence.
    assert len(out_lines) == _DISTINCT_EVENT_COUNT + 1, (
        f"expected {_DISTINCT_EVENT_COUNT + 1} rows "
        f"({_DISTINCT_EVENT_COUNT} distinct events + 1 folded row), got "
        f"{len(out_lines)}: {out_lines!r}"
    )

    sweep_rows = [ln for ln in out_lines if "sweep-census: skipped" in ln]
    assert len(sweep_rows) == 1, (
        f"the repeated identity must fold to exactly one row, got "
        f"{len(sweep_rows)}: {sweep_rows!r}"
    )

    # The whole day's true count, and the earliest occurrence's own
    # agent and time (index 0 of the round-robin above: pm at 08:00:00)
    # -- proving the fold spans every agent, not just one agent's log.
    expected_first_iso, expected_first_hms = _stamp(8, 0, 0, 0)
    assert expected_first_hms == "08:00:00"
    folded_row = sweep_rows[0]
    assert folded_row.startswith(f"[pm {expected_first_hms}]"), folded_row
    assert f"(x{_REPEATED_LINE_COUNT}, first seen {expected_first_hms})" in folded_row, (
        folded_row
    )

    # Every distinct event survives, none dropped and none duplicated.
    for i in range(_DISTINCT_EVENT_COUNT):
        agent = _FIXTURE_AGENT_ORDER[i % len(_FIXTURE_AGENT_ORDER)]
        task_id = f"{agent.upper()}-repro-{i:03d}"
        matches = [ln for ln in out_lines if task_id in ln]
        assert len(matches) == 1, f"expected exactly one row for {task_id}, got {matches!r}"
        assert "(x" not in matches[0], (
            f"a non-collapsing distinct event must never carry a fold suffix: "
            f"{matches[0]!r}"
        )


def test_folded_row_count_does_not_grow_with_repetition_count(
    tmp_path: pathlib.Path,
) -> None:
    """The folded row count is bounded by the number of DISTINCT collapse
    identities, not by how many times the repeated line occurs -- a
    fixture with far more repetitions still renders exactly one row for
    the identity, proving the fold does not scale with volume."""
    small_root = tmp_path / "kanroot-small"
    large_root = tmp_path / "kanroot-large"

    # 5,000 repetitions at the default 12s stagger would span over 16
    # hours, some of it rolling past midnight and out of the renderer's
    # TODAY filter -- a 1s stagger keeps the whole large fixture inside
    # one day regardless of repetition count.
    for root, count, stagger in ((small_root, 50, 12), (large_root, 5000, 1)):
        per_agent_lines = _build_repetition_fixture(
            count, distinct_event_count=5, stagger_seconds=stagger
        )
        for agent, lines in per_agent_lines.items():
            _write_log(root / "logs" / f"cron-{agent}.log", lines)

    small_result = _run(small_root)
    large_result = _run(large_root)
    assert small_result.returncode == 0, small_result.stderr
    assert large_result.returncode == 0, large_result.stderr

    small_lines = [ln for ln in small_result.stdout.splitlines() if ln.strip()]
    large_lines = [ln for ln in large_result.stdout.splitlines() if ln.strip()]

    small_sweep_rows = [ln for ln in small_lines if "sweep-census: skipped" in ln]
    large_sweep_rows = [ln for ln in large_lines if "sweep-census: skipped" in ln]

    assert len(small_sweep_rows) == 1, small_sweep_rows
    assert len(large_sweep_rows) == 1, large_sweep_rows
    assert "(x50, first seen" in small_sweep_rows[0], small_sweep_rows[0]
    assert "(x5000, first seen" in large_sweep_rows[0], large_sweep_rows[0]

    # Same total row count (5 distinct events + 1 folded row) in both
    # fixtures despite a 100x difference in repetition volume.
    assert len(small_lines) == len(large_lines) == 6, (small_lines, large_lines)


def test_one_line_per_class_fixture_leaves_non_collapsing_classes_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """The same fixture shape reduced to one line per signal class
    renders identically to the pre-collapse renderer for every class
    that declares no collapse identity -- only the one class that does
    (sweep-census skips) may differ from a plain one-line rendering."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    sweep_iso, sweep_hms = _stamp(8, 0, 0, 0)
    dispatch_iso, dispatch_hms = _stamp(9, 0, 0, 0)
    finished_iso, finished_hms = _stamp(9, 1, 0, 0)
    promoted_iso, promoted_hms = _stamp(9, 2, 0, 0)
    halt_iso, halt_hms = _stamp(9, 3, 0, 0)

    _write_log(
        logs_dir / "cron-pm.log",
        [
            f"[{sweep_iso}] wake(pm): {_SWEEP_CENSUS_SUFFIX}",
            f"[{promoted_iso}] wake(pm): re-check complete: "
            "promoted 1 task(s) from WAITING to BACKLOG",
        ],
    )
    _write_log(
        logs_dir / "cron-coder.log",
        [
            f"[{dispatch_iso}] wake(coder): task CODER-onceper-class: "
            "transitioned to WORKING [A]",
            f"[{finished_iso}] wake(coder): "
            "finished task CODER-onceper-class with state DONE",
        ],
    )
    _write_log(
        logs_dir / "cron-tester.log",
        [f"[{halt_iso}] wake(tester): HALT flag detected, exiting"],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(out_lines) == 5, out_lines

    non_collapsing_expected = {
        f"[coder {dispatch_hms}] wake(coder): task CODER-onceper-class: "
        "transitioned to WORKING [A]",
        f"[coder {finished_hms}] wake(coder): "
        "finished task CODER-onceper-class with state DONE",
        f"[pm {promoted_hms}] wake(pm): re-check complete: "
        "promoted 1 task(s) from WAITING to BACKLOG",
        f"[tester {halt_hms}] wake(tester): HALT flag detected, exiting",
    }
    for expected in non_collapsing_expected:
        assert expected in out_lines, (
            f"non-collapsing class rendering changed -- expected {expected!r} "
            f"unchanged in {out_lines!r}"
        )
        # None of these carry a fold suffix -- collapsing is opt-in per
        # pattern, and none of these four patterns opt in.
        assert "(x" not in expected

    # The one class that DOES declare a collapse identity is the only
    # one whose rendering may differ from a plain single-occurrence line
    # -- it still carries the fold suffix even at count 1, since folding
    # is a property of the pattern's declared identity, not of whether
    # this particular fixture happens to repeat it.
    sweep_rows = [ln for ln in out_lines if "sweep-census: skipped" in ln]
    assert len(sweep_rows) == 1, sweep_rows
    assert sweep_rows[0].startswith(f"[pm {sweep_hms}]"), sweep_rows[0]
    assert f"(x1, first seen {sweep_hms})" in sweep_rows[0], sweep_rows[0]


# A literal tab embedded in the collapse-key capture: the scratch row's
# collapse-key column sits BEFORE the free-form `rest` column (see
# event-band.sh's own comment on that ordering), so a raw embedded tab
# there splits the row into extra `read` fields and shifts every column
# after it -- garbling the printed text and truncating the identity at
# the first tab, which folds two genuinely different identities into
# one and drops the second. This is the regression fixture: run against
# a revision that does not escape the collapse-key column before
# writing it, it fails.
_TAB = "\t"


def test_tab_bearing_signal_lines_keep_distinct_identity_and_print_full_text_once(
    tmp_path: pathlib.Path,
) -> None:
    """Two sweep-census identities that both carry a literal embedded tab,
    and that diverge only in the text after that tab, render as two
    separate rows carrying their own counts -- never one row that merges
    the counts and drops one identity's text."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"

    alpha_text = (
        "wake(pm): sweep-census: skipped"
        f"{_TAB}— /p/req/ALPHA.md: finalize=tag — not report"
    )
    beta_text = (
        "wake(pm): sweep-census: skipped"
        f"{_TAB}— /p/req/BETA.md: finalize=report — other reason"
    )

    alpha_isos = [_stamp(9, 0, 0, i * 60)[0] for i in (1, 2, 3)]
    beta_isos = [_stamp(10, 0, 0, i * 60)[0] for i in (1, 2, 3, 4)]
    alpha_first_hms = _stamp(9, 0, 0, 60)[1]
    beta_first_hms = _stamp(10, 0, 0, 60)[1]

    lines = [f"[{iso}] {alpha_text}" for iso in alpha_isos]
    lines += [f"[{iso}] {beta_text}" for iso in beta_isos]
    _write_log(logs_dir / "cron-pm.log", lines)

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(out_lines) == 2, (
        f"expected two rows, one per distinct tab-bearing identity, got "
        f"{len(out_lines)}: {out_lines!r}"
    )

    # Exact-match, not substring: a garbled/duplicated line (the failure
    # shape this test guards) would fail this even if it happened to contain the
    # right substrings somewhere in its scrambled text.
    expected_alpha = (
        f"[pm {alpha_first_hms}] {alpha_text} (x3, first seen {alpha_first_hms})"
    )
    expected_beta = (
        f"[pm {beta_first_hms}] {beta_text} (x4, first seen {beta_first_hms})"
    )

    assert expected_alpha in out_lines, (
        f"expected the ALPHA identity's row printed once, in full, tab "
        f"intact, count 3: {expected_alpha!r} not found in {out_lines!r}"
    )
    assert expected_beta in out_lines, (
        f"expected the BETA identity's row printed once, in full, tab "
        f"intact, count 4: {expected_beta!r} not found in {out_lines!r}"
    )

    for ln in out_lines:
        assert _TAB in ln, f"embedded tab must survive into the displayed text: {ln!r}"


# ---------------------------------------------------------------------------
# Lock-narration event-band folding: the two lock-narration dual-channel blocks
# (lock.provenance.contended, lock.stale_threshold.floor_enforced) fold
# into one row each -- error:/message:/remedy: merged, not three separate
# rows per occurrence -- carrying a count and the distinct agent list.
# Every OTHER dual-channel block (a generic gate/HALT refusal) is
# untouched and still renders all three lines separately.
# ---------------------------------------------------------------------------


def _agent_anchor_line(agent: str, hour: int, minute: int, second: int) -> str:
    """A realistic timestamped SIGNAL line each fixture below opens an
    agent's log with. Every bare continuation block that follows
    inherits ITS timestamp -- exactly what happens in a real wake log,
    where the boilerplate line immediately preceding a dual-channel
    block (e.g. wake_driver.sh's own "repo lock held by ..." line) is
    itself filtered out as non-signal, leaving the nearest EARLIER
    signal-classified timestamped line as what event-band.sh's
    continuation-line handling actually inherits from."""
    ts = _today_iso(hour, minute, second)
    return (
        f"[{ts}] wake({agent}): finished task {agent.upper()}-anchor "
        "with state BLOCKED"
    )


def _lock_narration_block(key: str) -> list[str]:
    """One bare dual-channel error:/message:/remedy: block, exactly as
    pgai_emit_error_contract (team/scripts/lib/error_contract.sh) writes
    it: no leading timestamp of its own."""
    return [
        f"error:   {key}",
        "message: pgai_lock_acquire could not take /locks/demo.lock: "
        "it is already held by holder=cm pid=999 op=wake-chain:demo task=none",
        "remedy:  If the holder is still legitimately working, wait and retry.",
    ]


class TestLockNarrationEventBandFolding:
    def test_thirty_contended_probes_across_four_agents_fold_to_one_row(
        self, tmp_path: pathlib.Path
    ) -> None:
        kanban_root = tmp_path / "kanroot"
        logs_dir = kanban_root / "logs"

        probe_counts = {"pm": 8, "coder": 8, "writer": 7, "tester": 7}
        assert sum(probe_counts.values()) == 30

        for agent_index, (agent, count) in enumerate(probe_counts.items()):
            lines = [_agent_anchor_line(agent, 9, agent_index, 0)]
            for _probe in range(count):
                lines += _lock_narration_block("lock.provenance.contended")
            _write_log(logs_dir / f"cron-{agent}.log", lines)

        result = _run(kanban_root)
        assert result.returncode == 0, result.stderr

        out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        contended_rows = [ln for ln in out_lines if "lock.provenance.contended" in ln]
        assert len(contended_rows) == 1, (
            f"expected exactly one collapsed contended row for 30 probes "
            f"across 4 agents, got {len(contended_rows)}: {contended_rows!r}\n"
            f"full output: {out_lines!r}"
        )
        row = contended_rows[0]
        assert "error:" not in row and "message:" not in row and "remedy:" not in row, (
            f"the folded row must show the bare key, not the raw "
            f"error:/message:/remedy: lines: {row!r}"
        )
        assert "(x30, first seen" in row, row
        for agent in probe_counts:
            assert agent in row, f"expected agent {agent!r} named in the row: {row!r}"

    def test_contended_and_floor_enforced_each_fold_to_their_own_row(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A fixture carrying both known lock-narration keys, from
        several agents each, folds each key to exactly one row -- never
        merged with the other, never left uncollapsed."""
        kanban_root = tmp_path / "kanroot"
        logs_dir = kanban_root / "logs"

        pm_lines = [_agent_anchor_line("pm", 9, 0, 0)]
        pm_lines += _lock_narration_block("lock.provenance.contended")
        pm_lines += _lock_narration_block("lock.provenance.contended")
        pm_lines += _lock_narration_block("lock.stale_threshold.floor_enforced")

        cm_lines = [_agent_anchor_line("cm", 9, 1, 0)]
        cm_lines += _lock_narration_block("lock.provenance.contended")
        cm_lines += _lock_narration_block("lock.stale_threshold.floor_enforced")

        _write_log(logs_dir / "cron-pm.log", pm_lines)
        _write_log(logs_dir / "cron-cm.log", cm_lines)

        result = _run(kanban_root)
        assert result.returncode == 0, result.stderr

        out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        contended_rows = [ln for ln in out_lines if "lock.provenance.contended" in ln]
        floor_rows = [ln for ln in out_lines if "lock.stale_threshold.floor_enforced" in ln]
        assert len(contended_rows) == 1, (
            f"expected exactly one collapsed contended row, got "
            f"{contended_rows!r}\nfull output: {out_lines!r}"
        )
        assert len(floor_rows) == 1, (
            f"expected exactly one collapsed clamp row, got "
            f"{floor_rows!r}\nfull output: {out_lines!r}"
        )
        assert "(x3, first seen" in contended_rows[0], contended_rows[0]
        assert "(x2, first seen" in floor_rows[0], floor_rows[0]
        for row in (contended_rows[0], floor_rows[0]):
            assert "pm" in row and "cm" in row, (
                f"expected both contributing agents named: {row!r}"
            )

    def test_other_dual_channel_blocks_are_not_folded(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A dual-channel block whose key is NOT one of the two known
        lock narrations renders all three lines separately, exactly as
        before this feature -- the fold is scoped to those two literal
        keys only."""
        kanban_root = tmp_path / "kanroot"
        logs_dir = kanban_root / "logs"

        lines = [_agent_anchor_line("pm", 9, 0, 0)]
        lines += _lock_narration_block("gate.refusal.example")
        _write_log(logs_dir / "cron-pm.log", lines)

        result = _run(kanban_root)
        assert result.returncode == 0, result.stderr

        out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert len(out_lines) == 4, (
            f"expected the anchor line plus all three error/message/remedy "
            f"lines to render separately, got {len(out_lines)}: {out_lines!r}"
        )
        assert any("error:   gate.refusal.example" in ln for ln in out_lines), out_lines
        assert any(ln.startswith("[pm") and "message: " in ln for ln in out_lines), out_lines
        assert any(ln.startswith("[pm") and "remedy:  " in ln for ln in out_lines), out_lines

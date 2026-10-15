"""
test_dashboard_cadence.py
==========================
Coverage for the visibility-aware refresh cadence, refresh-on-activate, and
per-pane stagger jitter (team/scripts/dashboard/lib/cadence.sh,
team/scripts/dashboard/cadence-loop.sh, team/scripts/lib/dashboard_constants.sh).

cadence-loop.sh renders once on arrival, then at the configured interval
while its pane is visible (its window is active AND a client is attached);
while hidden it does not render at all -- it only re-checks visibility every
PGAI_DASHBOARD_HIDDEN_MULTIPLIER intervals -- and when its pane becomes
visible the activation signal makes it render at once (never waiting out
the remainder of a slow re-check). A --stream pane's renderer is stopped
while hidden and continued when visible. A bounded, per-pane-deterministic
stagger jitter is applied once before the first render.

Test groups
-----------
Group 1 -- Static coverage (no tmux, no subprocess timing):
    The named constants exist with the documented shapes, create.sh no
    longer contains any flat `watch -n` pane command, and the jitter
    function is deterministic and guarantees distinct offsets for
    consecutive pane indices in the same window.

Group 2 -- Behavioral coverage of the running loop (no tmux required --
exercises cadence-loop.sh's actual render loop as a subprocess using the
PGAI_DASHBOARD_CADENCE_FORCE_STATE test-only override in place of a live
tmux window-switch; its "file:<path>" form lets a test change the state
while the loop runs). Every verdict waits on events the loop produces --
renders, and the interval requests a PATH `sleep` test double records --
never on a fixed wall-clock window, so host load can delay a verdict but
not change it:
    Visible cadence, hidden suspension (no render after the arrival render,
    only re-checks), refresh-on-activate when the pane becomes visible
    (including a signal that lands mid-render), and --stream panes stopped
    while hidden and continued when visible.

Group 3 -- Real tmux coverage (tmux-gated, private socket): confirms
dashboard_pane_visible reads a REAL tmux session -- a detached session
shows no pane, an attached one shows exactly the active window's panes --
not just the FORCE_STATE override Group 2 relies on for speed.

Group 4 -- The CPU-sample test: measures the hidden-window CPU cost
against the recorded PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO named
constant (team/scripts/lib/dashboard_constants.sh), asserting a RATIO
against a same-run, same-host baseline rather than an absolute time. Both
arms are sampled over the same span of the loop's own schedule, counted
on the `sleep` test double's virtual clock, so each is charged for the
same number of ticks whatever the host load (see that constant's own
comment for the measurement method and the recorded baseline).

Docker is not used anywhere in this module -- tmux-dependent tests run on
a private socket per team/tests/unit/test_dashboard_pane_signal_traps.py's
established convention. Tmux-dependent groups narrate a skip (never a
silent one) when tmux is not on PATH.

Test naming (roles/TESTER.md anti-pattern 6): names describe behavior,
never bug IDs, scaffolding labels, or version strings.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from typing import Callable, Iterator, NamedTuple

import pytest

from tests.fixtures.binary_guard import narrate_binary_skip, try_resolve_binary

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's location, matching every other
# dashboard test module.
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_REPO_ROOT = _TEAM_DIR.parent
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_CADENCE_LOOP_SH = _TEAM_DIR / "scripts" / "dashboard" / "cadence-loop.sh"
_CADENCE_LIB_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "cadence.sh"
_DASHBOARD_CONSTANTS_SH = _TEAM_DIR / "scripts" / "lib" / "dashboard_constants.sh"
_SNAPSHOT_LIB_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "snapshot.sh"

_TS_RE = re.compile(r"\d{10}\.\d+")


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_REPO_ROOT)
    env["TERM"] = "xterm-256color"
    return env


def _read_constant(name: str) -> str:
    """Read a named constant out of dashboard_constants.sh by actually
    sourcing it in bash -- never re-implement the parsing in Python, which
    could silently drift from what the shell scripts actually see."""
    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(_DASHBOARD_CONSTANTS_SH))}; echo ${{{name}}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Group 1 -- Static coverage (no tmux, no subprocess timing)
# ---------------------------------------------------------------------------


def test_hidden_multiplier_constant_is_exactly_ten() -> None:
    """The hidden re-check multiplier is one named constant, not a magic
    number scattered across create.sh / cadence.sh / cadence-loop.sh."""
    assert _read_constant("PGAI_DASHBOARD_HIDDEN_MULTIPLIER") == "10"


def test_jitter_bound_constant_is_a_positive_integer_below_the_default_interval() -> None:
    """The jitter bound must be small enough to never meaningfully delay a
    pane's first paint relative to the default 5s active interval."""
    bound = _read_constant("PGAI_DASHBOARD_JITTER_MAX_SECONDS")
    assert bound.isdigit(), f"expected a positive integer, got {bound!r}"
    assert 0 < int(bound) < 5, f"expected a bound below the default 5s interval, got {bound}"


def test_hidden_cpu_budget_ratio_constant_is_a_fraction_below_one() -> None:
    """The CPU-sample test's pass/fail threshold is a named, documented
    constant -- not an unexplained literal buried in the test body."""
    ratio = _read_constant("PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO")
    assert 0 < float(ratio) < 1, f"expected a ratio strictly between 0 and 1, got {ratio}"


def test_create_sh_contains_no_flat_watch_pane_commands() -> None:
    """
    Every former `watch -t -c -n <interval> -- <renderer>` pane command
    (and every hand-rolled "clear; while true; do ...; sleep; done"
    visibility loop) must have been replaced by a cadence-loop.sh
    invocation. A `watch -n`/`watch -t -c -n` pane command reappearing in
    a `*_CMD=` assignment means a pane silently reverted to the flat,
    visibility-blind cadence that cadence-loop.sh replaces.
    """
    text = _CREATE_SH.read_text()
    offending = [
        line
        for line in text.splitlines()
        if re.search(r"_CMD=.*\bwatch\s+-", line)
    ]
    assert not offending, (
        "found a pane command still invoking `watch` directly -- every pane "
        "must route through cadence-loop.sh for visibility-aware cadence:\n"
        + "\n".join(offending)
    )


def test_create_sh_contains_no_hand_rolled_sleep_refresh_loops() -> None:
    """Complementary to the above: no pane command still contains the
    pre-ticket hand-rolled 'sleep $REFRESH_INTERVAL' polling pattern."""
    text = _CREATE_SH.read_text()
    offending = [
        line
        for line in text.splitlines()
        if re.search(r"_CMD=.*sleep \$\{?REFRESH_INTERVAL\}?", line)
    ]
    assert not offending, (
        "found a pane command with a hand-rolled flat-interval sleep loop:\n"
        + "\n".join(offending)
    )


def test_create_sh_dispatches_every_watch_replaced_pane_through_cadence_loop() -> None:
    """Regression-proof count: cadence-loop.sh must be invoked for every
    interval-based pane (main/operator/visibility/git/metadata/
    rc-metrics/agent-metrics/training-logs/human-review/drill-N -- the
    tail/cron-tail/interactive-shell panes are excluded, they were
    never interval-based). A future pane added back on a bare `watch`
    would not increment this count and the drift test above would catch
    the literal `watch` reappearing; this test catches a pane silently
    losing its cadence-loop.sh dispatch entirely (e.g. reverted to a
    one-shot render with no loop at all)."""
    text = _CREATE_SH.read_text()
    call_count = text.count("cadence-loop.sh")
    assert call_count >= 20, (
        f"expected at least 20 cadence-loop.sh references in create.sh "
        f"(one per watch-replaced or hand-rolled-loop pane command), found "
        f"{call_count} -- a pane may have reverted to a flat, "
        f"visibility-blind refresh"
    )


def test_agent_metrics_window_runs_live_and_full_history_panes_under_cadence_loop() -> None:
    """The agent-metrics window's two panes both refresh through the shared
    visibility-aware cadence-loop.sh at the standard interval: pane 0 keeps
    the live default span (no span flag), pane 1 runs the --all report.
    Neither introduces its own refresh mechanism."""
    text = _CREATE_SH.read_text()
    loop = "${SCRIPT_DIR}/cadence-loop.sh --interval ${REFRESH_INTERVAL} -- "
    live = (
        'AGENT_METRICS_CMD="' + loop
        + '${SCRIPT_DIR}/agent-metrics.sh --kanban-root ${KANBAN_ROOT}"'
    )
    full = (
        'AGENT_METRICS_ALL_CMD="' + loop
        + '${SCRIPT_DIR}/agent-metrics.sh --all --kanban-root ${KANBAN_ROOT}"'
    )
    assert live in text, "agent-metrics live-table pane command changed or lost its cadence-loop.sh wrap"
    assert full in text, "agent-metrics full-history pane is missing or not under cadence-loop.sh"
    assert 'dashboard_send_armed_keys "agent-metrics.0" "${AGENT_METRICS_CMD}"' in text
    assert 'dashboard_send_armed_keys "agent-metrics.1" "${AGENT_METRICS_ALL_CMD}"' in text


def test_jitter_seconds_is_deterministic_across_repeated_calls() -> None:
    """The same pane label always produces the same jitter offset --
    "not re-rolled every tick" also means not re-rolled across separate
    invocations of the function for the same pane."""

    def jitter(label: str, bound: int = 3) -> str:
        result = subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(_CADENCE_LIB_SH))}; "
             f"dashboard_cadence_jitter_seconds {shlex.quote(label)} {bound}"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    first = jitter("visibility.0")
    second = jitter("visibility.0")
    assert first == second, f"jitter for the same pane label changed between calls: {first!r} vs {second!r}"


def test_jitter_seconds_is_bounded() -> None:
    """Every jitter offset falls in [0, bound) for a representative set of
    pane labels."""

    def jitter(label: str, bound: int) -> int:
        result = subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(_CADENCE_LIB_SH))}; "
             f"dashboard_cadence_jitter_seconds {shlex.quote(label)} {bound}"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        return int(result.stdout.strip())

    bound = 3
    for label in ["visibility.0", "visibility.1", "visibility.7", "operator.4", "main.0", "rc-metrics.1"]:
        value = jitter(label, bound)
        assert 0 <= value < bound, f"jitter({label!r}, {bound}) = {value}, outside [0, {bound})"


def test_two_panes_launched_together_do_not_share_a_jitter_offset() -> None:
    """
    Acceptance criterion: "Two panes launched together do not tick in
    lockstep; their offsets differ by a bounded, per-pane-deterministic
    amount." Exercised against the real jitter bound the shipped
    constants file declares, for the common "launched together" case of
    two panes with consecutive tmux pane indices in the same window
    (e.g. visibility.0 and visibility.1, the first two panes create.sh
    sends to the Visibility window).
    """
    bound = _read_constant("PGAI_DASHBOARD_JITTER_MAX_SECONDS")

    def jitter(label: str) -> int:
        result = subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(_CADENCE_LIB_SH))}; "
             f"dashboard_cadence_jitter_seconds {shlex.quote(label)} {bound}"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        return int(result.stdout.strip())

    pairs = [
        ("visibility.0", "visibility.1"),
        ("operator.0", "operator.1"),
        ("rc-metrics.0", "rc-metrics.1"),
        ("agent-metrics.0", "agent-metrics.1"),
    ]
    for label_a, label_b in pairs:
        offset_a, offset_b = jitter(label_a), jitter(label_b)
        assert offset_a != offset_b, (
            f"{label_a} and {label_b} (consecutive pane indices, launched "
            f"together) both got jitter offset {offset_a} -- they would "
            f"tick in lockstep"
        )


# ---------------------------------------------------------------------------
# Group 2 -- Behavioral coverage of the running loop (FORCE_STATE override,
# no tmux)
#
# Every test here observes the loop through events it produces -- a render
# on the display stream, a line in an event log, a sleep request -- and
# never through a fixed wall-clock sampling window, so a loaded or stalled
# host can delay a verdict but cannot change it. Two instruments make that
# possible:
#   - a PATH `sleep` test double (_SLEEP_SHIM) that records every interval
#     request the loop makes, so the configured cadence is read straight
#     from the loop's own schedule rather than inferred from render gaps;
#   - wall-clock gaps that are still asserted are LOWER bounds only ("never
#     sooner than the interval"), which host load can only make truer.
# ---------------------------------------------------------------------------

# Upper bound on how long any single wait in Groups 2, 2b and 4 may take
# before the test fails as hung. Every wait ends on its own event, and a
# working loop meets each one in well under a second, so this bound decides
# whether a broken loop fails or hangs -- never whether a working one passes.
_HANG_GUARD_SECONDS = 60.0

# The cadence loop's only `sleep` call is its interval wait (the jitter
# sleep is disabled with --jitter-bound 0 throughout), so each call this
# double sees is exactly one interval request.
_SLEEP_SHIM = r'''#!/usr/bin/env bash
# Test double for `sleep`, placed first on PATH for a cadence-loop.sh under
# test.
#   CADENCE_TEST_EVENT_LOG       when set, appends "sleep <args>" per call.
#   CADENCE_TEST_VIRTUAL_WINDOW_MS, CADENCE_TEST_VIRTUAL_CLOCK and
#   CADENCE_TEST_WINDOW_DONE     when set, returns at once and advances a
#                                virtual clock (milliseconds, kept in the
#                                clock file) by the requested duration; the
#                                first call made at or past the window
#                                creates the done file and parks instead.
if [[ -n "${CADENCE_TEST_EVENT_LOG:-}" ]]; then
    printf 'sleep %s\n' "$*" >> "$CADENCE_TEST_EVENT_LOG"
fi
if [[ -n "${CADENCE_TEST_VIRTUAL_WINDOW_MS:-}" ]]; then
    requested="${1:-0}"
    whole="${requested%%.*}"
    frac=""
    [[ "$requested" == *.* ]] && frac="${requested#*.}"
    frac="${frac}000"
    requested_ms=$(( 10#${whole:-0} * 1000 + 10#${frac:0:3} ))
    clock_ms=0
    [[ -s "$CADENCE_TEST_VIRTUAL_CLOCK" ]] && clock_ms="$(<"$CADENCE_TEST_VIRTUAL_CLOCK")"
    if (( clock_ms >= CADENCE_TEST_VIRTUAL_WINDOW_MS )); then
        : > "$CADENCE_TEST_WINDOW_DONE"
        exec __REAL_SLEEP__ 3600
    fi
    printf '%s\n' "$(( clock_ms + requested_ms ))" > "$CADENCE_TEST_VIRTUAL_CLOCK"
    exit 0
fi
exec __REAL_SLEEP__ "$@"
'''

# A renderer that records each render in the event log before painting a
# timestamp, so the log reads as the loop's own render/sleep sequence.
_LOGGING_RENDERER = 'printf "render\\n" >> "$CADENCE_TEST_EVENT_LOG"; date +%s.%N'


class CadenceHarness(NamedTuple):
    env: dict[str, str]
    event_log: pathlib.Path


def _cadence_harness(work_dir: pathlib.Path, state: str) -> CadenceHarness:
    """Environment for one cadence loop pinned to <state>: the recording
    `sleep` double first on PATH, an event log, and a private framework
    temp root so the loop's pane debug log stays inside <work_dir>."""
    real_sleep = shutil.which("sleep")
    assert real_sleep, "no `sleep` on PATH -- cadence-loop.sh cannot run without one"
    bin_dir = work_dir / "shim-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "sleep"
    shim.write_text(_SLEEP_SHIM.replace("__REAL_SLEEP__", shlex.quote(real_sleep)), encoding="utf-8")
    shim.chmod(0o755)
    event_log = work_dir / "cadence-events.log"
    event_log.touch()

    env = _base_env()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PGAI_DASHBOARD_CADENCE_FORCE_STATE"] = state
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(work_dir / "pgai_tmp")
    env["CADENCE_TEST_EVENT_LOG"] = str(event_log)
    return CadenceHarness(env=env, event_log=event_log)


def _read_events(event_log: pathlib.Path) -> list[str]:
    return [line for line in event_log.read_text(encoding="utf-8").splitlines() if line]


def _events_between_renders(events: list[str], renders: int) -> list[list[str]]:
    """For the first <renders> "render" entries in <events>, the entries
    recorded between each consecutive pair (renders - 1 lists)."""
    positions = [i for i, event in enumerate(events) if event == "render"][:renders]
    assert len(positions) == renders, f"expected {renders} renders in the event log, found: {events}"
    return [events[a + 1:b] for a, b in zip(positions, positions[1:])]


def _requested_seconds(event: str) -> float:
    """The duration a recorded "sleep <seconds>" event asked for."""
    assert event.startswith("sleep "), f"expected a recorded sleep request, got {event!r}"
    return float(event.split()[1])


class LoopDisplay:
    """Non-blocking reader of one running cadence loop's display stream
    (stdout, plus stderr when the loop was started with it merged)."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._fd = proc.stdout.fileno()
        self._data = b""
        self._eof = False

    def pump(self, timeout: float) -> None:
        """Read whatever the stream has ready, waiting at most <timeout>."""
        if self._eof:
            time.sleep(timeout)
            return
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if ready:
            # One raw os.read(): a buffered read would block past what
            # select() confirmed was ready.
            chunk = os.read(self._fd, 65536)
            if chunk:
                self._data += chunk
            else:
                self._eof = True

    @property
    def text(self) -> str:
        return self._data.decode(errors="replace")

    def timestamps(self) -> list[float]:
        """Render timestamps in the order the loop painted them."""
        return [float(m) for m in _TS_RE.findall(self.text)]

    def exited(self) -> bool:
        return self.proc.poll() is not None

    def stop(self) -> str:
        """Kill the loop's whole process group, drain and reap it, and
        return everything it displayed."""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        drain_deadline = time.monotonic() + 10
        while not self._eof and time.monotonic() < drain_deadline:
            self.pump(0.2)
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        return self.text


@contextlib.contextmanager
def _running_cadence_loop(
    env: dict[str, str],
    interval: float,
    renderer: list[str],
    merge_stderr: bool = False,
) -> Iterator[LoopDisplay]:
    """Run cadence-loop.sh in its own process group for the duration of
    the block; the whole group is killed and reaped on exit."""
    args = ["bash", str(_CADENCE_LOOP_SH), "--interval", str(interval), "--jitter-bound", "0",
            "--", *renderer]
    proc = subprocess.Popen(
        args, env=env, cwd=str(_REPO_ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
        start_new_session=True,
    )
    display = LoopDisplay(proc)
    try:
        yield display
    finally:
        display.stop()


def _wait_for(
    condition: Callable[[], bool],
    what: str,
    displays: list[LoopDisplay],
    diagnostics: Callable[[], str],
) -> None:
    """Pump <displays> until <condition> holds. Fails at once if a loop
    exits (a cadence loop never exits on its own), and fails as hung after
    _HANG_GUARD_SECONDS."""
    deadline = time.monotonic() + _HANG_GUARD_SECONDS
    while True:
        for display in displays:
            display.pump(0.02)
        if not displays:
            time.sleep(0.02)
        if condition():
            return
        for display in displays:
            if display.exited():
                pytest.fail(
                    f"waiting for {what}: the cadence loop exited with status "
                    f"{display.proc.returncode}\n{diagnostics()}"
                )
        if time.monotonic() > deadline:
            pytest.fail(
                f"waiting for {what}: nothing happened within the "
                f"{_HANG_GUARD_SECONDS:.0f}s hang guard -- the loop is stuck\n{diagnostics()}"
            )


def _diagnostics(display: LoopDisplay, event_log: pathlib.Path | None = None) -> str:
    text = f"--- display stream ---\n{display.text!r}"
    if event_log is not None and event_log.exists():
        text += f"\n--- event log ---\n{event_log.read_text(encoding='utf-8')}"
    return text


def test_pane_renders_at_the_configured_interval_while_active(tmp_path: pathlib.Path) -> None:
    """
    Acceptance criterion: "A pane in the active window renders at the
    configured interval ... asserted by observing render timestamps."
    FORCE_STATE=active pins the pane to the active-window rate for the
    whole run (no real tmux window-switch needed for this assertion --
    Group 3 covers the real-tmux visibility read separately).

    Between every pair of consecutive renders the loop must make exactly
    one interval request, for exactly the configured interval -- read from
    the loop's own recorded schedule, so neither a slow host nor a fast one
    moves it -- and the painted timestamps must never be closer together
    than that interval (the loop really waits out what it requested).
    """
    interval = 0.3
    renders = 4
    harness = _cadence_harness(tmp_path, "active")
    with _running_cadence_loop(harness.env, interval, ["bash", "-c", _LOGGING_RENDERER]) as display:
        _wait_for(
            lambda: len(display.timestamps()) >= renders,
            f"{renders} renders at a {interval}s active interval",
            [display], lambda: _diagnostics(display, harness.event_log),
        )

    events = _read_events(harness.event_log)
    for between in _events_between_renders(events, renders):
        assert len(between) == 1 and _requested_seconds(between[0]) == pytest.approx(interval), (
            f"expected exactly one {interval}s interval request between consecutive "
            f"active-window renders, got {between}; events: {events}"
        )

    timestamps = display.timestamps()[:renders]
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    for gap in gaps:
        assert gap >= interval - 0.05, (
            f"render gap {gap:.3f}s is shorter than the configured {interval}s interval: {gaps}"
        )


def test_hidden_pane_renders_on_arrival_then_only_rechecks_visibility(tmp_path: pathlib.Path) -> None:
    """
    A hidden pane suspends: after its arrival render it never renders
    again while hidden. Its loop only sleeps for the hidden re-check
    interval -- ten times the configured interval -- and re-checks. The
    `sleep` test double runs a virtual clock across fifteen re-checks
    in well under a second, so the verdict covers many hidden ticks
    without waiting them out. The factor is the literal 10, deliberately
    not re-read from PGAI_DASHBOARD_HIDDEN_MULTIPLIER, so a changed
    constant fails here as well as in the constant's own test.
    """
    interval = 0.3
    hidden_interval = interval * 10
    harness = _cadence_harness(tmp_path, "hidden")
    done = tmp_path / "virtual-window.done"
    env = dict(harness.env)
    env["CADENCE_TEST_VIRTUAL_WINDOW_MS"] = str(int(hidden_interval * 15 * 1000))
    env["CADENCE_TEST_VIRTUAL_CLOCK"] = str(tmp_path / "virtual-clock")
    env["CADENCE_TEST_WINDOW_DONE"] = str(done)
    with _running_cadence_loop(env, interval, ["bash", "-c", _LOGGING_RENDERER]) as display:
        _wait_for(
            done.exists, "fifteen hidden re-checks on the virtual clock",
            [display], lambda: _diagnostics(display, harness.event_log),
        )

    events = _read_events(harness.event_log)
    assert events.count("render") == 1, (
        f"a hidden pane must render only its arrival render; events: {events}"
    )
    requests = [_requested_seconds(e) for e in events if e.startswith("sleep ")]
    assert len(requests) >= 15, events
    assert all(r == pytest.approx(hidden_interval) for r in requests), (
        f"every hidden wait must be the {hidden_interval:.1f}s re-check interval; events: {events}"
    )


def test_activation_signal_while_still_hidden_does_not_render(tmp_path: pathlib.Path) -> None:
    """
    The activation signal makes the loop re-check visibility at once; a
    pane that is still hidden goes straight back to its hidden wait
    without rendering.
    """
    interval = 60
    hidden_interval = interval * 10
    harness = _cadence_harness(tmp_path, "hidden")
    with _running_cadence_loop(harness.env, interval, ["bash", "-c", _LOGGING_RENDERER]) as display:
        diag = lambda: _diagnostics(display, harness.event_log)  # noqa: E731
        _wait_for(
            lambda: [e for e in _read_events(harness.event_log) if e.startswith("sleep ")],
            "the arrival render and the first hidden wait",
            [display], diag,
        )
        os.kill(display.proc.pid, signal.SIGUSR1)
        _wait_for(
            lambda: len([e for e in _read_events(harness.event_log) if e.startswith("sleep ")]) >= 2,
            "a second hidden wait after the activation signal",
            [display], diag,
        )

    events = _read_events(harness.event_log)
    assert events.count("render") == 1, events
    assert [_requested_seconds(e) for e in events if e.startswith("sleep ")][:2] == [
        pytest.approx(hidden_interval), pytest.approx(hidden_interval)
    ], events


def test_pane_becoming_visible_renders_immediately_on_the_activation_signal(tmp_path: pathlib.Path) -> None:
    """
    Switching to a hidden window produces a render immediately rather than
    after the remaining slow re-check. Starts a pane hidden with a 600s
    hidden re-check, waits until the loop has painted once and asked for
    that wait, then makes the pane visible and signals the loop's own pid
    -- the way the activation hook wakes a loop, never its process group
    -- and requires a second render. Nothing but the signal can produce one
    inside the test's lifetime: the natural re-check is ten minutes away,
    an order of magnitude past the hang guard.
    """
    interval = 60
    hidden_interval = interval * 10
    state = tmp_path / "visibility-state"
    state.write_text("hidden\n", encoding="utf-8")
    harness = _cadence_harness(tmp_path, f"file:{state}")
    with _running_cadence_loop(harness.env, interval, ["bash", "-c", _LOGGING_RENDERER]) as display:
        diag = lambda: _diagnostics(display, harness.event_log)  # noqa: E731
        _wait_for(
            lambda: len(display.timestamps()) >= 1
            and any(e.startswith("sleep ") for e in _read_events(harness.event_log)),
            "the first render and the hidden-interval request after it",
            [display], diag,
        )
        state.write_text("active\n", encoding="utf-8")
        signal_sent_at = time.monotonic()
        os.kill(display.proc.pid, signal.SIGUSR1)  # PGAI_DASHBOARD_PANE_SIGNAL default
        _wait_for(
            lambda: len(display.timestamps()) >= 2,
            "a render after the activation signal",
            [display], diag,
        )
        latency = time.monotonic() - signal_sent_at

    events = _read_events(harness.event_log)
    requests = [_requested_seconds(e) for e in events if e.startswith("sleep ")]
    assert requests and requests[0] == pytest.approx(hidden_interval), (
        f"expected the loop's first request to be the {hidden_interval}s hidden "
        f"interval; events: {events}"
    )
    assert latency < hidden_interval / 2, (
        f"second render took {latency:.2f}s after the activation signal -- "
        f"expected well under the {hidden_interval}s hidden interval"
    )


def test_activation_signal_arriving_mid_render_still_cuts_the_hidden_wait_short(
    tmp_path: pathlib.Path,
) -> None:
    """
    Refresh-on-activate must not depend on WHEN the signal lands. Here the
    pane becomes visible and is signalled while its first render is still
    running (the renderer holds on a FIFO until the test releases it, so
    the timing is fixed by the test, not by the scheduler). The loop must
    then render again straight away -- with no interval request in
    between -- instead of discarding the activation and waiting out a 600s
    hidden re-check.
    """
    interval = 60
    state = tmp_path / "visibility-state"
    state.write_text("hidden\n", encoding="utf-8")
    harness = _cadence_harness(tmp_path, f"file:{state}")
    gate = tmp_path / "render-gate"
    os.mkfifo(gate)
    opened = tmp_path / "render-gate.opened"
    renderer_script = tmp_path / "gated_renderer.sh"
    renderer_script.write_text(
        "#!/usr/bin/env bash\n"
        'printf "render\\n" >> "$CADENCE_TEST_EVENT_LOG"\n'
        f"if [[ ! -e {shlex.quote(str(opened))} ]]; then\n"
        f"    : > {shlex.quote(str(opened))}\n"
        f"    read -r _ < {shlex.quote(str(gate))}\n"
        "fi\n"
        "date +%s.%N\n",
        encoding="utf-8",
    )
    renderer_script.chmod(0o755)

    with _running_cadence_loop(harness.env, interval, [str(renderer_script)]) as display:
        diag = lambda: _diagnostics(display, harness.event_log)  # noqa: E731
        _wait_for(opened.exists, "the first render to start", [display], diag)
        state.write_text("active\n", encoding="utf-8")
        os.kill(display.proc.pid, signal.SIGUSR1)  # PGAI_DASHBOARD_PANE_SIGNAL default
        gate_fd = os.open(gate, os.O_WRONLY)
        try:
            os.write(gate_fd, b"release\n")
        finally:
            os.close(gate_fd)
        _wait_for(
            lambda: _read_events(harness.event_log).count("render") >= 2 and display.timestamps(),
            "a render after an activation signal that arrived mid-render",
            [display], diag,
        )

    events = _read_events(harness.event_log)
    (between,) = _events_between_renders(events, 2)
    assert between == [], (
        f"expected the loop to render again without requesting an interval "
        f"after an activation that arrived mid-render, got {between}; events: {events}"
    )


# ---------------------------------------------------------------------------
# Group 2b -- Snapshot-narration display cleanliness (no tmux required):
# lib/snapshot.sh's cache-miss narration is designed to stay off stdout
# (see that module's own header), but a tmux pane's stderr paints the same
# terminal as its stdout, so the narration only stays off the DISPLAY when
# cadence-loop.sh actually redirects the renderer's stderr to a debug log
# rather than leaving it connected to the pane. These tests exercise the
# real snapshot.sh library through a real cadence-loop.sh subprocess (never
# a synthetic stand-in) to prove the combination holds end to end, closing
# the gap between each module's own isolated coverage (test_dashboard_
# snapshot.py never runs its renderer through cadence-loop.sh; this file's
# Group 2 above never runs a renderer that narrates on stderr at all).
# Both wait on events (content on the display, narration in the log)
# rather than on fixed spans of time.
# ---------------------------------------------------------------------------

_MISS_NARRATION = "no fresh snapshot"


def _debug_log_text(debug_log_dir: pathlib.Path) -> str:
    if not debug_log_dir.exists():
        return ""
    return "".join(f.read_text(encoding="utf-8") for f in sorted(debug_log_dir.glob("*.log")))


def test_cadence_loop_keeps_snapshot_miss_narration_off_the_display_and_in_the_debug_log(
    tmp_path: pathlib.Path,
) -> None:
    """
    A tmux pane has exactly one terminal: stdout and stderr both paint it,
    with no separate channel for either. This test's captured "display
    stream" merges cadence-loop.sh's own stdout and stderr for that reason
    -- a plain stdout-only capture would let a narration line that merely
    escaped redirection into cadence-loop.sh's OWN stderr slip past
    unnoticed, exactly the gap a bare DEVNULL capture would hide.
    lib/snapshot.sh's by-design first-miss narration ("no fresh snapshot")
    must never reach that merged display stream -- it must land in
    cadence-loop.sh's pane-scoped debug log instead, and remain
    recoverable there for inspection. Exercised against a genuine cache
    miss: a snapshot key never written under a fresh, isolated temp root.
    The loop is stopped once the render's content has reached the display
    and its narration has reached either the log or (a failure) the
    display.
    """
    temp_root = tmp_path / "pgai_tmp"
    debug_log_dir = temp_root / "dashboard" / "logs"
    env = _base_env()
    env["PGAI_DASHBOARD_CADENCE_FORCE_STATE"] = "active"
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)

    driver = (
        f"source {shlex.quote(str(_SNAPSHOT_LIB_SH))}; "
        "dashboard_snapshot_render clean-display-key 3600 clean-display-test -- "
        'printf "RENDERED-COLUMN-CONTENT\\n"'
    )
    with _running_cadence_loop(env, 0.3, ["bash", "-c", driver], merge_stderr=True) as display:
        _wait_for(
            lambda: "RENDERED-COLUMN-CONTENT" in display.text
            and ("[dashboard-snapshot]" in display.text
                 or _MISS_NARRATION in _debug_log_text(debug_log_dir)),
            "a rendered miss and its narration",
            [display],
            lambda: f"{_diagnostics(display)}\n--- debug log ---\n{_debug_log_text(debug_log_dir)}",
        )
    display_stream = display.text

    assert "RENDERED-COLUMN-CONTENT" in display_stream, (
        f"expected the renderer's own content to reach the display stream; "
        f"display_stream={display_stream!r}"
    )
    assert "[dashboard-snapshot]" not in display_stream, (
        f"snapshot cache-miss narration must never reach the pane's display "
        f"stream; display_stream={display_stream!r}"
    )

    log_files = list(debug_log_dir.glob("*.log")) if debug_log_dir.exists() else []
    assert log_files, f"expected a pane-scoped debug log to exist under {debug_log_dir}"
    combined_log = _debug_log_text(debug_log_dir)
    assert "[dashboard-snapshot]" in combined_log and _MISS_NARRATION in combined_log, (
        f"expected the cache-miss narration to be recoverable from the pane "
        f"debug log; got: {combined_log!r}"
    )


def test_cadence_loop_panes_keep_rendering_cleanly_after_the_snapshot_cache_directory_is_removed_mid_run(
    tmp_path: pathlib.Path,
) -> None:
    """
    Twin panes sharing one snapshot cache key must keep rendering, and must
    keep any re-triggered miss narration off their displays, even when the
    shared cache directory disappears out from under them mid-session (a
    teardown race, a disk-pressure eviction, or an operator clearing the
    temp root by hand). Runs two real cadence-loop.sh processes -- the
    twin-pane shape the shared snapshot cache exists to serve -- against
    the SAME snapshot key, removes the snapshot directory partway through
    the run, and asserts across the WHOLE run (spanning both before and
    after the removal): neither pane's merged display stream (stdout and
    stderr combined -- a real tmux pane has exactly one terminal for both;
    see the sibling snapshot-miss test above for why a stdout-only capture
    would hide this) ever carries "[dashboard-snapshot]" narration, both
    panes keep producing real rendered content after the removal (never a
    blank pane), and the fresh miss the removal causes is recoverable from
    the shared pane debug log -- never from either pane's display stream.

    The removal waits until each pane has painted twice. A pane's second
    paint can only begin once its first render -- including that render's
    snapshot write -- has finished, so no miss render is in flight to
    write pre-removal content back into a recreated directory. The
    directory is renamed away (atomic, so a pane mid-write cannot make the
    removal fail) and then deleted.
    """
    temp_root = tmp_path / "pgai_tmp"
    env = _base_env()
    env["PGAI_DASHBOARD_CADENCE_FORCE_STATE"] = "active"
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)
    snapshot_dir = temp_root / "dashboard" / "snapshots"
    debug_log_dir = temp_root / "dashboard" / "logs"

    renderer_script = tmp_path / "twin_renderer.sh"
    renderer_script.write_text(
        "#!/usr/bin/env bash\ndate +%s.%N\nprintf 'TWIN-RENDER-CONTENT\\n'\n",
        encoding="utf-8",
    )
    renderer_script.chmod(0o755)

    driver = (
        f"source {shlex.quote(str(_SNAPSHOT_LIB_SH))}; "
        f"dashboard_snapshot_render twin-pane-key 3600 twin-pane-test -- "
        f"{shlex.quote(str(renderer_script))}"
    )
    renderer = ["bash", "-c", driver]

    with contextlib.ExitStack() as stack:
        pane_a = stack.enter_context(_running_cadence_loop(env, 0.3, renderer, merge_stderr=True))
        pane_b = stack.enter_context(_running_cadence_loop(env, 0.3, renderer, merge_stderr=True))
        panes = [pane_a, pane_b]

        def diag() -> str:
            return (f"--- pane A ---\n{pane_a.text!r}\n--- pane B ---\n{pane_b.text!r}\n"
                    f"--- debug log ---\n{_debug_log_text(debug_log_dir)}")

        def narration_on_a_display() -> bool:
            return any("[dashboard-snapshot]" in pane.text for pane in panes)

        _wait_for(
            lambda: narration_on_a_display()
            or (all(len(pane.timestamps()) >= 2 for pane in panes) and snapshot_dir.exists()),
            "two renders from each pane with the snapshot cache in place",
            panes, diag,
        )
        assert snapshot_dir.exists(), (
            f"expected a snapshot cache directory at {snapshot_dir} before removal\n{diag()}"
        )
        misses_before = _debug_log_text(debug_log_dir).count(_MISS_NARRATION)

        removed = temp_root / "dashboard" / "snapshots.removed"
        deletion_time = time.time()
        snapshot_dir.rename(removed)
        shutil.rmtree(removed)

        _wait_for(
            lambda: narration_on_a_display()
            or (all(pane.timestamps() and pane.timestamps()[-1] > deletion_time for pane in panes)
                and _debug_log_text(debug_log_dir).count(_MISS_NARRATION) > misses_before),
            "a fresh render from each pane, and a logged miss, after the cache removal",
            panes, diag,
        )

    for label, pane in (("pane A", pane_a), ("pane B", pane_b)):
        display_stream = pane.text
        assert "[dashboard-snapshot]" not in display_stream, (
            f"{label} display stream carried snapshot narration after the "
            f"cache directory was removed mid-run; display_stream={display_stream!r}"
        )
        timestamps = pane.timestamps()
        assert timestamps, f"{label} produced no renders at all; display_stream={display_stream!r}"
        assert max(timestamps) > deletion_time, (
            f"{label} did not render again after the cache directory was "
            f"removed at {deletion_time}; last render at {max(timestamps)}, "
            f"display_stream={display_stream!r}"
        )
        assert "TWIN-RENDER-CONTENT" in display_stream, (
            f"{label} display must still carry real rendered content after "
            f"the cache directory was removed; display_stream={display_stream!r}"
        )

    log_after = _debug_log_text(debug_log_dir)
    assert log_after.count(_MISS_NARRATION) > misses_before, (
        "expected the cache directory's removal to trigger a fresh miss "
        f"narration appended to the pane debug log; got: {log_after!r}"
    )
    assert "[dashboard-snapshot]" in log_after, (
        f"expected the re-triggered miss to be recoverable from the pane "
        f"debug log; got: {log_after!r}"
    )


# ---------------------------------------------------------------------------
# Group 3 -- Real tmux coverage (tmux-gated, private socket)
# ---------------------------------------------------------------------------


_TMUX_SKIP_LABEL = "dashboard-cadence"


class PrivateTmux(NamedTuple):
    real_tmux: str
    socket_name: str

    def run(self, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.real_tmux, "-L", self.socket_name, *args],
            capture_output=True, text=True, timeout=timeout,
        )


@pytest.fixture
def private_tmux() -> Iterator[PrivateTmux]:
    # Resolve without skipping yet: the skip decision below happens INSIDE
    # the try block so that, on a scrubbed PATH, this fixture's own finally
    # clause genuinely runs guard.teardown() as a real no-op (proving the
    # teardown path exercised, not merely reasoned about) instead of the
    # skip unwinding before the try is ever entered.
    guard = try_resolve_binary("tmux")
    socket_name = f"pgai-cadence-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        if not guard.resolved:
            narrate_binary_skip("tmux", label=_TMUX_SKIP_LABEL)
        pt = PrivateTmux(real_tmux=guard.path, socket_name=socket_name)
        yield pt
    finally:
        guard.teardown(
            lambda path: subprocess.run(
                [path, "-L", socket_name, "kill-server"], capture_output=True, timeout=10
            )
        )


@contextlib.contextmanager
def _attached_client(private_tmux: PrivateTmux, session: str) -> Iterator[None]:
    """Attach a real tmux client to <session> on a pseudo-terminal for the
    duration of the block, then detach it and reap it."""
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        os.environ["TERM"] = "xterm-256color"
        os.execv(private_tmux.real_tmux, [private_tmux.real_tmux, "-L", private_tmux.socket_name,
                                          "attach-session", "-t", session])
    stop = threading.Event()

    def _drain() -> None:
        # Keep reading the client's screen output so it never blocks on a
        # full pseudo-terminal buffer.
        while not stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready and not os.read(fd, 65536):
                    return
            except OSError:
                return

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()
    try:
        deadline = time.monotonic() + _HANG_GUARD_SECONDS
        while private_tmux.run("display-message", "-p", "-t", session,
                               "#{session_attached}").stdout.strip() in ("", "0"):
            if time.monotonic() > deadline:
                pytest.fail(f"a client never attached to {session}")
            time.sleep(0.05)
        yield
    finally:
        private_tmux.run("detach-client", "-s", session)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
        stop.set()
        drainer.join(timeout=5)
        with contextlib.suppress(OSError):
            os.close(fd)


def _pane_ids(private_tmux: PrivateTmux, target: str) -> list[str]:
    return private_tmux.run("list-panes", "-t", target, "-F", "#{pane_id}").stdout.split()


def _visible(env: dict[str, str], pane_id: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(_CADENCE_LIB_SH))}; dashboard_pane_visible {shlex.quote(pane_id)}"],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_pane_visibility_reads_a_real_tmux_session(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    dashboard_pane_visible, against a REAL private-socket tmux session with
    two windows: with no client attached, NO pane is visible -- not even
    the window tmux marks active; with a client attached, exactly the
    active window's pane is visible and the other window's is not.
    """
    session = f"cadence-{uuid.uuid4().hex[:6]}"
    assert private_tmux.run("new-session", "-d", "-s", session, "-x", "80", "-y", "24", "-n", "winA").returncode == 0
    assert private_tmux.run("new-window", "-t", session, "-n", "winB").returncode == 0
    (pane_a,) = _pane_ids(private_tmux, f"{session}:winA")
    (pane_b,) = _pane_ids(private_tmux, f"{session}:winB")

    # Use a PATH-wrapped tmux so the library's internal `tmux` calls land
    # on the private socket. The wrapper lives under pytest's tmp_path.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "tmux"
    wrapper.write_text(f"#!/usr/bin/env bash\nexec {shlex.quote(private_tmux.real_tmux)} -L {shlex.quote(private_tmux.socket_name)} \"$@\"\n")
    wrapper.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PGAI_DASHBOARD_CADENCE_FORCE_STATE"] = ""
    env.pop("TMUX_PANE", None)

    # new-window made winB the active window -- but nobody is attached.
    assert _visible(env, pane_b) == "0", "a detached session shows no pane, even in the active window"
    assert _visible(env, pane_a) == "0"

    with _attached_client(private_tmux, session):
        assert _visible(env, pane_b) == "1", "the active window's pane is visible to an attached client"
        assert _visible(env, pane_a) == "0", "a pane in another window is hidden"
        assert private_tmux.run("select-window", "-t", f"{session}:winA").returncode == 0
        assert _visible(env, pane_a) == "1"
        assert _visible(env, pane_b) == "0"

    assert _visible(env, pane_a) == "0", "detaching hides every pane again"


def test_only_one_visibility_decider_exists_under_team_scripts_dashboard() -> None:
    """Exactly one function under team/scripts/dashboard queries tmux for a
    window's active flag or a session's attached-client count -- the two
    facts a visibility decision is made of: dashboard_pane_visible in
    lib/cadence.sh. (create.sh's status-format strings use #{window_active}
    to style tab labels; they are options handed to tmux, not a query.)"""
    dashboard_dir = _TEAM_DIR / "scripts" / "dashboard"
    query_re = re.compile(r"\btmux\s+(?:display-message|display|list-windows|list-panes|list-sessions|list-clients)\b")
    func_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")
    deciders = set()
    for path in sorted(dashboard_dir.rglob("*.sh")):
        current_function = "<top level>"
        logical = ""
        for raw in path.read_text(encoding="utf-8").splitlines():
            match = func_re.match(raw)
            if match:
                current_function = match.group(1)
            if raw.lstrip().startswith("#"):
                continue
            logical += raw
            if raw.endswith("\\"):
                continue
            if query_re.search(logical) and ("window_active" in logical or "session_attached" in logical):
                deciders.add(f"{path.relative_to(_TEAM_DIR)}:{current_function}")
            logical = ""
    assert sorted(deciders) == ["scripts/dashboard/lib/cadence.sh:dashboard_pane_visible"], sorted(deciders)


# ---------------------------------------------------------------------------
# Group 4 -- CPU-sample test
# ---------------------------------------------------------------------------


def _cpu_seconds_of_process_tree(pid: int) -> float:
    """utime+stime+cutime+cstime for <pid>, in seconds -- captures the
    loop process's own CPU plus every renderer child it has already
    reaped via its own `wait`/command-substitution (see
    dashboard_constants.sh's PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO
    comment for the full measurement-method writeup this mirrors)."""
    clk_tck = os.sysconf("SC_CLK_TCK")
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().split()
    utime, stime, cutime, cstime = (int(fields[i]) for i in (13, 14, 15, 16))
    return (utime + stime + cutime + cstime) / clk_tck


class CpuSample(NamedTuple):
    cpu_seconds: float
    interval_requests: int


def _measure_cpu_over_virtual_window(
    work_dir: pathlib.Path, state: str, interval: float, window_seconds: float
) -> CpuSample:
    """Run cadence-loop.sh pinned to <state> against a real renderer until
    its own schedule has covered <window_seconds>, then read its CPU cost.

    The window is counted on a virtual clock the `sleep` test double
    advances by each interval the loop requests, so it always holds
    exactly the renders the loop's schedule fits into it -- one render per
    elapsed interval plus the first paint -- however slowly the host runs
    them. CPU seconds are a cost the process itself pays, so they do not
    stretch with wall-clock delay either."""
    harness = _cadence_harness(work_dir, state)
    done = work_dir / "virtual-window.done"
    env = dict(harness.env)
    env["CADENCE_TEST_VIRTUAL_WINDOW_MS"] = str(int(round(window_seconds * 1000)))
    env["CADENCE_TEST_VIRTUAL_CLOCK"] = str(work_dir / "virtual-clock")
    env["CADENCE_TEST_WINDOW_DONE"] = str(done)
    renderer = str(_TEAM_DIR / "scripts" / "dashboard" / "legend-render.sh")
    proc = subprocess.Popen(
        ["bash", str(_CADENCE_LOOP_SH), "--interval", str(interval), "--jitter-bound", "0",
         "--", renderer, "--kanban-root", str(_REPO_ROOT)],
        env=env, cwd=str(_REPO_ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + _HANG_GUARD_SECONDS
        while not done.exists():
            if proc.poll() is not None:
                pytest.fail(
                    f"the {state} cadence loop exited with status {proc.returncode} before "
                    f"its schedule covered {window_seconds}s; events: {_read_events(harness.event_log)}"
                )
            if time.monotonic() > deadline:
                pytest.fail(
                    f"the {state} cadence loop's schedule did not cover {window_seconds}s "
                    f"within the {_HANG_GUARD_SECONDS:.0f}s hang guard; "
                    f"events: {_read_events(harness.event_log)}"
                )
            time.sleep(0.02)
        cpu = _cpu_seconds_of_process_tree(proc.pid)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=10)
    requests = sum(1 for e in _read_events(harness.event_log) if e.startswith("sleep "))
    return CpuSample(cpu_seconds=cpu, interval_requests=requests)


def test_hidden_window_cpu_cost_stays_under_the_recorded_budget_ratio(tmp_path: pathlib.Path) -> None:
    """
    THE BUDGET IS MEASURED, NOT INVENTED (task constraint). This test
    measures BOTH arms fresh, in this run, on this host:
      - "baseline": cadence-loop.sh pinned to the active (full-rate)
        cadence -- reproduces the pre-change behavior where hidden windows
        ticked at the same flat rate as active ones.
      - "hidden": the same renderer, same sampling window, pinned to the
        hidden (10x-slower) cadence.
    It then asserts hidden_cpu <= baseline_cpu * PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO
    -- a RATIO against a same-run, same-host baseline. The budget ratio
    itself is the named constant in dashboard_constants.sh, read here
    rather than duplicated as a literal, and that constant's own comment
    records the measurement method and the ratio observed when it was set.

    Sampling window and margin (stated in the test body and printed at
    assertion time):
      - Active interval: 0.2s; hidden interval = 0.2 * PGAI_DASHBOARD_HIDDEN_MULTIPLIER
      - Sampling window: 6s of the loop's own schedule, counted on the
        `sleep` test double's virtual clock (baseline renders 31x; the
        hidden arm renders once, its arrival render, and then only
        re-checks visibility), so host load cannot change how many renders
        either arm is charged for.
      - Margin: the budget ratio is read from PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO
        (0.5; see that constant's comment for the measured ratio).
    """
    active_interval = 0.2
    sampling_window = 6.0
    budget_ratio = float(_read_constant("PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO"))

    baseline = _measure_cpu_over_virtual_window(tmp_path / "active", "active", active_interval, sampling_window)
    hidden = _measure_cpu_over_virtual_window(tmp_path / "hidden", "hidden", active_interval, sampling_window)
    baseline_cpu, hidden_cpu = baseline.cpu_seconds, hidden.cpu_seconds

    assert baseline_cpu > 0, (
        f"baseline CPU measurement came back {baseline_cpu} -- the "
        f"baseline arm did not render anything over the {sampling_window}s "
        f"sampling window; the measurement harness itself is broken"
    )

    ratio = hidden_cpu / baseline_cpu
    print(
        f"\n[cadence-cpu-sample] sampling_window={sampling_window}s "
        f"active_interval={active_interval}s baseline_cpu={baseline_cpu:.4f}s "
        f"baseline_requests={baseline.interval_requests} hidden_cpu={hidden_cpu:.4f}s "
        f"hidden_requests={hidden.interval_requests} ratio={ratio:.4f} "
        f"budget_ratio={budget_ratio}"
    )

    assert hidden_cpu <= baseline_cpu * budget_ratio, (
        f"hidden-window CPU cost ({hidden_cpu:.4f}s, {hidden.interval_requests} "
        f"interval requests) exceeded {budget_ratio} of the same-run baseline "
        f"({baseline_cpu:.4f}s, {baseline.interval_requests} interval requests) "
        f"over a {sampling_window}s sampling window -- the hidden-window "
        f"slowdown may have regressed"
    )

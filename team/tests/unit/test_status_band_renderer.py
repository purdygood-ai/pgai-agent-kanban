"""
test_status_band_renderer.py
=============================
Behavioral unit tests for team/scripts/dashboard/status-band.sh, the
schedule window's per-agent status band: one row per roster agent
answering what that lane last did, when it last fired, and when it
fires next.

WHY THIS EXISTS
---------------
The band must distill each agent's own cron log into exactly one of four
short verdicts -- a completed-task count, an idle/no-BACKLOG exit, a HALT
skip, or a lock-contention deferral -- never the raw log line itself, and
must never drop a roster agent just because it has no log yet. The
roster (agent identity and NEXT text) has to come from the same schedule
source the roster band renderer (next-cron-firings.sh) uses, so the two
bands can never disagree about which agents exist or when they next
fire. This module locks in all of that behaviorally, against the real
script.

COVERAGE
--------
  - `--help` exits 0 and documents `--kanban-root`.
  - Each of the four LAST RESULT verdicts, from a fixture log built from
    the literal wake_driver.sh / lock-provenance log line shapes.
  - A roster agent with no cron log yet renders the explicit "no log
    yet" placeholder in both columns instead of being omitted.
  - A roster agent whose cron log exists but whose scanned window carries
    no line matching any of the four verdict shapes renders "no data
    yet" / "no result yet" and does not truncate the render loop --
    including every roster agent ordered after it, and a fixture
    reproducing the full seven-agent live-install shape.
  - The raw log line a verdict was distilled from never leaks into the
    rendered row.
  - NEXT-column parity: under an identical fixture crontab and a frozen
    clock, this band's NEXT text for a given agent is byte-identical to
    next-cron-firings.sh's own countdown/label for that same agent --
    covering both the sub-hour (mark + countdown) and weekly
    (day+time hint) entry shapes.
  - The roster is read from the schedule source, not a hardcoded tuple:
    an agent name that is not one of the five schedule-window columns
    still renders its own row.
  - Repeated renders under a PID 1 that never reaps leave no zombie and
    no extra process behind: every process the renderer starts is
    collected before it exits.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import select
import shutil
import signal
import stat
import subprocess
import time

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.local_timestamps import today_local_iso

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPT = _TEAM_DIR / "scripts" / "dashboard" / "status-band.sh"
_NEXT_CRON_FIRINGS_SH = _TEAM_DIR / "scripts" / "dashboard" / "next-cron-firings.sh"

_TIMEOUT_SECONDS = 15


def _write_log(path: pathlib.Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(
    kanban_root: pathlib.Path, extra_args: list[str] | None = None, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run status-band.sh standalone (no tmux) against a fixture root."""
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    if extra_env:
        env.update(extra_env)
    args = ["bash", str(_SCRIPT), "--kanban-root", str(kanban_root)]
    if extra_args:
        args += extra_args
    return subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, env=env)


def _run_next_cron_firings(
    kanban_root: pathlib.Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(_NEXT_CRON_FIRINGS_SH), "--kanban-root", str(kanban_root), "--no-color"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        env=env,
    )


def _install_crontab(text: str) -> None:
    """Install a synthetic crontab through the autouse crontab-sandbox stub
    (team/tests/conftest.py's `_sandbox_crontab`) -- writes to the test's
    own tmp_path/.fake_crontab, never the real operator crontab."""
    result = subprocess.run(
        ["crontab", "-"],
        input=text,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=10,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


_DATE_STUB_TEMPLATE = """\
#!/usr/bin/env bash
# Test-only stub (test_status_band_renderer.py): answers `date +%s` with a
# fixed epoch (PGAI_TEST_FIXED_EPOCH) so both renderers under test compute
# an identical "now"; every other invocation form passes straight through
# to the real binary.
if [[ "$1" == "+%s" ]] && [[ -n "${{PGAI_TEST_FIXED_EPOCH:-}}" ]]; then
  printf '%s\\n' "$PGAI_TEST_FIXED_EPOCH"
  exit 0
fi
exec "{real_date}" "$@"
"""


def _install_date_stub(tmp_path: pathlib.Path, monkeypatch, epoch: int) -> None:
    real_date = shutil.which("date")
    if real_date is None:  # pragma: no cover -- every POSIX host has date
        pytest.skip("no 'date' binary resolvable on PATH")
    stub_dir = tmp_path / "_date_stub_bin"
    stub_dir.mkdir(exist_ok=True)
    stub_path = stub_dir / "date"
    stub_path.write_text(_DATE_STUB_TEMPLATE.format(real_date=real_date), encoding="utf-8")
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PGAI_TEST_FIXED_EPOCH", str(epoch))


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
# Header row
# ---------------------------------------------------------------------------


def test_header_row_names_all_four_columns(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanroot"
    (kanban_root / "logs").mkdir(parents=True)
    _install_crontab("* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm --sleep=0\n")

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    header = result.stdout.splitlines()[0]
    for column in ("AGENT", "LAST FIRED", "LAST RESULT", "NEXT"):
        assert column in header, f"{column!r} missing from header {header!r}"


# ---------------------------------------------------------------------------
# The four LAST RESULT verdicts
# ---------------------------------------------------------------------------


def test_completed_verdict_shows_count_not_raw_line(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab("* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder --sleep=12\n")
    _write_log(
        logs_dir / "cron-coder.log",
        [
            f"[{today_local_iso(2, 0, 25)}] wake(coder): project demo: "
            "done: completed=3 project_elapsed=40s batch_elapsed=61s "
            'reason="reached MAX_TASKS_PER_WAKE (3)"',
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next(ln for ln in result.stdout.splitlines() if ln.startswith("CODER"))
    assert "completed 3" in row, row
    assert "batch_elapsed" not in row, row
    assert "reason=" not in row, row


def test_idle_verdict_shows_no_backlog_label(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab("* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm --sleep=0\n")
    _write_log(
        logs_dir / "cron-pm.log",
        [
            f"[{today_local_iso(2, 1, 11)}] wake(pm): project demo: "
            "done: completed=0 project_elapsed=7s batch_elapsed=8s "
            'reason="no more BACKLOG tasks in pm queue"',
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next(ln for ln in result.stdout.splitlines() if ln.startswith("PM"))
    assert "idle" in row, row
    assert "BACKLOG" in row, row
    assert "no more BACKLOG tasks in pm queue" not in row, row


def test_halt_verdict_shows_halt_skip_label(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab("* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm --sleep=24\n")
    _write_log(
        logs_dir / "cron-cm.log",
        [
            f"[{today_local_iso(2, 0, 36)}] wake(cm): "
            "HALT flag detected at /fixture/HALT -- stopping batch loop cleanly",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next(ln for ln in result.stdout.splitlines() if ln.startswith("CM"))
    assert "HALT skip" in row, row
    assert "/fixture/HALT" not in row, row


def test_halt_verdict_from_per_project_halt_present_line(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab("0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n")
    _write_log(
        logs_dir / "cron-overwatch.log",
        [
            f"[{today_local_iso(3, 30, 0)}] wake(overwatch): project demo: "
            "per-project HALT present, resolution stage evaluated, "
            "skipping task processing",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next(ln for ln in result.stdout.splitlines() if ln.startswith("OVERWATCH"))
    assert "HALT skip" in row, row


def test_contended_verdict_shows_contended_label(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab("0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n")
    _write_log(
        logs_dir / "cron-overwatch.log",
        [
            f"[{today_local_iso(3, 30, 4)}] wake(overwatch): project demo: "
            "repo lock held by holder=coder pid=123 op=wake-chain:demo "
            "task=none -- deferring for an end-of-iteration retry",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next(ln for ln in result.stdout.splitlines() if ln.startswith("OVERWATCH"))
    assert "contended" in row, row
    assert "holder=coder" not in row, row
    assert "pid=123" not in row, row


# ---------------------------------------------------------------------------
# Missing-log placeholder row
# ---------------------------------------------------------------------------


def test_missing_log_renders_placeholder_row_not_omitted(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanroot"
    (kanban_root / "logs").mkdir(parents=True)
    _install_crontab("* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=writer --sleep=24\n")
    # No cron-writer.log is ever created.

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next((ln for ln in result.stdout.splitlines() if ln.startswith("WRITER")), None)
    assert row is not None, (
        f"WRITER row missing entirely -- an agent with no log yet must " f"still render its own row: {result.stdout!r}"
    )
    assert "no log yet" in row, row


# ---------------------------------------------------------------------------
# Log exists but carries no distillable verdict -- must not truncate the
# render loop.
# ---------------------------------------------------------------------------


def test_log_exists_with_no_verdict_renders_placeholder_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """A cron log that exists but whose scanned window carries no line
    matching any of the four verdict shapes (a firing still in progress,
    a freshly created log, or one that predates the timestamped log()
    convention) must render the script's own "no data yet" / "no result
    yet" placeholders and exit 0 -- never abort the render loop."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab("0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n")
    _write_log(
        logs_dir / "cron-overwatch.log",
        [
            "starting sweep",
            "scanning project demo",
            "no actionable findings this cycle",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next(
        (ln for ln in result.stdout.splitlines() if ln.startswith("OVERWATCH")),
        None,
    )
    assert row is not None, (
        f"OVERWATCH row missing -- a log with no distillable verdict must "
        f"still render its own row, not truncate the band: {result.stdout!r}"
    )
    assert "no data yet" in row, row
    assert "no result yet" in row, row
    assert "starting sweep" not in row, row


def test_agent_after_non_distillable_agent_still_renders(
    tmp_path: pathlib.Path,
) -> None:
    """The non-distillable-verdict case must not truncate the render loop
    for roster agents ordered after it: every agent past the first
    non-distillable one must still render its own row."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab(
        "0 4 * * 0 $KANBAN_ROOT/scripts/wake-batch.sh --agent=cleanup\n"
        "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n"
    )
    # CLEANUP's log exists but carries only bare, non-timestamped error
    # lines -- no distillable verdict (mirrors the live install's
    # cron-cleanup.log). OVERWATCH is ordered after it in the crontab and
    # has a normal completed-verdict log.
    _write_log(
        logs_dir / "cron-cleanup.log",
        [
            "ERROR: no project specified; usage: cleanup.sh --project <name>",
            "ERROR: no project specified; usage: cleanup.sh --project <name>",
        ],
    )
    _write_log(
        logs_dir / "cron-overwatch.log",
        [
            f"[{today_local_iso(3, 30, 5)}] wake(overwatch): project demo: "
            "done: completed=2 project_elapsed=12s batch_elapsed=14s "
            'reason="reached MAX_TASKS_PER_WAKE (2)"',
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    lines = result.stdout.splitlines()
    cleanup_row = next((ln for ln in lines if ln.startswith("CLEANUP")), None)
    overwatch_row = next((ln for ln in lines if ln.startswith("OVERWATCH")), None)

    assert cleanup_row is not None, f"CLEANUP row missing: {result.stdout!r}"
    assert "no data yet" in cleanup_row, cleanup_row
    assert "no result yet" in cleanup_row, cleanup_row

    assert overwatch_row is not None, (
        f"OVERWATCH row missing -- an agent ordered after a "
        f"non-distillable-verdict agent must still render its own row, "
        f"proving the render loop was not truncated: {result.stdout!r}"
    )
    assert "completed 2" in overwatch_row, overwatch_row


def test_five_agent_fixture_plus_cleanup_and_overwatch_renders_full_roster(
    tmp_path: pathlib.Path,
) -> None:
    """Five agents with parseable idle verdicts, a CLEANUP log of bare
    error lines, and an OVERWATCH log with timestamped lines but no
    done: line -- all seven roster rows must render and the script
    must exit 0."""
    kanban_root = tmp_path / "kanroot"
    logs_dir = kanban_root / "logs"
    _install_crontab(
        "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm --sleep=0\n"
        "1-59/2 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder  --sleep=0\n"
        "1-59/2 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=writer --sleep=21\n"
        "*/2 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester --sleep=42\n"
        "*/2 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm     --sleep=21\n"
        "0 4 * * 0 $KANBAN_ROOT/scripts/wake-batch.sh --agent=cleanup\n"
        "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n"
    )
    for agent in ("pm", "coder", "writer", "tester", "cm"):
        _write_log(
            logs_dir / f"cron-{agent}.log",
            [
                f"[{today_local_iso(2, 1, 11)}] wake({agent}): project demo: "
                "done: completed=0 project_elapsed=7s batch_elapsed=8s "
                f'reason="no more BACKLOG tasks in {agent} queue"',
            ],
        )
    _write_log(
        logs_dir / "cron-cleanup.log",
        [
            "ERROR: no project specified; usage: cleanup.sh --project <name>",
            "ERROR: no project specified; usage: cleanup.sh --project <name>",
            "ERROR: no project specified; usage: cleanup.sh --project <name>",
            "ERROR: no project specified; usage: cleanup.sh --project <name>",
            "ERROR: no project specified; usage: cleanup.sh --project <name>",
        ],
    )
    _write_log(
        logs_dir / "cron-overwatch.log",
        [
            f"[{today_local_iso(3, 30, 5)}] wake(overwatch): starting sweep",
            f"[{today_local_iso(3, 30, 6)}] wake(overwatch): scanning project demo",
        ],
    )

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    lines = result.stdout.splitlines()
    for agent in ("PM", "CODER", "WRITER", "TESTER", "CM", "CLEANUP", "OVERWATCH"):
        row = next((ln for ln in lines if ln.startswith(agent)), None)
        assert row is not None, f"{agent} row missing -- full roster must render: {result.stdout!r}"


# ---------------------------------------------------------------------------
# NEXT-column parity with the roster band renderer
# ---------------------------------------------------------------------------

_PARITY_CRONTAB = (
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm     --sleep=0\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder  --sleep=12\n"
    "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n"
)


def test_next_column_matches_roster_band_under_identical_clock(tmp_path: pathlib.Path, monkeypatch) -> None:
    kanban_root = tmp_path / "kanroot"
    (kanban_root / "logs").mkdir(parents=True)
    _install_crontab(_PARITY_CRONTAB)

    now = int(__import__("time").time())
    fixed_epoch = now - (now % 60) + 5
    _install_date_stub(tmp_path, monkeypatch, fixed_epoch)

    status_env = {"PGAI_TEST_FIXED_EPOCH": str(fixed_epoch)}
    status_result = _run(kanban_root, extra_env=status_env)
    assert status_result.returncode == 0, status_result.stderr

    firings_result = _run_next_cron_firings(kanban_root, extra_env=status_env)
    assert firings_result.returncode == 0, firings_result.stderr

    # status-band.sh's columns are fixed-width and space-padded (AGENT,
    # LAST FIRED, LAST RESULT each end in at least one padding space before
    # the next column, on top of the single-space delimiter) -- every
    # column boundary is therefore a run of 2+ spaces, while no column's
    # own content (agent tokens, "no log yet", a mark+countdown or a
    # day+time hint) ever contains two consecutive spaces. Splitting on
    # that run isolates NEXT without hardcoding the renderer's column
    # widths in this test.
    import re as _re

    def _next_column(row: str) -> str:
        return _re.split(r"\s{2,}", row.strip())[-1]

    # Sub-hour entry (PM: "PM :MM (in M:SS)") -- the exact chunk after the
    # agent token must be identical in both renderers.
    for agent in ("PM", "CODER"):
        status_row = next(ln for ln in status_result.stdout.splitlines() if ln.startswith(agent))
        status_next = _next_column(status_row)
        assert f"{agent} {status_next}" in firings_result.stdout, (
            f"status-band NEXT for {agent} ({status_next!r}) not found "
            f"verbatim in next-cron-firings.sh output: "
            f"{firings_result.stdout!r}"
        )

    # Weekly entry (OVERWATCH: "OVERWATCH Day Ham/pm") -- same parity check
    # for the STR (day+time hint) entry shape, not just the INT shape.
    overwatch_row = next(ln for ln in status_result.stdout.splitlines() if ln.startswith("OVERWATCH"))
    overwatch_next = _next_column(overwatch_row)
    assert f"OVERWATCH {overwatch_next}" in firings_result.stdout, (
        f"status-band NEXT for OVERWATCH ({overwatch_next!r}) not found "
        f"verbatim in next-cron-firings.sh output: {firings_result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Roster comes from the schedule source, never a hardcoded tuple
# ---------------------------------------------------------------------------


def test_agent_outside_the_five_named_columns_still_renders_its_own_row(
    tmp_path: pathlib.Path,
) -> None:
    """A schedule entry for an agent name that is not one of the five
    schedule-window columns (pm/coder/writer/tester/cm) must still get its
    own row -- proof the roster is read from the crontab, not a literal
    tuple of known agent names."""
    kanban_root = tmp_path / "kanroot"
    (kanban_root / "logs").mkdir(parents=True)
    _install_crontab("* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=extra --sleep=6\n")

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr

    row = next((ln for ln in result.stdout.splitlines() if ln.startswith("EXTRA")), None)
    assert row is not None, (
        f"EXTRA agent row missing -- roster must come from the schedule "
        f"source, never a hardcoded agent tuple: {result.stdout!r}"
    )
    assert "no log yet" in row, row


# ---------------------------------------------------------------------------
# Empty roster
# ---------------------------------------------------------------------------


def test_empty_crontab_prints_manual_mode_notice_not_a_crash(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = tmp_path / "kanroot"
    (kanban_root / "logs").mkdir(parents=True)
    _install_crontab("")

    result = _run(kanban_root)
    assert result.returncode == 0, result.stderr
    assert "no roster agents found" in result.stdout


# ---------------------------------------------------------------------------
# Process hygiene: nothing the renderer starts outlives it
#
# A pane re-runs the renderer every refresh. A child it starts and does not
# wait for is reparented to PID 1 when the renderer exits; under a PID 1
# that never reaps (a container's `sleep infinity` without --init) that
# child stays a zombie for good, one more per render. The case below builds
# exactly that: a user+PID namespace whose PID 1 is `sleep`, and a driver
# that renders the band repeatedly inside it.
# ---------------------------------------------------------------------------

_UNSHARE_SKIP_LABEL = "status-band-reaping"
_RENDERS = 10
_HANG_GUARD_SECONDS = 120.0

# The namespace's PID 1 outlives the driver; the test ends the whole
# namespace by killing unshare (--kill-child takes PID 1, and with it every
# process in the namespace, down too).
_NAMESPACE_INIT = 'bash "$1" & exec sleep 600'

# Runs inside the namespace as PID 1's child. Reports, one line each:
#   ROWS <n>                   agent rows in the first render's output
#   RUN <i> <zombies> <procs>  after render i: Z-state processes in the
#                              namespace ("pid:comm" words) and the count of
#                              every process in it
#   END
_RENDER_DRIVER = r"""
set -u
_zombies() {
    local f line rest state pid comm out=""
    for f in /proc/[0-9]*/stat; do
        { read -r line < "$f"; } 2>/dev/null || continue
        rest="${line##*) }"
        read -r state _ <<<"$rest"
        [[ "$state" == Z ]] || continue
        pid="${f#/proc/}"; pid="${pid%/stat}"
        comm="${line#*(}"; comm="${comm%)*}"
        out+="${pid}:${comm},"
    done
    printf '%s' "${out:--}"
}
_procs() {
    local f n=0
    for f in /proc/[0-9]*/stat; do [[ -e "$f" ]] && n=$((n + 1)); done
    printf '%s' "$n"
}
for i in $(seq "$RENDER_COUNT"); do
    "$RENDER_SCRIPT" --kanban-root "$KANBAN_ROOT" > "$RENDER_OUT" 2>&1 \
        || { echo "FAIL render $i exited $?: $(tr '\n' '|' < "$RENDER_OUT")"; exit 1; }
    if [[ "$i" == 1 ]]; then
        echo "ROWS $(grep -c -E '^(PM|CODER|WRITER|TESTER|CM) ' "$RENDER_OUT")"
    fi
    echo "RUN $i $(_zombies) $(_procs)"
done
echo "END"
"""


def _require_unshare_pid_namespace(unshare: str) -> None:
    """Skip, naming the reason, unless unshare can create a user+PID
    namespace with its own /proc here."""
    probe = subprocess.run([unshare, "-Urpf", "--mount-proc", "true"], capture_output=True, text=True, timeout=30)
    if probe.returncode != 0:
        reason = (
            "unshare cannot create a user+PID namespace on this host "
            f"(unshare -Urpf --mount-proc exited {probe.returncode}: {probe.stderr.strip()})"
        )
        print(f"\n[{_UNSHARE_SKIP_LABEL}] SKIP — {reason}", flush=True)
        pytest.skip(reason)


def test_repeated_renders_leave_no_zombie_under_a_non_reaping_pid_1(tmp_path: pathlib.Path) -> None:
    """
    Under a PID 1 that never reaps (`sleep`, in a user+PID namespace), ten
    renders of a populated band leave no Z-state process behind, and the
    namespace's process count is the same after every render: the log
    reader each agent row starts is waited for before the renderer exits.
    The logs are larger than a pipe buffer, so the reader is still writing
    when the distill loop stops early.
    """
    unshare = require_binary("unshare", _UNSHARE_SKIP_LABEL).path
    require_binary("sleep", _UNSHARE_SKIP_LABEL)
    _require_unshare_pid_namespace(unshare)

    # A throwaway kanban root: all five live-install names set or cleared.
    kanban_root = tmp_path / "kanroot"
    (kanban_root / "tmp").mkdir(parents=True)
    agents = ("pm", "coder", "writer", "tester", "cm")
    _install_crontab(
        "".join(f"* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent={agent} --sleep=0\n" for agent in agents)
    )
    for agent in agents:
        filler = [f"[{today_local_iso(1, 0, 0)}] wake({agent}): stagger line {n:04d} " + "x" * 40 for n in range(3000)]
        _write_log(
            kanban_root / "logs" / f"cron-{agent}.log",
            filler
            + [
                f"[{today_local_iso(2, 1, 11)}] wake({agent}): project demo: "
                "done: completed=0 project_elapsed=7s batch_elapsed=8s "
                f'reason="no more BACKLOG tasks in {agent} queue"',
            ],
        )
    driver = tmp_path / "driver.sh"
    driver.write_text(_RENDER_DRIVER, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k not in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT")}
    env.update(
        KANBAN_ROOT=str(kanban_root),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(kanban_root),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(kanban_root / "tmp"),
        NO_COLOR="1",
        TERM="dumb",
        RENDER_SCRIPT=str(_SCRIPT),
        RENDER_OUT=str(tmp_path / "render.out"),
        RENDER_COUNT=str(_RENDERS),
    )

    proc = subprocess.Popen(
        [unshare, "-Urpf", "--kill-child", "--mount-proc", "bash", "-c", _NAMESPACE_INIT, "init", str(driver)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    runs: list[tuple[str, int]] = []
    rows = -1
    output = b""
    try:
        deadline = time.monotonic() + _HANG_GUARD_SECONDS
        while "END" not in output.decode(errors="replace").splitlines():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(f"the namespace driver did not finish within {_HANG_GUARD_SECONDS:.0f}s:\n{output!r}")
            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
            if not ready:
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                pytest.fail(f"the namespace exited before its driver finished:\n{output!r}")
            output += chunk
        for line in output.decode(errors="replace").splitlines():
            key, _, value = line.partition(" ")
            if key == "FAIL":
                pytest.fail(f"the namespace driver failed: {line}\n{output!r}")
            if key == "ROWS":
                rows = int(value)
            elif key == "RUN":
                _, zombies, procs = value.split()
                runs.append((zombies, int(procs)))
    finally:
        # Killing unshare takes the namespace's PID 1 (--kill-child), and so
        # every process inside it, down -- whatever the outcome above.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)
        proc.stdout.close()

    assert rows == len(agents), f"the band did not render one row per roster agent:\n{output!r}"
    assert len(runs) == _RENDERS, output
    leaked = [(n, zombies) for n, (zombies, _) in enumerate(runs, 1) if zombies != "-"]
    assert not leaked, f"zombies left after renders (render, pid:comm): {leaked}"
    counts = [procs for _, procs in runs]
    assert len(set(counts)) == 1, f"the namespace's process count changed across renders: {counts}"

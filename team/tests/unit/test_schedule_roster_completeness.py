"""
test_schedule_roster_completeness.py
=====================================
Regression coverage locking in a fixture-driven guarantee that the two
next-fire schedule homes -- next-cron-firings.sh
(the window-4 pane) and show-multi.sh --mode header's single-project
delegate, show-header.sh (the window-0 header block) -- never lose a
roster row without saying so.

WHY THIS EXISTS
---------------
Before the roster-sizing fix landed, show-header.sh's cron
column read from a hardcoded six-name array (pm/coder/writer/tester/cm/
cleanup); a roster entry outside that list -- OVERWATCH -- was silently
absent from window-0 no matter how much space the pane had. Separately,
next-cron-firings.sh printed every row it parsed unconditionally, with no
notion of the hosting pane's height, so a roster taller than its tmux
tile scrolled the top rows off with nothing telling the operator rows
were missing. This module locks in both fixes behaviourally: the roster
is read from the schedule source (never a literal tuple), and a viewport
too small for the roster drops entries with an explicit "+N more" marker
rather than losing them silently.

A later change (the roster BAND redesign) replaced the tall vertical list
with a compact columnar band: at default geometry the whole roster packs
into at most PGAI_ROSTER_BAND_MAX_ROWS rows, entries joined by
PGAI_ROSTER_BAND_SEPARATOR (team/scripts/lib/dashboard_constants.sh), via
the single shared renderer at team/scripts/dashboard/lib/roster_band.sh.
Only when the hosting pane is too NARROW for the band to pack into that
many rows does the renderer fall back to the pre-existing vertical list,
which then applies the pre-existing height-based fit -- the same "+N
more" narration this module has always locked in, now reached through a
width-triggered fallback rather than unconditionally. This module's
helpers therefore assert on entry PRESENCE and entry ORDER by scanning
the whole rendered output for each roster token as a whole word, rather
than assuming one entry starts each output line -- a packed band row can
carry several entries, the vertical fallback still carries exactly one.

FIXTURE STRATEGY
-----------------
- **Crontab**: installed through the crontab-sandbox stub every test in
  this tree already gets from the autouse `_sandbox_crontab` fixture in
  team/tests/conftest.py (`crontab -` writes to $HOME/.fake_crontab; `-l`
  reads it back). This is the only crontab command any test here issues,
  and it never touches the operator's real crontab. Rows fire every
  minute with `--sleep=N` stagger, mirroring
  templates/install/crontab-large.example; weekly rows (OVERWATCH,
  CLEANUP, and the synthetic 8th row EXTRA) use a distinct day/hour so
  they render as a "Day Ham/pm" sentinel.
- **Deterministic "now"**: both renderers ultimately compute `now` from
  `date +%s`. A local `date` stub (deterministic_now_bin fixture)
  answers that one invocation form with a fixed epoch whose within-the-
  minute seconds component is 59, and passes every other `date` call
  straight through to the real binary. It exists so INT/STR
  classification and marker/budget arithmetic are reproducible for the
  presence- and count-based assertions elsewhere in this module -- it is
  NOT how window-4's row order is verified (see next paragraph).
- **Window-4 roster order is verified two ways, neither of them a clock
  trick that makes a soonest-first sort coincidentally look right**:
  `adversarial_now_bin` fixes "now" to a within-the-minute second (30)
  chosen to be as far as possible from every `--sleep=N` boundary
  (0/12/24/36/48) in `_FULL_ROSTER_CRONTAB`, so a soonest-first sort would
  produce a row order with NO relationship to roster order at that
  instant (worked out in `_adversarial_now_epoch`'s docstring) -- a test
  that only passes at this epoch because the code renders fixed roster
  order regardless of clock position, never because the epoch happens to
  make the two orderings agree. Separately,
  `test_next_cron_firings_pm_first_stable_across_real_time` runs the
  renderer against the live wall clock twice, a couple of real seconds
  apart, and asserts the row order does not move -- the direct behavioral
  regression test for a soonest-first sort visibly reordering across a
  real-time gap like this on real crontab data; roster order does not.
- **Deterministic pane geometry**: a local `tmux` stub (the
  pane_height_bin / pane_width_bin fixtures, sharing one underlying
  install) answers `tmux display-message -p -t <pane> '#{pane_height}'`
  with a fixed row count set per test via PGAI_TEST_PANE_HEIGHT, and
  `'#{pane_width}'` with a fixed column count via PGAI_TEST_PANE_WIDTH --
  either query left unset by a test degrades to "unqueryable" exactly as
  next_fire_pane_height / next_fire_pane_width do against a real
  non-tmux invocation, never a guessed value. A test forces the roster
  band's vertical fallback with a narrow pane_width_bin value (the band
  cannot pack the roster within PGAI_ROSTER_BAND_MAX_ROWS rows at that
  width) and then, independently, sizes the fallback's own "+N more"
  narration with pane_height_bin -- the two dimensions are orthogonal
  fixtures because the renderer's two decisions (band-vs-vertical, then
  how many vertical rows fit) are themselves independent. Neither stub
  invokes the real tmux binary -- team/tests/fixtures/binary_guard.py
  guards calls to a REAL external binary; a fake stub that shadows the
  name entirely needs no guard (docs/test-authoring-guidelines.md, "Reach
  for an external binary only through the shared guard", "When
  acceptable").

ACCEPTANCE CRITERIA COVERED
----------------------------
  1. TestFullRosterRendersEveryRow -- the current seven-entry roster
     (pm/coder/writer/tester/cm/overwatch/cleanup) renders every entry in
     both homes, pm first in both, at default (unqueryable) geometry --
     never a "+N more" marker when the whole roster fits. Window-4's
     pm-first assertion is checked twice: once at an adversarial clock
     instant that would defeat a soonest-first sort (`adversarial_now_bin`),
     and once against the live wall clock across a real 2-second gap
     (`test_next_cron_firings_pm_first_stable_across_real_time`) -- the
     literal reported regression scenario, with no clock stub involved.
  2. TestBandPacksIntoTwoRows -- a moderate, real pane width (neither
     unqueryable nor narrow enough to force the vertical fallback) packs
     the full roster into exactly the columnar band's two-row ceiling,
     every entry present, no overflow marker -- the direct behavioral
     exercise of the band's own row limit rather than its unconstrained
     (always-fits) or narrow (always-falls-back) edges.
  3. TestEighthEntryNeverSilentlyLost -- a synthetic 8th entry (EXTRA)
     either renders literally or is captured by the "+N more" marker;
     the shown-entry count plus the marker's count always reconciles to
     the full roster size.
  4. TestShortViewportStillNarratesOverflow -- a pane too narrow for the
     band, and then too short for the vertical fallback it drops to,
     still prints the first roster entry (pm) and the overflow marker,
     in both homes.
  5. TestRosterCountHelper / TestRosterCountDegradesToZero --
     pin `dashboard_schedule_roster_count()` (lib/dashboard_constants.sh)
     directly: 7 for the current roster, 8 with a synthetic 8th entry, and
     0 across all three ways it degrades to zero (an empty crontab, a
     missing cron_parser.py path, and `crontab -l` exiting non-zero). This
     helper backs create.sh's pane-height floors, not the band's own
     row-count ceiling (PGAI_ROSTER_BAND_MAX_ROWS), and is untouched by
     the band redesign.
  6. TestScheduleCreationFloors -- pin create.sh's roster-derived pane
     heights, HEADER_MIN_H (window 0's HEADER tile) and window 4's list
     band (STATUS_BAND_MIN_H and SCHED_LIST_H; the schedule window has no
     next-cron-firings.sh band, so no SCHED_FIRINGS_MIN_H), against the
     same roster count plus each renderer's own reserved-row constant
     (lib/dashboard_constants.sh),
     read via `create.sh --print-schedule-floors` -- never a hardcoded
     height. See that flag's own comment in create.sh for why the two
     floors are resolved once, immediately after SCHEDULE_ROSTER_COUNT,
     rather than duplicated at each pane's own resize block. These floors
     size for the vertical fallback's worst case and are untouched by the
     band redesign -- a real pane is always tall enough for a two-row
     band regardless.

RED-CASE VERIFICATION (recorded per roles/CODER.md, "exercise the
prevention once before DONE")
------------------------------------------------------------------------
Run against a tree without the roster-sizing and pane-viewport
behaviour these tests guard:
  - TestFullRosterRendersEveryRow::test_show_multi_header_renders_seven_entries_pm_first
    fails -- the hardcoded CRON_AGENTS array omits "overwatch" so only
    six entries ever render, however tall the pane.
  - Every TestShortViewportStillNarratesOverflow case fails in both
    homes -- when neither script queries pane height or emits a "+N
    more" marker, a short pane silently prints (and tmux clips) every
    row instead.
  - The constrained-height cases in TestEighthEntryNeverSilentlyLost fail
    for the same reason (no marker ever appears).
This was verified interactively (temporary `git worktree add --detach`
against that earlier commit, exercised, torn down) and is not itself
checked into the tree.

The roster BAND redesign on top of that (columnar band + width-triggered
vertical fallback, one shared renderer for both homes) was exercised
directly against the two renderer scripts before this module's own
assertions were updated to match, using the same crontab/tmux stubbing
technique this module automates: an unqueryable pane width packs the
full seven-entry roster onto one line with no marker; a moderate real
width (comfortably above the point where two entries fit per row, below
the point where the whole roster fits on one) packs it into exactly two
rows with no marker; a narrow real width (well below one entry's own
length) forces the vertical fallback and reproduces the pre-existing
"+N more" narration at the same counts the old vertical-only renderer
produced. This confirmed the guard (width-triggered fallback) actually
fires under a genuinely narrow pane and stays silent otherwise, before
this module's assertions were written to check the same behavior on
every run.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import stat
import subprocess
import time

import pytest

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location, matching every
# other dashboard test module (never the directory pytest happened to be
# launched from).
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_REPO_ROOT = _TEAM_DIR.parent
_DASHBOARD_DIR = _TEAM_DIR / "scripts" / "dashboard"
_NEXT_CRON_FIRINGS_SH = _DASHBOARD_DIR / "next-cron-firings.sh"
_SHOW_MULTI_SH = _DASHBOARD_DIR / "show-multi.sh"
_CREATE_SH = _DASHBOARD_DIR / "create.sh"
_DASHBOARD_CONSTANTS_SH = _TEAM_DIR / "scripts" / "lib" / "dashboard_constants.sh"
_CRON_PARSER_LIB = _TEAM_DIR / "pm-agent" / "lib" / "cron_parser.py"

# team/ itself is used as the synthetic --kanban-root: it already contains
# pm-agent/lib/cron_parser.py and pgai_agent_kanban/dashboard/cron_firings.py
# at exactly the relative paths both renderers probe, with no projects.cfg
# (so show-multi.sh takes its single-project delegation path to
# show-header.sh) and no HALT file -- never the operator's real kanban root.
_SYNTHETIC_KANBAN_ROOT = _TEAM_DIR

# The project name every renderer subprocess runs under. show-multi.sh's
# single-project delegation calls show-header.sh with no --project, and the
# dashboard data it reads resolves the project from PGAI_PROJECT_NAME alone,
# refusing when that is unset. _run() pins it, so the outcome never depends on
# whether the caller's shell exports one. The name is deliberately not
# registered under _SYNTHETIC_KANBAN_ROOT: no assertion here reads project
# state, only the schedule roster.
_SYNTHETIC_PROJECT_NAME = "roster-fixture"

# ---------------------------------------------------------------------------
# Synthetic crontab fixtures
# ---------------------------------------------------------------------------
# Sub-hourly rows fire every minute, staggered by --sleep=N exactly as
# templates/install/crontab-large.example does; weekly rows use a distinct
# day/hour so they render as a "Day Ham/pm" sentinel. Text order is the
# roster order both renderers must preserve -- pm first, then first-seen
# crontab order -- regardless of each row's own remaining time.
_FULL_ROSTER_CRONTAB = (
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm     --sleep=0\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder  --sleep=12\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=writer --sleep=24\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester --sleep=36\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm     --sleep=48\n"
    "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n"
    "0 4 * * 0 $KANBAN_ROOT/scripts/cleanup.sh\n"
)
_EIGHT_ROW_CRONTAB = _FULL_ROSTER_CRONTAB + (
    "0 5 * * 2 $KANBAN_ROOT/scripts/wake-batch.sh --agent=extra\n"
)

# Uppercase agent tokens, in fixed roster order (pm first, then crontab
# order), independent of each entry's own remaining time or which clock
# instant a run lands on. Both next-fire homes now render through the
# SAME shared entry-text builder (roster_entries.py, upper-casing every
# agent), so one order list covers both -- the two legacy names
# (_CRON_ROW_ORDER / _HEADER_ROW_ORDER) are kept as aliases rather than
# collapsed into one, so each call site still reads as "which home is
# this assertion about" without renaming every test.
_CRON_ROW_ORDER = ["PM", "CODER", "WRITER", "TESTER", "CM", "OVERWATCH", "CLEANUP"]
_HEADER_ROW_ORDER = _CRON_ROW_ORDER
_CRON_ROW_ORDER_EIGHT = _CRON_ROW_ORDER + ["EXTRA"]
_HEADER_ROW_ORDER_EIGHT = _HEADER_ROW_ORDER + ["EXTRA"]


def _fixed_now_epoch() -> int:
    """An epoch whose within-the-minute seconds component is 59.

    Computed from the live clock rather than hardcoded so the suite never
    depends on which epoch happened to be current when this file was
    written; `epoch % 60` is timezone-independent (real-world UTC offsets
    are whole minutes), so this is reproducible on any host. Used only for
    the presence/count-based assertions in this module -- never to verify
    window-4's row order (see `_adversarial_now_epoch`).
    """
    now = int(time.time())
    return now - (now % 60) + 59


def _adversarial_now_epoch() -> int:
    """An epoch whose within-the-minute seconds component (30) sits
    roughly midway between every `--sleep=N` stagger boundary
    (0/12/24/36/48) `_FULL_ROSTER_CRONTAB` uses.

    At second 30, each INT row's remaining time under the OLD soonest-first
    sort works out to (using `(offset - 30) % 60`): pm=30, coder=42,
    writer=54, tester=6, cm=18 -- an ascending order of
    [TESTER, CM, PM, CODER, WRITER], nothing like roster order
    [PM, CODER, WRITER, TESTER, CM]. A test that asserts roster order at
    this instant can only pass because the renderer no longer sorts by
    remaining time at all -- unlike `_fixed_now_epoch`'s second 59, which
    was chosen because it makes the two orderings coincide and therefore
    could never have caught a soonest-first reordering regression.
    """
    now = int(time.time())
    return now - (now % 60) + 30


# ---------------------------------------------------------------------------
# Test-only stub binaries: `date` (fixed "now") and `tmux` (fixed pane
# height). Neither shadows a binary this suite actually invokes for real --
# see the module docstring's "FIXTURE STRATEGY" on why no binary_guard is
# owed here.
# ---------------------------------------------------------------------------

_DATE_STUB_TEMPLATE = """\
#!/usr/bin/env bash
# Test-only stub (test_schedule_roster_completeness.py): answers `date +%s`
# with a fixed epoch (PGAI_TEST_FIXED_EPOCH) so the next-fire renderers
# compute a deterministic "now"; every other invocation form -- including
# plain `date` calls elsewhere in the same scripts -- passes straight
# through to the real binary.
if [[ "$1" == "+%s" ]] && [[ -n "${{PGAI_TEST_FIXED_EPOCH:-}}" ]]; then
  printf '%s\\n' "$PGAI_TEST_FIXED_EPOCH"
  exit 0
fi
exec "{real_date}" "$@"
"""

_TMUX_STUB = """\
#!/usr/bin/env bash
# Test-only stub (test_schedule_roster_completeness.py): answers a tmux
# pane-height OR pane-width query -- whichever format string the caller
# requested -- with a fixed value (PGAI_TEST_PANE_HEIGHT /
# PGAI_TEST_PANE_WIDTH respectively) so the renderers' viewport-fit logic
# (next_fire_pane_height / next_fire_pane_width / next_fire_fit_lines /
# roster_band_pack) can be exercised deterministically without a live
# tmux session. A dimension whose env var a test never set degrades to
# "unqueryable" (non-zero exit, same as a real query failing) rather than
# guessing -- the two dimensions are independent knobs. Any other tmux
# subcommand is refused -- this stub is never meant to stand in for a
# real session.
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


def _write_stub(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_now_stub(tmp_path: pathlib.Path, monkeypatch, epoch: int) -> int:
    """Prepend a `date` stub fixing `date +%s` to `epoch` and return it.

    Shared by `deterministic_now_bin` and `adversarial_now_bin` -- the two
    fixtures differ only in which epoch they fix, not in how the stub
    works.
    """
    real_date = shutil.which("date")
    if real_date is None:  # pragma: no cover -- every POSIX host has date
        pytest.skip("no 'date' binary resolvable on PATH")
    stub_dir = tmp_path / "_date_stub_bin"
    stub_dir.mkdir(exist_ok=True)
    _write_stub(stub_dir / "date", _DATE_STUB_TEMPLATE.format(real_date=real_date))
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PGAI_TEST_FIXED_EPOCH", str(epoch))
    return epoch


@pytest.fixture
def deterministic_now_bin(tmp_path: pathlib.Path, monkeypatch) -> int:
    """Fix `date +%s` to an epoch whose within-the-minute second is 59,
    and return that epoch.

    Every renderer under test resolves "now" via exactly one `date +%s`
    call; fixing it makes the "in M:SS" / "now" labels and marker/budget
    arithmetic reproducible instead of a race against the wall clock. Not
    used to verify window-4's row order -- see `adversarial_now_bin`.
    """
    return _install_now_stub(tmp_path, monkeypatch, _fixed_now_epoch())


@pytest.fixture
def adversarial_now_bin(tmp_path: pathlib.Path, monkeypatch) -> int:
    """Fix `date +%s` to an epoch chosen so a soonest-first sort would
    diverge sharply from roster order, and return that epoch.

    See `_adversarial_now_epoch` for the arithmetic. Used only by the
    window-4 roster-order assertion, which must hold at a clock instant
    that would defeat a soonest-first sort -- never at one tuned to agree
    with it.
    """
    return _install_now_stub(tmp_path, monkeypatch, _adversarial_now_epoch())


def _install_pane_stub(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Prepend the `tmux` stub and set TMUX_PANE so a subsequent
    `next_fire_pane_height` / `next_fire_pane_width` query in the renderer
    under test resolves through it rather than a real tmux binary. Shared
    by `pane_height_bin` and `pane_width_bin` -- pytest's fixture caching
    means a test requesting both only installs the stub once."""
    stub_dir = tmp_path / "_tmux_stub_bin"
    stub_dir.mkdir(exist_ok=True)
    _write_stub(stub_dir / "tmux", _TMUX_STUB)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("TMUX_PANE", "%1")


@pytest.fixture
def pane_height_bin(tmp_path: pathlib.Path, monkeypatch):
    """Install the tmux stub (if not already installed by pane_width_bin
    in the same test) and return a setter that fixes the pane HEIGHT the
    renderers' vertical-fallback fit will see. Leaving width unset (no
    pane_width_bin call) means width stays unqueryable -- the band
    renders unconstrained and this height setting is never consulted."""
    _install_pane_stub(tmp_path, monkeypatch)

    def _set_height(height: int) -> None:
        monkeypatch.setenv("PGAI_TEST_PANE_HEIGHT", str(height))

    return _set_height


@pytest.fixture
def pane_width_bin(tmp_path: pathlib.Path, monkeypatch):
    """Install the tmux stub (if not already installed by pane_height_bin
    in the same test) and return a setter that fixes the pane WIDTH the
    roster band's pack-or-fall-back decision will see. A narrow value
    (well below any single entry's own length) forces the vertical
    fallback; a moderate value can force the band to use its full
    two-row ceiling without falling back at all."""
    _install_pane_stub(tmp_path, monkeypatch)

    def _set_width(width: int) -> None:
        monkeypatch.setenv("PGAI_TEST_PANE_WIDTH", str(width))

    return _set_width


# ---------------------------------------------------------------------------
# Crontab install + subprocess-run helpers
# ---------------------------------------------------------------------------


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
        cwd=str(_REPO_ROOT),
        timeout=10,
    )
    assert result.returncode == 0, _diagnostic(result)


def _remove_crontab() -> None:
    """Remove any installed sandbox crontab via `crontab -r` (the stub's
    `-r` verb, still through team/tests/conftest.py's `_sandbox_crontab`).
    Leaves $HOME/.fake_crontab absent, which is what makes the stub's `-l`
    verb exit non-zero -- one way `dashboard_schedule_roster_count()`
    degrades to zero (`crontab -l` exiting non-zero), distinct from an
    installed-but-empty crontab (`crontab -l` exits 0 with empty
    output)."""
    result = subprocess.run(
        ["crontab", "-r"],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        cwd=str(_REPO_ROOT),
        timeout=10,
    )
    assert result.returncode == 0, _diagnostic(result)


def _run(args: list) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_SYNTHETIC_KANBAN_ROOT)
    env["PGAI_DEV_TREE_PATH"] = str(_REPO_ROOT)
    env["PGAI_PROJECT_NAME"] = _SYNTHETIC_PROJECT_NAME
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=30,
    )


def _run_next_cron_firings() -> subprocess.CompletedProcess:
    return _run(
        ["bash", str(_NEXT_CRON_FIRINGS_SH), "--kanban-root", str(_SYNTHETIC_KANBAN_ROOT)]
    )


def _run_show_multi_header() -> subprocess.CompletedProcess:
    return _run(
        [
            "bash",
            str(_SHOW_MULTI_SH),
            "--mode",
            "header",
            "--kanban-root",
            str(_SYNTHETIC_KANBAN_ROOT),
        ]
    )


def _run_roster_count(cron_parser_lib: "str | pathlib.Path") -> subprocess.CompletedProcess:
    """Source lib/dashboard_constants.sh and call
    dashboard_schedule_roster_count() directly with `cron_parser_lib` --
    exercised without any of create.sh's tmux/session machinery around
    it. `crontab` resolves
    through the same PATH-shadowing stub `_install_crontab` /
    `_remove_crontab` install (dict(os.environ) carries the monkeypatched
    PATH set by team/tests/conftest.py's autouse `_sandbox_crontab`)."""
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" && dashboard_schedule_roster_count "$2"',
            "_",
            str(_DASHBOARD_CONSTANTS_SH),
            str(cron_parser_lib),
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        cwd=str(_REPO_ROOT),
        timeout=10,
    )


def _reserved_rows_constants() -> "tuple[int, int]":
    """Read PGAI_SCHEDULE_HEADER_RESERVED_ROWS and
    PGAI_STATUS_BAND_RESERVED_ROWS directly from
    lib/dashboard_constants.sh -- never hardcoded here, so a future edit to
    either constant's value cannot silently desync this module's own
    expectation from the renderers' reserved-row accounting."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" && printf "%s %s" '
            '"$PGAI_SCHEDULE_HEADER_RESERVED_ROWS" '
            '"$PGAI_STATUS_BAND_RESERVED_ROWS"',
            "_",
            str(_DASHBOARD_CONSTANTS_SH),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, _diagnostic(result)
    header_reserved, status_reserved = result.stdout.split()
    return int(header_reserved), int(status_reserved)


def _run_print_schedule_floors() -> subprocess.CompletedProcess:
    """Run create.sh --print-schedule-floors -- the diagnostic dry-run
    that prints SCHEDULE_ROSTER_COUNT, HEADER_MIN_H, STATUS_BAND_MIN_H,
    SCHED_LIST_H and SCHED_EVENT_MIN_H and exits before any tmux session is
    created (see
    create.sh's own comment on the flag). Exercises the SAME single
    computation site the production resize blocks clamp up to -- deleting
    either floor line there is a live regression AC4 of this module's
    task pins by hand (bash team/scripts/run-unit-tests.sh must fail)."""
    return _run(
        [
            "bash",
            str(_CREATE_SH),
            "--print-schedule-floors",
            "--kanban-root",
            str(_SYNTHETIC_KANBAN_ROOT),
        ]
    )


def _parse_schedule_floors(stdout: str) -> "dict[str, int]":
    values: "dict[str, int]" = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        values[key.strip()] = int(raw_value.strip())
    return values


# ---------------------------------------------------------------------------
# Output-parsing helpers
# ---------------------------------------------------------------------------

_MARKER_RE = re.compile(r"\+(\d+)\s+more")


def _marker_count(stdout: str) -> "int | None":
    m = _MARKER_RE.search(stdout)
    return int(m.group(1)) if m else None


# An entry token is matched as a whole word anywhere in stdout -- never
# anchored to the start of a physical output line, because show-header.sh
# interleaves its right (roster) column after a left-column-width prefix
# ("Project: ... <padding> PM :00 (in 0:37)"), so a roster entry does not
# generally start its own printed line there even though it does in
# next-cron-firings.sh's undecorated pane. A word-boundary search works
# identically for a packed band row (several entries separated by
# PGAI_ROSTER_BAND_SEPARATOR), the vertical fallback (one entry per
# line), and the interleaved header column, because every entry is always
# preceded by whitespace (a separator, a newline, or column padding) and
# followed by whitespace, and no roster token is a substring of another.
# Both next-fire homes render identical entry text through the same
# shared renderer (lib/roster_band.sh / roster_entries.py), so one
# pattern covers both -- there is no longer a format difference between
# them to test separately.
def _entry_present(stdout: str, token: str) -> bool:
    """True when `token` (an upper-cased agent name) appears as a whole
    roster entry anywhere in `stdout`."""
    return re.search(rf"\b{re.escape(token)}\b", stdout) is not None


# Both next-fire homes render through the same shared renderer now, so
# presence detection is identical for either -- kept as two names (rather
# than collapsed into one) so each call site still reads as "which home
# is this assertion about".
_cron_row_present = _entry_present
_header_row_present = _entry_present


def _shown_cron_rows(stdout: str, roster: list) -> list:
    """Which of `roster`'s tokens rendered at all, in roster order.

    Presence-only: this is deliberately NOT positional, because the
    "shown count + dropped count reconciles" assertions only care whether
    an entry rendered, never where. Do not use this to assert on entry
    ORDER -- iterating over `roster` itself always returns its members in
    `roster`'s own order regardless of where (or whether, in what
    sequence) they actually appear in `stdout`, so it would pass a
    reordered rendering unnoticed. Use `_entry_render_order` for that.
    """
    return [tok for tok in roster if _entry_present(stdout, tok)]


def _shown_header_rows(stdout: str, roster: list) -> list:
    """Header-tile counterpart of `_shown_cron_rows`; same presence-only
    caveat applies -- use `_entry_render_order` to assert order."""
    return [agent for agent in roster if _entry_present(stdout, agent)]


def _entry_render_order(stdout: str, roster: list) -> list:
    """The roster tokens actually present in `stdout`, in the SEQUENCE
    they render (scanning the whole output left-to-right, top-to-bottom)
    -- unlike `_shown_cron_rows` / `_shown_header_rows`, which return
    membership in `roster`'s own fixed order and therefore cannot detect
    a reordered rendering. Works for a packed band row (several entries
    per line) exactly as it does for the vertical fallback (one per
    line), by finding each token's own first-match position rather than
    assuming one entry starts each line."""
    positions = []
    for tok in roster:
        m = re.search(rf"\b{re.escape(tok)}\b", stdout)
        if m is not None:
            positions.append((m.start(), tok))
    positions.sort(key=lambda pair: pair[0])
    return [tok for _, tok in positions]


# Kept as two names for the same reason as _cron_row_present /
# _header_row_present above -- same implementation, since both homes now
# share one renderer.
_cron_row_render_order = _entry_render_order
_header_row_render_order = _entry_render_order


# ---------------------------------------------------------------------------
# AC1 -- the full current roster renders every row, pm first, in both homes.
#
# The "pm first" tests below assert on `_cron_row_render_order` /
# `_header_row_render_order` (actual top-to-bottom rendering sequence), never
# `_shown_cron_rows` / `_shown_header_rows` (membership only, always returned
# in `roster`'s own fixed order regardless of what stdout actually contains).
# Asserting membership against a list already in the expected order cannot
# distinguish the correct rendering from any other permutation that happens
# to include the same seven names -- see this module's git history for the
# reordering that slipped past exactly that shape of assertion.
# ---------------------------------------------------------------------------


class TestFullRosterRendersEveryRow:
    def test_next_cron_firings_renders_seven_rows_pm_first(
        self, adversarial_now_bin
    ) -> None:
        """Roster order holds at a clock instant a soonest-first sort
        would render as [TESTER, CM, PM, CODER, WRITER] (see
        `_adversarial_now_epoch`) -- this only passes because the
        renderer no longer sorts by remaining time, not because the
        instant happens to favor pm."""
        _install_crontab(_FULL_ROSTER_CRONTAB)
        result = _run_next_cron_firings()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        rendered = _cron_row_render_order(stdout, _CRON_ROW_ORDER)
        assert rendered == _CRON_ROW_ORDER, (
            f"expected all seven rows to RENDER in fixed roster order "
            f"{_CRON_ROW_ORDER}, got {rendered}\n{_diagnostic(result)}"
        )
        assert _marker_count(stdout) is None, (
            f"no overflow marker expected when every row fits\n{_diagnostic(result)}"
        )

    def test_next_cron_firings_pm_first_stable_across_real_time(self) -> None:
        """The direct behavioral regression test: no clock stub at all
        -- two runs against the live wall clock, a couple of
        real seconds apart, must produce the identical roster order. A
        soonest-first sort visibly reorders across a gap like this on
        `_FULL_ROSTER_CRONTAB`'s data (that was the bug); fixed roster
        order does not move regardless of when either run happens to
        land within the minute."""
        _install_crontab(_FULL_ROSTER_CRONTAB)

        first = _run_next_cron_firings()
        assert first.returncode == 0, _diagnostic(first)
        time.sleep(2)
        second = _run_next_cron_firings()
        assert second.returncode == 0, _diagnostic(second)

        rendered_first = _cron_row_render_order(first.stdout, _CRON_ROW_ORDER)
        rendered_second = _cron_row_render_order(second.stdout, _CRON_ROW_ORDER)
        assert rendered_first == _CRON_ROW_ORDER, (
            f"expected all seven rows to RENDER in fixed roster order "
            f"{_CRON_ROW_ORDER} on the first run, got {rendered_first}"
            f"\n{_diagnostic(first)}"
        )
        assert rendered_second == _CRON_ROW_ORDER, (
            f"expected all seven rows to RENDER in fixed roster order "
            f"{_CRON_ROW_ORDER} on the second run (2s later), got "
            f"{rendered_second}\n{_diagnostic(second)}"
        )
        assert rendered_first == rendered_second, (
            f"row order changed between two runs 2s apart: "
            f"{rendered_first} -> {rendered_second}\nfirst run:\n"
            f"{_diagnostic(first)}\nsecond run:\n{_diagnostic(second)}"
        )

    def test_show_multi_header_renders_seven_entries_pm_first(
        self, deterministic_now_bin
    ) -> None:
        _install_crontab(_FULL_ROSTER_CRONTAB)
        result = _run_show_multi_header()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        rendered = _header_row_render_order(stdout, _HEADER_ROW_ORDER)
        assert rendered == _HEADER_ROW_ORDER, (
            f"expected all seven roster entries to RENDER in crontab order "
            f"{_HEADER_ROW_ORDER}, got {rendered}\n{_diagnostic(result)}"
        )
        assert _marker_count(stdout) is None, (
            f"no overflow marker expected when every row fits\n{_diagnostic(result)}"
        )


# ---------------------------------------------------------------------------
# AC1b -- a real, moderate pane width (neither unqueryable nor narrow
# enough to force the vertical fallback) packs the full seven-entry
# roster into the band's own two-row ceiling, in both homes, still with
# no overflow marker. This is the direct behavioral exercise of "at most
# two rows" -- the unconstrained case above always collapses to one row
# regardless of that ceiling, so it alone cannot show the ceiling is
# actually two and not, say, unbounded.
#
# 80 columns is chosen with margin on both sides against the empirically
# observed band/vertical-fallback boundary for this fixture's fixed-width
# entry text (~77 columns in this module's own host locale): comfortably
# above it (so the band never falls back here) and comfortably below the
# roster's fully unconstrained single-row width (~140+ columns), so the
# packer must use both of its two rows rather than collapsing to one.
# ---------------------------------------------------------------------------
_MODERATE_BAND_WIDTH = 80


class TestBandPacksIntoTwoRows:
    def test_next_cron_firings_moderate_width_uses_two_rows_no_marker(
        self, deterministic_now_bin, pane_width_bin
    ) -> None:
        pane_width_bin(_MODERATE_BAND_WIDTH)
        _install_crontab(_FULL_ROSTER_CRONTAB)
        result = _run_next_cron_firings()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        shown = _shown_cron_rows(stdout, _CRON_ROW_ORDER)
        assert shown == _CRON_ROW_ORDER, (
            f"expected every roster entry to render at a moderate pane width; "
            f"got {shown}\n{_diagnostic(result)}"
        )
        assert _marker_count(stdout) is None, (
            f"a roster that fits within the band's row ceiling must never show "
            f"an overflow marker\n{_diagnostic(result)}"
        )
        roster_line_count = sum(
            1 for line in stdout.splitlines() if line.strip() and "===" not in line
        )
        assert roster_line_count == 2, (
            f"expected the band to use exactly two rows at this width; got "
            f"{roster_line_count} non-empty roster line(s)\n{_diagnostic(result)}"
        )

    def test_show_multi_header_moderate_width_uses_two_rows_no_marker(
        self, deterministic_now_bin, pane_width_bin
    ) -> None:
        pane_width_bin(_MODERATE_BAND_WIDTH)
        _install_crontab(_FULL_ROSTER_CRONTAB)
        result = _run_show_multi_header()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        shown = _shown_header_rows(stdout, _HEADER_ROW_ORDER)
        assert shown == _HEADER_ROW_ORDER, (
            f"expected every roster entry to render at a moderate pane width; "
            f"got {shown}\n{_diagnostic(result)}"
        )
        assert _marker_count(stdout) is None, (
            f"a roster that fits within the band's row ceiling must never show "
            f"an overflow marker\n{_diagnostic(result)}"
        )


# ---------------------------------------------------------------------------
# AC2 -- a synthetic 8th entry either renders or is captured by the
# overflow marker; the shown-row count and the marker always reconcile to
# the full roster size, so nothing vanishes without narration.
# ---------------------------------------------------------------------------


class TestEighthEntryNeverSilentlyLost:
    def test_next_cron_firings_eighth_entry_rendered_when_it_fits(
        self, deterministic_now_bin
    ) -> None:
        _install_crontab(_EIGHT_ROW_CRONTAB)
        result = _run_next_cron_firings()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        shown = _shown_cron_rows(stdout, _CRON_ROW_ORDER_EIGHT)
        assert shown == _CRON_ROW_ORDER_EIGHT, (
            f"a pane with no queryable height must render every row untruncated, "
            f"including the synthetic 8th entry; got {shown}\n{_diagnostic(result)}"
        )
        assert _marker_count(stdout) is None, _diagnostic(result)

    def test_next_cron_firings_eighth_entry_narrated_when_it_does_not_fit(
        self, deterministic_now_bin, pane_height_bin, pane_width_bin
    ) -> None:
        _install_crontab(_EIGHT_ROW_CRONTAB)
        # Narrow width forces the vertical fallback (the band cannot pack
        # eight ~15-20-column entries into two rows at 20 columns); the
        # height budget then governs how many of the fallback's rows fit.
        pane_width_bin(20)
        pane_height_bin(6)  # budget=4 -> show=3, dropped=5
        result = _run_next_cron_firings()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        shown = _shown_cron_rows(stdout, _CRON_ROW_ORDER_EIGHT)
        dropped = _marker_count(stdout)
        assert dropped is not None, (
            f"a roster too tall for the pane must narrate the drop with a "
            f"'+N more' marker\n{_diagnostic(result)}"
        )
        assert "EXTRA" not in shown, (
            f"this scenario is only meaningful if EXTRA was actually dropped "
            f"(not coincidentally rendered): shown={shown}\n{_diagnostic(result)}"
        )
        assert len(shown) + dropped == len(_CRON_ROW_ORDER_EIGHT), (
            f"shown rows ({len(shown)}) + marker count ({dropped}) must equal the "
            f"full roster ({len(_CRON_ROW_ORDER_EIGHT)}) -- every row is either "
            f"visible or counted, never silently lost\n{_diagnostic(result)}"
        )

    def test_show_multi_header_eighth_entry_rendered_when_it_fits(
        self, deterministic_now_bin
    ) -> None:
        _install_crontab(_EIGHT_ROW_CRONTAB)
        result = _run_show_multi_header()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        shown = _shown_header_rows(stdout, _HEADER_ROW_ORDER_EIGHT)
        assert shown == _HEADER_ROW_ORDER_EIGHT, (
            f"a pane with no queryable height must render every entry untruncated, "
            f"including the synthetic 8th entry; got {shown}\n{_diagnostic(result)}"
        )
        assert _marker_count(stdout) is None, _diagnostic(result)

    def test_show_multi_header_eighth_entry_narrated_when_it_does_not_fit(
        self, deterministic_now_bin, pane_height_bin, pane_width_bin
    ) -> None:
        _install_crontab(_EIGHT_ROW_CRONTAB)
        # Narrow width forces the vertical fallback; the height budget then
        # governs how many of the fallback's rows fit.
        pane_width_bin(20)
        pane_height_bin(7)  # RESERVED_ROWS=3 -> budget=4 -> show=3, dropped=5
        result = _run_show_multi_header()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        shown = _shown_header_rows(stdout, _HEADER_ROW_ORDER_EIGHT)
        dropped = _marker_count(stdout)
        assert dropped is not None, (
            f"a roster too tall for the pane must narrate the drop with a "
            f"'+N more' marker\n{_diagnostic(result)}"
        )
        assert "EXTRA" not in shown, (
            f"this scenario is only meaningful if EXTRA was actually dropped "
            f"(not coincidentally rendered): shown={shown}\n{_diagnostic(result)}"
        )
        assert len(shown) + dropped == len(_HEADER_ROW_ORDER_EIGHT), (
            f"shown rows ({len(shown)}) + marker count ({dropped}) must equal the "
            f"full roster ({len(_HEADER_ROW_ORDER_EIGHT)}) -- every entry is either "
            f"visible or counted, never silently lost\n{_diagnostic(result)}"
        )


# ---------------------------------------------------------------------------
# AC3 -- a viewport too short for the roster still prints the first roster
# row and the overflow marker, in both homes.
# ---------------------------------------------------------------------------


class TestShortViewportStillNarratesOverflow:
    def test_next_cron_firings_short_pane_shows_first_row_and_marker(
        self, deterministic_now_bin, pane_height_bin, pane_width_bin
    ) -> None:
        _install_crontab(_FULL_ROSTER_CRONTAB)
        # Narrow width forces the vertical fallback; the height budget then
        # narrates the overflow within it.
        pane_width_bin(20)
        pane_height_bin(4)  # RESERVED_ROWS=2 -> budget=2 -> show=1, dropped=6
        result = _run_next_cron_firings()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        assert _cron_row_present(stdout, "PM"), (
            f"the first roster row (pm) must still print even when the pane "
            f"cannot hold the whole roster\n{_diagnostic(result)}"
        )
        shown = _shown_cron_rows(stdout, _CRON_ROW_ORDER)
        assert shown == ["PM"], f"expected exactly one shown row (PM); got {shown}"
        assert _marker_count(stdout) == 6, (
            f"expected '+6 more' (7-row roster, 1 shown)\n{_diagnostic(result)}"
        )

    def test_show_multi_header_short_pane_shows_first_row_and_marker(
        self, deterministic_now_bin, pane_height_bin, pane_width_bin
    ) -> None:
        _install_crontab(_FULL_ROSTER_CRONTAB)
        # Narrow width forces the vertical fallback; the height budget then
        # narrates the overflow within it.
        pane_width_bin(20)
        pane_height_bin(5)  # RESERVED_ROWS=3 -> budget=2 -> show=1, dropped=6
        result = _run_show_multi_header()
        assert result.returncode == 0, _diagnostic(result)
        stdout = result.stdout

        assert _header_row_present(stdout, "PM"), (
            f"the first roster row (pm) must still print even when the pane "
            f"cannot hold the whole roster\n{_diagnostic(result)}"
        )
        shown = _shown_header_rows(stdout, _HEADER_ROW_ORDER)
        assert shown == ["PM"], f"expected exactly one shown entry (PM); got {shown}"
        assert _marker_count(stdout) == 6, (
            f"expected '+6 more' (7-entry roster, 1 shown)\n{_diagnostic(result)}"
        )


# ---------------------------------------------------------------------------
# dashboard_schedule_roster_count() itself (lib/dashboard_constants.sh),
# exercised directly rather than through either renderer.
# ---------------------------------------------------------------------------


class TestRosterCountHelper:
    def test_full_roster_counts_seven(self) -> None:
        _install_crontab(_FULL_ROSTER_CRONTAB)
        result = _run_roster_count(_CRON_PARSER_LIB)
        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "7", _diagnostic(result)

    def test_synthetic_eighth_entry_counts_eight(self) -> None:
        _install_crontab(_EIGHT_ROW_CRONTAB)
        result = _run_roster_count(_CRON_PARSER_LIB)
        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "8", _diagnostic(result)


class TestRosterCountDegradesToZero:
    """The three ways dashboard_schedule_roster_count() degrades to 0 --
    each collapses to the helper's own
    `[[ -z "$crontab_text" ]] || [[ ! -f "$cron_parser_lib" ]] ||
    ! command -v python3` guard, but each is reachable by a distinct,
    independently-plausible operational failure, so each gets its own test
    rather than one test exercising only one of the three."""

    def test_empty_crontab_degrades_to_zero(self) -> None:
        """An installed-but-empty crontab: `crontab -l` exits 0 with empty
        output -- distinct from `_missing_crontab_degrades_to_zero` below,
        where `crontab -l` itself exits non-zero."""
        _install_crontab("")
        result = _run_roster_count(_CRON_PARSER_LIB)
        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "0", _diagnostic(result)

    def test_missing_cron_parser_lib_degrades_to_zero(self) -> None:
        """A full, valid roster crontab, but the cron_parser.py path the
        caller hands in does not exist."""
        _install_crontab(_FULL_ROSTER_CRONTAB)
        missing_lib = _CRON_PARSER_LIB.parent / "cron_parser_missing_for_test.py"
        assert not missing_lib.exists(), (
            f"fixture setup error: {missing_lib} unexpectedly exists"
        )
        result = _run_roster_count(missing_lib)
        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "0", _diagnostic(result)

    def test_crontab_command_failure_degrades_to_zero(self) -> None:
        """No crontab installed at all: the sandbox stub's `-l` verb exits
        1 (team/tests/conftest.py's `_CRONTAB_STUB`), which is the
        `crontab -l` failure path -- not the same code path as an
        installed-but-empty crontab (`test_empty_crontab_degrades_to_zero`
        above), even though both end at the same `-z` check."""
        _remove_crontab()
        result = _run_roster_count(_CRON_PARSER_LIB)
        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "0", _diagnostic(result)


# ---------------------------------------------------------------------------
# The roster-derived pane heights in create.sh -- HEADER_MIN_H for window
# 0's HEADER tile, and STATUS_BAND_MIN_H / SCHED_LIST_H for window 4's list
# band -- read via the --print-schedule-floors diagnostic dry-run and
# asserted against roster count + each renderer's own reserved-row
# constant -- never a hardcoded height (see this module's docstring, item
# 6, and create.sh's own comment on the flag).
# ---------------------------------------------------------------------------


def _assert_floors_track_roster(result: subprocess.CompletedProcess, expected_roster: int) -> None:
    header_reserved, status_reserved = _reserved_rows_constants()
    floors = _parse_schedule_floors(result.stdout)

    roster_count = floors["SCHEDULE_ROSTER_COUNT"]
    assert roster_count == expected_roster, _diagnostic(result)
    assert floors["HEADER_MIN_H"] == roster_count + header_reserved, (
        f"HEADER_MIN_H ({floors['HEADER_MIN_H']}) must equal roster count "
        f"({roster_count}) + PGAI_SCHEDULE_HEADER_RESERVED_ROWS "
        f"({header_reserved})\n{_diagnostic(result)}"
    )
    assert floors["STATUS_BAND_MIN_H"] == roster_count + status_reserved, _diagnostic(result)
    # The list band: header + one row per agent + one row of headroom.
    assert floors["SCHED_LIST_H"] == roster_count + status_reserved + 1, _diagnostic(result)
    assert floors["SCHED_EVENT_MIN_H"] >= 5, _diagnostic(result)
    assert "SCHED_FIRINGS_MIN_H" not in floors, (
        "the schedule window has two bands; no next-cron-firings.sh floor "
        f"should be printed\n{_diagnostic(result)}"
    )


class TestScheduleCreationFloors:
    def test_floors_match_roster_count_plus_reserved_rows(self) -> None:
        _install_crontab(_FULL_ROSTER_CRONTAB)

        result = _run_print_schedule_floors()
        assert result.returncode == 0, _diagnostic(result)
        _assert_floors_track_roster(result, 7)

    def test_floors_track_a_synthetic_eighth_roster_entry(self) -> None:
        _install_crontab(_EIGHT_ROW_CRONTAB)

        result = _run_print_schedule_floors()
        assert result.returncode == 0, _diagnostic(result)
        _assert_floors_track_roster(result, 8)

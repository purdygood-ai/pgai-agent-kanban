"""
test_blocked_column_metrics_pipeline.py
========================================
End-to-end integration coverage for the rc-metrics Blocked column, driven
through every production component in the chain rather than a test double
for any one of them:

  1. the blocked-interval ledger writer/reader
     (team/pgai_agent_kanban/metrics/blocked_ledger.py) -- the same
     append_block_start/append_block_end functions the status-writer
     calls, and the same blocked_minutes() reader every consumer of the
     ledger goes through,
  2. the per-RC rollup aggregator (team/scripts/lib/metrics_aggregator.py)
     -- aggregate_rc(), which reads the ledger and clamps/merges its own
     window,
  3. the history.csv writer (team/scripts/lib/metrics_csv_writer.py) --
     append_rc_row(), which degrades a missing blocked_minutes value to an
     empty cell exactly as it would for a rollup written before the column
     existed,
  4. the production renderer (team/scripts/dashboard/show-metrics.sh) --
     the same script window 7's history pane and the standalone rc-metrics
     CLI both invoke.

No seam reimplements the merge, the clamp, or the comma/dash/zero
formatting under test; each of those is exercised by calling the real
function or script.

THE FIXTURE IS GITLESS
-----------------------
No `git init` (or any other git subcommand) is ever invoked by this
module, and the fixture kanban root is never made into a git repository.
Every component exercised here -- the ledger, the aggregator's blocked-
minutes read, the CSV writer, and the renderer -- reads and writes plain
files under the fixture root; none of the four requires a repository to
function, matching the requirement's Host Matrix ("gitless install --
ledger is plain files, no git").

One pre-existing, unrelated aggregator code path -- _detect_outcome()'s
opportunistic `git tag -l` probe for the RC's shipped/cancelled/unknown
outcome field -- is not part of the Blocked column and is not read or
asserted on by any test below. It already degrades cleanly with no repo
present (the exact shape team/tests/unit/test_metrics_aggregator.py's own
tmp_path-based aggregate_rc() calls already exercise, unmodified, in this
same tree). Neutralizing it further is not covered here, since doing so
would require changing aggregator code the Blocked column never touches.

SCENARIO
--------
One fixture project carries three history rows, each produced through a
different slice of the same pipeline:

  - v9.9.1: a 40-minute HALT interval and a 20-minute BLOCKED-task
    interval overlapping it by 10 minutes -> merged Blocked = 50 minutes,
    Wall unaffected.
  - v9.9.2: an RC with the identical opened/closed window as v9.9.1 but no
    boundaries recorded in the ledger for its own version -- the ledger
    module's own contract treats "no boundaries for this version" and "no
    ledger file at all" as the same zero-blocked outcome (see
    blocked_ledger.py's module docstring, "Read path", point 4) -> Blocked
    = 0, and Wall equal to v9.9.1's own Wall, proving the column never
    perturbs it.
  - v9.8.0: a "pre-era" row written the way a rollup from before this
    column existed would look -- no blocked_minutes key in the rollup
    dict at all -- so the CSV writer's own missing-value degradation path
    runs for real, rather than a hand-authored CSV row standing in for it.

Test names describe the behavior under test; no bug ID, task ID, or
release version appears in a function name (roles/TESTER.md Anti-pattern
6) -- the vX.Y.Z strings above are fixture DATA, not identifiers.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location, each tree finds
# itself (roles/CODER.md "Each tree finds itself").
#
# File structure (relative to dev-tree root):
#   team/tests/integration/test_blocked_column_metrics_pipeline.py
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_SHOW_METRICS_SH = _TEAM_DIR / "scripts" / "dashboard" / "show-metrics.sh"

for _p in (str(_LIB_DIR), str(_TEAM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metrics_aggregator as ma  # noqa: E402
import metrics_csv_writer as csvw  # noqa: E402
from pgai_agent_kanban.metrics.blocked_ledger import (  # noqa: E402
    append_block_end,
    append_block_start,
    ledger_path_for_project,
)

_TIMEOUT_SECONDS = 30
_PROJECT = "fixture-project"

# The two synthetic block sources for v9.9.1's overlap scenario.
_HALT_START = "2026-01-01T00:10:00+00:00"
_HALT_END = "2026-01-01T00:50:00+00:00"
_TASK_BLOCK_START = "2026-01-01T00:40:00+00:00"
_TASK_BLOCK_END = "2026-01-01T01:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixture: a gitless kanban root with one registered project
# ---------------------------------------------------------------------------


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal, gitless kanban root: projects.cfg plus an empty project
    directory. Individual tests populate release-state/, metrics/, and the
    ledger themselves. No git command runs against this tree at any point
    in this module."""
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n",
        encoding="utf-8",
    )
    project_dir = root / "projects" / _PROJECT
    project_dir.mkdir(parents=True)
    return root


def _clean_subprocess_env(root: pathlib.Path) -> dict[str, str]:
    """A subprocess environment isolated from ambient project identity and
    pointed at the fixture kanban root -- the same seam
    test_blocked_column_dashboard.py already uses for show-metrics.sh."""
    env = dict(os.environ)
    for key in (
        "PGAI_PROJECT_NAME",
        "PGAI_DEV_TREE_PATH",
        "PGAI_PROJECT_ROOT",
        "PGAI_AGENT_KANBAN_ROOT_PATH",
    ):
        env.pop(key, None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def _write_release_state(
    root: pathlib.Path, version: str, opened_at: str, closed_at: str
) -> None:
    """Write projects/<project>/release-state/<version>.json -- the same
    file aggregate_rc() reads for its opened_at/closed_at window, the
    window the ledger's blocked_minutes() reader clamps every interval
    to."""
    state_dir = root / "projects" / _PROJECT / "release-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{version}.json").write_text(
        json.dumps(
            {"opened_at": opened_at, "closed_at": closed_at, "outcome": "shipped"}
        ),
        encoding="utf-8",
    )


def _run_show_metrics(
    history_csv: pathlib.Path, root: pathlib.Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash",
            str(_SHOW_METRICS_SH),
            "--history-csv",
            str(history_csv),
            "--project",
            _PROJECT,
            "--no-color",
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        env=_clean_subprocess_env(root),
    )


def _row_cells(stdout: str, rc: str) -> list[str]:
    """Return the whitespace-split cells of the rendered row starting with
    *rc*. Column order (see show-metrics.sh's PY_HISTORY heredoc): RC,
    Wall, Blocked, Input, Output, Cache Read, Cache Write, Tasks -- of
    which "Cache Read" and "Cache Write" are two header words but always a
    single data cell each, so index 0 is RC and index 1 is Wall and index
    2 is Blocked for every data row regardless of header wording."""
    line = next(l for l in stdout.splitlines() if l.startswith(rc))
    return line.split()


# ---------------------------------------------------------------------------
# The full pipeline: ledger -> aggregator -> history.csv -> renderer
# ---------------------------------------------------------------------------


class TestBlockedColumnPipelineOnGitlessInstall:
    def test_overlapping_halt_and_blocked_task_intervals_merge_to_fifty_minutes(
        self, kanban_root: pathlib.Path
    ) -> None:
        """A 40-minute HALT interval and a 20-minute BLOCKED-task interval
        overlapping it by 10 minutes merge to 50 blocked minutes end to
        end -- ledger write, aggregator read, CSV persistence, and
        rendered table cell all agree -- while Wall renders the RC's own
        full open-to-ship window, untouched by the blocked total."""
        version = "v9.9.1"
        opened_at = "2026-01-01T00:00:00+00:00"
        closed_at = "2026-01-01T02:00:00+00:00"  # 120-minute window
        _write_release_state(kanban_root, version, opened_at, closed_at)

        ledger_path = ledger_path_for_project(str(kanban_root), _PROJECT)
        append_block_start(ledger_path, version, "halt:project", _HALT_START)
        append_block_end(ledger_path, version, "halt:project", _HALT_END)
        append_block_start(ledger_path, version, "task:CODER-FIXTURE-001", _TASK_BLOCK_START)
        append_block_end(ledger_path, version, "task:CODER-FIXTURE-001", _TASK_BLOCK_END)

        rollup_path = ma.aggregate_rc(_PROJECT, version, kanban_root=kanban_root)
        rollup = json.loads(rollup_path.read_text(encoding="utf-8"))
        assert rollup["blocked_minutes"] == 50
        assert rollup["wall_time_minutes"] == 120.0

        history_csv = kanban_root / "projects" / _PROJECT / "metrics" / "history.csv"
        assert csvw.append_rc_row(history_csv, rollup) is True

        result = _run_show_metrics(history_csv, kanban_root)
        assert result.returncode == 0, result.stderr
        cells = _row_cells(result.stdout, version)
        assert cells[1] == "120.0"
        assert cells[2] == "50"

    def test_rc_with_no_recorded_boundaries_renders_zero_and_matching_wall(
        self, kanban_root: pathlib.Path
    ) -> None:
        """An RC sharing the identical window length as the overlap
        scenario above, but with no boundaries recorded in the ledger for
        its own version, renders a real 0 -- never blank -- and the same
        Wall value, proving the Blocked column never perturbs Wall.

        blocked_ledger.py's own module docstring ("Read path", point 4)
        states that an absent ledger file and a ledger with no boundaries
        for a given version are the same zero-blocked outcome; this test
        exercises the latter shape by reusing the project's own ledger
        file (already populated by the previous scenario's version) for a
        second version that never appears in it.
        """
        version = "v9.9.2"
        opened_at = "2026-02-01T00:00:00+00:00"
        closed_at = "2026-02-01T02:00:00+00:00"  # same 120-minute window
        _write_release_state(kanban_root, version, opened_at, closed_at)

        # Populate the shared per-project ledger with boundaries for a
        # DIFFERENT version only, so this version's own read is genuinely
        # "no boundaries recorded for *version*" rather than "no ledger
        # file at all" -- the stronger form of the same contract.
        ledger_path = ledger_path_for_project(str(kanban_root), _PROJECT)
        append_block_start(ledger_path, "v9.9.1", "halt:project", _HALT_START)
        append_block_end(ledger_path, "v9.9.1", "halt:project", _HALT_END)

        rollup_path = ma.aggregate_rc(_PROJECT, version, kanban_root=kanban_root)
        rollup = json.loads(rollup_path.read_text(encoding="utf-8"))
        assert rollup["blocked_minutes"] == 0
        assert rollup["wall_time_minutes"] == 120.0

        history_csv = kanban_root / "projects" / _PROJECT / "metrics" / "history.csv"
        assert csvw.append_rc_row(history_csv, rollup) is True

        result = _run_show_metrics(history_csv, kanban_root)
        assert result.returncode == 0, result.stderr
        cells = _row_cells(result.stdout, version)
        assert cells[1] == "120.0"
        assert cells[2] == "0"

    def test_pre_era_row_and_blocked_row_share_one_footer_line(
        self, kanban_root: pathlib.Path
    ) -> None:
        """A row written the way a pre-v2.24.0 rollup would look -- no
        blocked_minutes key in the rollup dict at all, exactly what an
        older aggregator produced -- renders a single dash through the
        CSV writer's real missing-value path (not a hand-authored CSV
        row), and the rendered table prints its one footer line exactly
        once even though a same-run blocked row is present alongside it.
        """
        history_csv = kanban_root / "projects" / _PROJECT / "metrics" / "history.csv"

        blocked_version = "v9.9.1"
        opened_at = "2026-01-01T00:00:00+00:00"
        closed_at = "2026-01-01T02:00:00+00:00"
        _write_release_state(kanban_root, blocked_version, opened_at, closed_at)
        ledger_path = ledger_path_for_project(str(kanban_root), _PROJECT)
        append_block_start(ledger_path, blocked_version, "halt:project", _HALT_START)
        append_block_end(ledger_path, blocked_version, "halt:project", _HALT_END)
        append_block_start(
            ledger_path, blocked_version, "task:CODER-FIXTURE-001", _TASK_BLOCK_START
        )
        append_block_end(ledger_path, blocked_version, "task:CODER-FIXTURE-001", _TASK_BLOCK_END)
        blocked_rollup_path = ma.aggregate_rc(
            _PROJECT, blocked_version, kanban_root=kanban_root
        )
        blocked_rollup = json.loads(blocked_rollup_path.read_text(encoding="utf-8"))
        assert csvw.append_rc_row(history_csv, blocked_rollup) is True

        pre_era_rollup = {
            "rc": "v9.8.0",
            "project": _PROJECT,
            "workflow_type": "release",
            "wall_time_minutes": 45,
            "outcome": "SHIPPED",
            # No "blocked_minutes" key at all -- the pre-column rollup shape.
        }
        assert "blocked_minutes" not in pre_era_rollup
        assert csvw.append_rc_row(history_csv, pre_era_rollup) is True

        result = _run_show_metrics(history_csv, kanban_root)
        assert result.returncode == 0, result.stderr

        blocked_cells = _row_cells(result.stdout, blocked_version)
        assert blocked_cells[2] == "50"

        pre_era_cells = _row_cells(result.stdout, "v9.8.0")
        assert pre_era_cells[2] == "-"

        footer_lines = [
            line
            for line in result.stdout.splitlines()
            if "Blocked tracked from v2.24.0" in line
        ]
        assert len(footer_lines) == 1, (
            f"expected exactly one footer line across both rows, got "
            f"{len(footer_lines)}: {footer_lines!r}"
        )

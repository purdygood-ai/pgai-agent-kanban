"""
test_metrics_totals_across_archive.py
=====================================
Token-cost and metrics totals do not change when finished tasks are moved
into the project's archive. Both roll-up writers --
``team/pm-agent/aggregate_tokens.py`` (read by ``cost-report.sh``) and
``team/scripts/lib/metrics_aggregator.py`` (read by ``metrics-report.sh``)
-- walk the project's live task folders together with its archived ones
(``archive/<project>/tasks/<ID>/``), so a roll-up rebuilt after an archive
run counts every task it counted before.

Covered, every fixture in a sandbox kanban root under pytest's tmp_path
(``tests/fixtures/archive_sandbox.py``'s acceptance scenario, each task
given an ``artifacts/tokens.json``), archived through the real
``archive.plan`` / ``archive.apply``:

  * ``archive.lookup.archived_task_dirs``: archived task folders with no
    live folder, in ID order; never the archive's ``tasks/queues/``, never a
    task that is also live, nothing for a directory outside the
    live-project shape or a project with no archive.
  * ``archive.lookup.task_dirs_with_archived``: live and archived folders
    merged in ID order, the live ``queues/`` left out.
  * Both aggregators' ``--all`` roll-ups, rebuilt from scratch after the
    archive run, carry the same content as before it (the RC roll-up's
    wall-clock ``shipped_at`` stamp aside).
  * ``cost-report.sh`` (real script, subprocess, roll-ups built on demand):
    the ``--rc`` CSV, the ``--month`` report and the ``--day`` CSV are
    identical before and after archiving.
  * ``metrics-report.sh`` (real script, subprocess, roll-ups built on
    demand): the ``--rc`` and ``--day`` JSON, and the ``--csv`` history
    dump, are identical before and after archiving.

Nothing here reads or writes the live install: each script runs from a
foreign cwd with an environment built from an explicit set
(``build_scrubbed_env``) whose every kanban root names the sandbox.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from pgai_agent_kanban.archive import RESULT_OK, apply, plan
from pgai_agent_kanban.archive.layout import archive_layout
from pgai_agent_kanban.archive.lookup import archived_task_dirs, task_dirs_with_archived
from tests.fixtures.archive_sandbox import ArchiveSandbox, build_acceptance_scenario
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_COST_REPORT_SH = _TEAM_DIR / "scripts" / "cost-report.sh"
_METRICS_REPORT_SH = _TEAM_DIR / "scripts" / "metrics-report.sh"
_TOKEN_AGGREGATOR = _TEAM_DIR / "pm-agent" / "aggregate_tokens.py"
_METRICS_AGGREGATOR = _TEAM_DIR / "scripts" / "lib" / "metrics_aggregator.py"

_LAST_RELEASED = "v1.3.0"
_DAY = "2026-03-01"
_MONTH = "2026-03"
_HISTORY_CSV = (
    "rc,project,workflow_type,outcome,tasks_total\n"
    "v1.3.0,archive-demo,release,shipped,71\n"
)


# ---------------------------------------------------------------------------
# Sandbox content
# ---------------------------------------------------------------------------


def _tokens(sequence: int) -> dict:
    """A new-schema tokens.json whose figures differ per task, so a dropped task changes every total."""
    return {
        "provider": "claude",
        "agent": "coder",
        "invocations": 1 + sequence % 3,
        "elapsed_seconds": 60 + sequence,
        "timestamp": f"{_DAY}T{sequence % 24:02d}:{sequence % 60:02d}:00Z",
        "total_cost_usd": round(0.01 * sequence, 6),
        "model_usage": {
            "claude-opus-4-8": {
                "input_tokens": 100 * sequence,
                "output_tokens": 10 * sequence,
                "cache_creation_input_tokens": sequence,
                "cache_read_input_tokens": 1000 + sequence,
                "cost_usd": round(0.01 * sequence, 6),
            }
        },
    }


def _scenario_with_tokens(tmp_path: pathlib.Path):
    """The archive acceptance scenario, every task carrying an artifacts/tokens.json."""
    scenario = build_acceptance_scenario(tmp_path)
    for sequence, task_id in enumerate(scenario.task_ids, start=1):
        tokens = scenario.sandbox.project_root / "tasks" / task_id / "artifacts" / "tokens.json"
        tokens.write_text(json.dumps(_tokens(sequence), indent=2) + "\n", encoding="utf-8")
    return scenario


def _archive_now(box: ArchiveSandbox) -> tuple[str, ...]:
    """Run the real archive engine on the sandbox; return the IDs it moved."""
    result = apply(plan(box.project, kanban_root=box.root), log=lambda _line: None)
    assert result.status == RESULT_OK, result.error
    return tuple(result.moved_ids)


def _assert_tasks_archived_with_tokens(box: ArchiveSandbox, moved: tuple[str, ...]) -> None:
    """At least one archived task carries usage, so a reader that ignored the archive would change totals."""
    archived_tasks = archive_layout(box.root, box.project).tasks
    with_tokens = [
        task_id for task_id in moved if (archived_tasks / task_id / "artifacts" / "tokens.json").is_file()
    ]
    assert with_tokens, f"the archive run moved no task carrying a tokens.json; moved: {moved}"
    for task_id in with_tokens:
        assert not (box.project_root / "tasks" / task_id).exists(), f"{task_id} is still live after archiving"


def _drop_rollups(box: ArchiveSandbox) -> None:
    """Remove every roll-up both aggregators write, so the next read rebuilds from task folders."""
    for sub in ("usage", "metrics/rc", "metrics/day"):
        shutil.rmtree(box.project_root / sub, ignore_errors=True)


# ---------------------------------------------------------------------------
# Running the real scripts
# ---------------------------------------------------------------------------


def _env(box: ArchiveSandbox, tmp_path: pathlib.Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (box.root / "tmp").mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(box.root),
            "KANBAN_ROOT": str(box.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(box.root / "tmp"),
            "NO_COLOR": "1",
        },
    )


def _run(command: list[str], box: ArchiveSandbox, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    foreign_cwd = tmp_path / "cwd"
    foreign_cwd.mkdir(exist_ok=True)
    return subprocess.run(
        command, capture_output=True, text=True, env=_env(box, tmp_path), cwd=str(foreign_cwd),
        timeout=120, stdin=subprocess.DEVNULL,
    )


def _say(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _report(script: pathlib.Path, box: ArchiveSandbox, tmp_path: pathlib.Path, *args: str) -> str:
    """The script's stdout for *args*; the run must succeed and print something."""
    result = _run([str(script), "--project", box.project, "--kanban-root", str(box.root), *args], box, tmp_path)
    assert result.returncode == 0, _say(result)
    assert result.stdout.strip(), f"{script.name} {' '.join(args)} printed nothing\n{_say(result)}"
    return result.stdout


def _aggregate_all(aggregator: pathlib.Path, box: ArchiveSandbox, tmp_path: pathlib.Path) -> None:
    result = _run(
        [sys.executable, str(aggregator), "--project", box.project, "--kanban-root", str(box.root), "--all"],
        box, tmp_path,
    )
    assert result.returncode == 0, _say(result)


def _rollups(directory: pathlib.Path) -> dict[str, object]:
    """Every roll-up under *directory*, by relative path, parsed; the RC roll-up's wall-clock stamp dropped."""
    found: dict[str, object] = {}
    for path in sorted(directory.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.pop("shipped_at", None)
        found[path.relative_to(directory).as_posix()] = data
    return found


# ---------------------------------------------------------------------------
# The shared task-folder listing
# ---------------------------------------------------------------------------


def test_archived_task_dirs_lists_archived_folders_with_no_live_folder(tmp_path):
    box = ArchiveSandbox(tmp_path)
    archived = archive_layout(box.root, box.project).tasks
    older, newer, both = (ArchiveSandbox.task_id("CODER", n) for n in (1, 2, 3))
    for task_id in (newer, older, both):
        (archived / task_id).mkdir(parents=True)
    (archived / "queues").mkdir()
    (archived / "not-a-task-id").mkdir()
    box.add_task(both, "DONE")

    found = archived_task_dirs(box.project_root / "tasks")

    assert found == [archived / older, archived / newer], (
        "expected the archived folders whose ID has no live folder, in ID order, "
        f"and never the archive's queues/ or a non-task name; got {found}"
    )


def test_archived_task_dirs_is_empty_outside_the_live_project_shape(tmp_path):
    box = ArchiveSandbox(tmp_path)
    (archive_layout(box.root, box.project).tasks / ArchiveSandbox.task_id("CODER", 1)).mkdir(parents=True)
    stray = tmp_path / "elsewhere" / "tasks"
    stray.mkdir(parents=True)

    assert archived_task_dirs(stray) == []
    other = ArchiveSandbox(tmp_path / "other")
    assert archived_task_dirs(other.project_root / "tasks") == [], "a project with no archive lists nothing"


def test_task_dirs_with_archived_merges_live_and_archived_in_id_order(tmp_path):
    box = ArchiveSandbox(tmp_path)
    archived = archive_layout(box.root, box.project).tasks
    first, second, third = (ArchiveSandbox.task_id("CODER", n) for n in (1, 2, 3))
    live_first = box.add_task(first, "DONE")
    live_third = box.add_task(third, "WORKING")
    (archived / second).mkdir(parents=True)
    (archived / "queues").mkdir()

    found = task_dirs_with_archived(box.project_root / "tasks")

    assert found == [live_first, archived / second, live_third], (
        f"expected live and archived task folders in ID order with no queues/ directory; got {found}"
    )


# ---------------------------------------------------------------------------
# Roll-ups rebuilt after archiving
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "aggregator, rollup_dir",
    [(_TOKEN_AGGREGATOR, "usage"), (_METRICS_AGGREGATOR, "metrics")],
    ids=["token-rollups", "metrics-rollups"],
)
def test_rollups_rebuilt_after_archiving_match_rollups_built_before(tmp_path, aggregator, rollup_dir):
    box = _scenario_with_tokens(tmp_path).sandbox
    _aggregate_all(aggregator, box, tmp_path)
    before = _rollups(box.project_root / rollup_dir)
    assert before, f"{aggregator.name} --all wrote no roll-up under {rollup_dir}/"

    _assert_tasks_archived_with_tokens(box, _archive_now(box))
    _drop_rollups(box)
    _aggregate_all(aggregator, box, tmp_path)

    assert _rollups(box.project_root / rollup_dir) == before, (
        f"{aggregator.name} --all rebuilt different roll-ups after the archive run"
    )


# ---------------------------------------------------------------------------
# The operator reports
# ---------------------------------------------------------------------------


_COST_SCOPES = [
    pytest.param(("--rc", _LAST_RELEASED, "--csv"), id="rc-csv"),
    pytest.param(("--month", _MONTH), id="month-text"),
    pytest.param(("--day", _DAY, "--csv"), id="day-csv"),
]


@pytest.mark.parametrize("args", _COST_SCOPES)
def test_cost_report_totals_are_identical_before_and_after_archiving(tmp_path, args):
    box = _scenario_with_tokens(tmp_path).sandbox
    before = _report(_COST_REPORT_SH, box, tmp_path, *args)

    _assert_tasks_archived_with_tokens(box, _archive_now(box))
    _drop_rollups(box)
    after = _report(_COST_REPORT_SH, box, tmp_path, *args)

    assert after == before, f"cost-report.sh {' '.join(args)} changed after archiving"


_METRICS_SCOPES = [
    pytest.param(("--rc", _LAST_RELEASED), id="rc-json"),
    pytest.param(("--day", _DAY), id="day-json"),
    pytest.param(("--csv",), id="history-csv"),
]


@pytest.mark.parametrize("args", _METRICS_SCOPES)
def test_metrics_report_totals_are_identical_before_and_after_archiving(tmp_path, args):
    box = _scenario_with_tokens(tmp_path).sandbox
    (box.project_root / "metrics").mkdir(exist_ok=True)
    (box.project_root / "metrics" / "history.csv").write_text(_HISTORY_CSV, encoding="utf-8")
    before = _report(_METRICS_REPORT_SH, box, tmp_path, *args)

    _assert_tasks_archived_with_tokens(box, _archive_now(box))
    _drop_rollups(box)
    after = _report(_METRICS_REPORT_SH, box, tmp_path, *args)

    assert after == before, f"metrics-report.sh {' '.join(args)} changed after archiving"

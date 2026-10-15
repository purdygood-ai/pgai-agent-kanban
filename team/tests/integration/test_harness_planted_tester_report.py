"""
test_harness_planted_tester_report.py
=====================================
The synthetic TESTER report each test harness plants is read by the one
TESTER report parser as the recommendation the harness wrote.

Two harnesses plant a ``report.md`` for a simulated TESTER task:

  - team/scripts/wake/harness.sh (the integration-test wake provider), from
    provider_invoke_agent, for every TESTER task it completes;
  - team/scripts/harness/helloworld-flow-check.sh, at the end of its verify
    stage.

Both build that report from the committed head fixture
team/scripts/harness/fixtures/synthetic-tester-report-head.md (title and
recommendation section) followed by their own Summary, Evidence and Findings
sections. Each test here runs one harness for real, finds the report it
planted, and parses it with ``parse_report``: the recommendation reads as the
``SHIP`` the harness writes, the harness's own summary follows the head
intact, and the report carries no findings.

``SHIP`` is the value as written (``TesterReport.raw["recommendation"]``).
It is not one of the parser's normalised recommendation tokens, so the
normalised ``recommendation`` field is not what these tests assert on.

Design notes:
  - No seam is mocked: each harness is run as a subprocess against its own
    throwaway root under tmp_path, and the report is read from where the
    harness wrote it.
  - Subprocess environments are built by build_scrubbed_env from an explicit
    set of names, never inherited wholesale.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

from pgai_agent_kanban.reports.tester_report import parse_report
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_WAKE_HARNESS = _TEAM_DIR / "scripts" / "wake" / "harness.sh"
_HELLOWORLD_HARNESS = _TEAM_DIR / "scripts" / "harness" / "helloworld-flow-check.sh"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

_PROJECT = "planted_report_proj"
_TASK_ID = "TESTER-20260101-001-verify-planted-report"

# The recommendation both harnesses write, as written.
_PLANTED_RECOMMENDATION = "SHIP"


def _interpreter_dir() -> pathlib.Path:
    """The directory holding the interpreter running this test: python3 there
    is 3.12+ with pytest importable, which both harnesses' substrates need."""
    return pathlib.Path(sys.executable).parent


# ---------------------------------------------------------------------------
# wake/harness.sh
# ---------------------------------------------------------------------------


def _build_wake_harness_root(parent: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Build a kanban root holding one BACKLOG TESTER task for wake/harness.sh.

    The root names harness as its active provider (the harness exits without
    dispatching otherwise) and registers one testing-only project whose
    tester backlog lists the single task.

    Returns:
        (kanban_root, project_root)
    """
    root = parent / "kanban_root"
    proj = root / "projects" / _PROJECT
    queues = proj / "tasks" / "queues"
    task_dir = proj / "tasks" / _TASK_ID
    for directory in (
        queues / "plans",
        task_dir / "artifacts",
        task_dir / "logs",
        proj / "logs",
        root / "logs" / "agents",
        root / "logs" / "debug" / "archive",
        root / "locks",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n"
        "[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 1\n\n"
        "[providers]\nactive = harness\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\n"
        "priority=1\n"
        "description=planted report fixture project\n"
        "enabled=true\n",
        encoding="utf-8",
    )
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_PROJECT}\n"
        "dev_tree_path = /dev/null\n"
        f"git_repo_url = git@example.com:{_PROJECT}.git\n"
        "git_remote_name = origin\n"
        "workflow_type = testing-only\n"
        "is_self_build = false\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    for agent in ("coder", "pm", "writer", "cm", "bug", "priority"):
        (queues / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n", encoding="utf-8"
        )
    (queues / "tester_backlog.md").write_text(
        f"# TESTER Backlog\n\n- [ ] {_TASK_ID}\n", encoding="utf-8"
    )

    (task_dir / "README.md").write_text(
        f"# Task: verify the planted report\n\n"
        f"## Task ID\n{_TASK_ID}\n\n"
        "## Role\nTESTER\n\n"
        "## Workflow Type\ntesting-only\n\n"
        "## Source Branch\nrc/v1.0.0\n\n"
        "## Goal\nComplete under the wake harness so it plants a report.\n\n"
        "## Prerequisites\nnone\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        "# Status\n\n"
        f"## Task\n{_TASK_ID}\n\n"
        "## Role\nTESTER\n\n"
        "## State\nBACKLOG\n\n"
        "## Summary\nnone\n\n"
        "## Blockers\nnone\n",
        encoding="utf-8",
    )

    workflows = root / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_REAL_WORKFLOWS_DIR / "testing-only", workflows / "testing-only")

    return root, proj


def test_wake_harness_plants_a_report_the_parser_reads_as_ship(tmp_path: pathlib.Path) -> None:
    """wake/harness.sh completes a TESTER task and plants a report whose
    recommendation parses as SHIP, with its own summary after the head."""
    root, proj = _build_wake_harness_root(tmp_path)
    temp_root = tmp_path / "pgai_kanban_tmp"
    temp_root.mkdir()
    (tmp_path / "home").mkdir()
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=(_interpreter_dir(),),
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_PROJECT_ROOT": str(proj),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root),
            "PYTHONPATH": str(_TEAM_DIR),
        },
    )

    result = subprocess.run(
        ["bash", str(_WAKE_HARNESS), "--agent=tester", "--max-tasks=1"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
        timeout=120,
    )
    log = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.returncode == 0, f"wake/harness.sh exited {result.returncode}.\n{log}"

    report_path = proj / "tasks" / _TASK_ID / "artifacts" / "report.md"
    assert report_path.is_file(), (
        f"wake/harness.sh planted no report at {report_path}; the task may not "
        f"have been dispatched.\n{log}"
    )

    report = parse_report(report_path)
    assert report.raw.get("recommendation") == _PLANTED_RECOMMENDATION, (
        f"Expected the planted report's recommendation to read "
        f"{_PLANTED_RECOMMENDATION!r}; parse_report read "
        f"{report.raw.get('recommendation')!r} from {report_path}:\n"
        f"{report_path.read_text(encoding='utf-8')}"
    )
    assert report.summary is not None and report.summary.startswith(
        "Integration test harness stub:"
    ), f"Expected the harness's own summary after the report head; got {report.summary!r}"
    assert report.findings == [], f"Expected no findings; got {report.findings!r}"


# ---------------------------------------------------------------------------
# harness/helloworld-flow-check.sh
# ---------------------------------------------------------------------------


@pytest.fixture
def _require_git() -> None:
    """The helloworld flow drives real git (CODER merge, CM open-rc/release)."""
    require_binary("git", label="harness-planted-tester-report")


def test_helloworld_flow_plants_a_report_the_parser_reads_as_ship(
    tmp_path: pathlib.Path, _require_git: None
) -> None:
    """helloworld-flow-check.sh's verify stage plants a report whose
    recommendation parses as SHIP, with its own summary after the head."""
    temp_root = tmp_path / "pgai_kanban_tmp"
    temp_root.mkdir()
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    env = build_scrubbed_env(
        home_dir=home_dir,
        extra_path_dirs=(_interpreter_dir(),),
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root)},
    )

    result = subprocess.run(
        ["bash", str(_HELLOWORLD_HARNESS)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    log = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.returncode == 0, f"helloworld-flow-check.sh exited {result.returncode}.\n{log}"

    reports = sorted(temp_root.rglob("TESTER-*/artifacts/report.md"))
    assert len(reports) == 1, (
        f"Expected exactly one planted TESTER report under {temp_root}; "
        f"found {[str(p) for p in reports]}.\n{log}"
    )
    report_path = reports[0]

    report = parse_report(report_path)
    assert report.raw.get("recommendation") == _PLANTED_RECOMMENDATION, (
        f"Expected the planted report's recommendation to read "
        f"{_PLANTED_RECOMMENDATION!r}; parse_report read "
        f"{report.raw.get('recommendation')!r} from {report_path}:\n"
        f"{report_path.read_text(encoding='utf-8')}"
    )
    assert report.summary is not None and report.summary.startswith(
        "helloworld-flow-check.sh harness:"
    ), f"Expected the harness's own summary after the report head; got {report.summary!r}"
    assert report.findings == [], f"Expected no findings; got {report.findings!r}"

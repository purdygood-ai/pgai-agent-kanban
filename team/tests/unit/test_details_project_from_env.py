"""
test_details_project_from_env.py
=================================
Behavioral tests for how team/scripts/why.sh --details and
team/scripts/verdict-report.sh --details find their project: --project
when it is given, otherwise $PGAI_PROJECT_NAME. Only when neither names a
project does --details refuse.

Acceptance criteria exercised:

  (1) With two projects registered and PGAI_PROJECT_NAME naming the second,
      why.sh --details with no --project prints that project's verdict line
      followed by the "== locks ==" pane -- the same output and exit code
      as why.sh --project NAME --details.
  (2) Under the same root, verdict-report.sh --details with no --project
      exits 0 and prints only that project's "project=" blocks -- the same
      output as verdict-report.sh --project NAME --details.
  (3) With PGAI_PROJECT_NAME unset and no --project, each script refuses
      --details with exit 1 and its stable machine key, and each refusal's
      remedy offers both $PGAI_PROJECT_NAME and --project.
  (4) Each script's --help describes --details as needing a project from
      --project or $PGAI_PROJECT_NAME, never as requiring --project.

Every run gets a throwaway kanban root and an environment built from an
explicit, minimal set of names (tests/fixtures/scrubbed_subprocess_env.py),
so neither the operator's live install nor an ambient PGAI_PROJECT_NAME,
PGAI_TASKS_DIR or PGAI_PROJECT_ROOT can reach the scripts (project README,
"Live-install safety").

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"
_VERDICT_REPORT_SH = _TEAM_DIR / "scripts" / "verdict-report.sh"

# The project PGAI_PROJECT_NAME names: second in priority order, so output
# naming it alone cannot be a walk of every project cut short.
_ENV_PROJECT = "bb"


@pytest.fixture()
def board(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """A throwaway root registering aa (priority 1) and bb (priority 2),
    each with a project directory, plus a scratch HOME and temp directory
    outside it."""
    root = tmp_path / "root"
    home = tmp_path / "home"
    temp_dir = tmp_path / "scratch"
    for directory in (root, home, temp_dir):
        directory.mkdir()
    (root / "projects.cfg").write_text(
        "[project:aa]\npriority = 1\n\n[project:bb]\npriority = 2\n", encoding="utf-8"
    )
    for name in ("aa", "bb"):
        (root / "projects" / name / "tasks" / "queues").mkdir(parents=True)
    return {"root": root, "home": home, "temp_dir": temp_dir}


def _run(
    script: pathlib.Path,
    args: list[str],
    board: dict[str, pathlib.Path],
    *,
    project_name: str | None,
) -> subprocess.CompletedProcess:
    """Run *script* against the board; PGAI_PROJECT_NAME is set only when
    *project_name* is given."""
    extra_env = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(board["root"]),
        "KANBAN_ROOT": str(board["root"]),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(board["temp_dir"]),
    }
    if project_name is not None:
        extra_env["PGAI_PROJECT_NAME"] = project_name
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=build_scrubbed_env(home_dir=board["home"], extra_env=extra_env),
        cwd=str(board["root"]),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def _report_projects(stdout: str) -> list[str]:
    """The project= value of every blank-line-separated block, in order."""
    projects = []
    for block in stdout.strip("\n").split("\n\n"):
        for line in block.splitlines():
            if line.startswith("project="):
                projects.append(line.partition("=")[2])
    return projects


# ---------------------------------------------------------------------------
# (1) why.sh --details takes its project from PGAI_PROJECT_NAME
# ---------------------------------------------------------------------------


def test_why_details_with_env_project_prints_its_verdict_line_then_the_locks_pane(board) -> None:
    result = _run(_WHY_SH, ["--details"], board, project_name=_ENV_PROJECT)

    assert result.returncode != 1, f"why.sh --details refused: {result.stderr!r}"
    lines = result.stdout.splitlines()
    assert lines[0].startswith(f"{_ENV_PROJECT}: "), f"first line is not bb's verdict line: {lines[:3]!r}"
    pane = [line for line in lines[1:] if line.strip()]
    assert pane[0] == "== locks ==", f"the locks pane does not follow the verdict line: {lines[:4]!r}"
    assert not any(line.startswith("aa: ") for line in lines), "the other registered project was reported"


def test_why_details_with_env_project_matches_the_explicit_project_flag(board) -> None:
    from_env = _run(_WHY_SH, ["--details"], board, project_name=_ENV_PROJECT)
    from_flag = _run(_WHY_SH, ["--project", _ENV_PROJECT, "--details"], board, project_name=None)

    assert from_env.returncode == from_flag.returncode
    assert from_env.stdout == from_flag.stdout


# ---------------------------------------------------------------------------
# (2) verdict-report.sh --details takes its project from PGAI_PROJECT_NAME
# ---------------------------------------------------------------------------


def test_verdict_report_details_with_env_project_prints_only_that_projects_blocks(board) -> None:
    result = _run(_VERDICT_REPORT_SH, ["--details"], board, project_name=_ENV_PROJECT)

    assert result.returncode == 0, f"verdict-report.sh --details failed: {result.stderr!r}"
    projects = _report_projects(result.stdout)
    assert projects, f"no project= blocks printed: {result.stdout!r}"
    assert set(projects) == {_ENV_PROJECT}, projects
    assert "kind=posture" in result.stdout, "the --details blocks did not print"


def test_verdict_report_details_with_env_project_matches_the_explicit_project_flag(board) -> None:
    from_env = _run(_VERDICT_REPORT_SH, ["--details"], board, project_name=_ENV_PROJECT)
    from_flag = _run(_VERDICT_REPORT_SH, ["--project", _ENV_PROJECT, "--details"], board, project_name=None)

    assert from_env.returncode == from_flag.returncode == 0
    assert from_env.stdout == from_flag.stdout


# ---------------------------------------------------------------------------
# (3) With neither --project nor PGAI_PROJECT_NAME, --details refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script, error_key",
    [
        (_WHY_SH, "why.details_requires_project"),
        (_VERDICT_REPORT_SH, "verdict_report.details_requires_project"),
    ],
    ids=["why", "verdict-report"],
)
def test_details_with_no_project_named_refuses_offering_both_sources(board, script, error_key) -> None:
    result = _run(script, ["--details"], board, project_name=None)

    assert result.returncode == 1
    assert result.stdout == ""
    assert f"error:   {error_key}" in result.stderr, result.stderr
    remedy = [line for line in result.stderr.splitlines() if line.startswith("remedy:")]
    assert remedy, f"no remedy line: {result.stderr!r}"
    assert "$PGAI_PROJECT_NAME" in remedy[0] and "--project" in remedy[0], remedy[0]


# ---------------------------------------------------------------------------
# (4) --help describes the same two sources
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script, paragraph_start, option_line",
    [
        (_WHY_SH, "--details:", False),
        (_VERDICT_REPORT_SH, "--project NAME --details:", True),
    ],
    ids=["why", "verdict-report"],
)
def test_help_says_details_needs_a_project_from_either_source(
    board, script, paragraph_start, option_line
) -> None:
    result = _run(script, ["--help"], board, project_name=None)

    assert result.returncode == 0
    assert "requires --project" not in result.stdout.lower()
    paragraphs = [p for p in result.stdout.split("\n\n") if p.startswith(paragraph_start)]
    assert len(paragraphs) == 1, f"no single help paragraph opens {paragraph_start!r}: {result.stdout!r}"
    # Whitespace-joined so a phrase wrapped across help lines reads as one.
    paragraph = " ".join(paragraphs[0].split())
    assert "--project" in paragraph and "$PGAI_PROJECT_NAME" in paragraph, paragraph
    if option_line:
        options = [line for line in result.stdout.splitlines() if line.lstrip().startswith("--details ")]
        assert len(options) == 1, result.stdout
        assert "--project" in options[0] and "$PGAI_PROJECT_NAME" in options[0], options[0]

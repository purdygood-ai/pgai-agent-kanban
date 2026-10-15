"""
test_discovery_dead_bundle_frees_slot.py
========================================
End to end through discovery_run_pipeline: a bundle that will never ship
does not stop discovery from bundling the next bug.

  - A bundle an RC unwind renamed ``*.SUPERSEDED-on-cancel-*`` and left at
    ``## Status running`` neither holds the bug-bundle slot nor counts as a
    queued requirement: an open bug is bundled, and with every bug deferred
    the hygiene sweep fires.
  - A ``running`` bundle whose PM task ended WONT-DO outside the Active RC
    is reported stuck (``discovery.bundle-stuck``, remedy naming the bundle)
    and an open bug is bundled anyway.
  - The control: a ``running`` bundle whose PM task is still live keeps the
    slot, and the open bug waits.

Every test builds its own two-project kanban root under tmp_path
(tests/fixtures/two_project.py); the helpers are local copies of the shapes
test_discovery_bug_deferral_gate.py uses, per that file's convention.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap
from typing import Optional

from pgai_agent_kanban.lib.error_contract import parse_error_contract

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "project_a"
_PM_TASK = "PM-20260101-001-decompose-v0-0-1-bugfix-bundle"
_SUPERSEDED = "v0.0.1-bugfix-bundle-20260101.SUPERSEDED-on-cancel-2026-01-02T00:00:00+00:00.md"


def _project(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _run_discovery(root: pathlib.Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{_PROJECT}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_DEV_TREE_PATH"):
        env.pop(name, None)
    env.update({
        "KANBAN_ROOT": str(root),
        "TEAM_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_PROJECT_NAME": _PROJECT,
    })
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, cwd=str(root), timeout=120,
    )


def _add_bug(root: pathlib.Path, number: int = 1, severity: str = "medium") -> pathlib.Path:
    bugs = _project(root) / "bugs"
    bugs.mkdir(parents=True, exist_ok=True)
    path = bugs / f"BUG-{number:04d}-open-thing.md"
    path.write_text(
        f"# BUG-{number:04d}-open-thing\n\n**Severity:** {severity}\n\n## Status\nopen\n\n## Summary\nx\n",
        encoding="utf-8",
    )
    return path


def _add_running_bundle(root: pathlib.Path, name: str, pm_task: str = _PM_TASK) -> pathlib.Path:
    requirements = _project(root) / "requirements"
    requirements.mkdir(parents=True, exist_ok=True)
    path = requirements / name
    path.write_text(
        "# Requirements: v0.0.1 -- BUG bundle\n\n## Status\nrunning\n\n"
        f"## PM Task\n\n{pm_task}\n\n## Target Version\n\nv0.0.1\n\n## Workflow Type\n\nrelease\n\n"
        "## Bundled Items\n\n"
        f"- BUG-0900-earlier.md (`{_project(root) / 'bugs' / 'BUG-0900-earlier.md'}`)\n",
        encoding="utf-8",
    )
    return path


def _add_pm_task(root: pathlib.Path, state: str) -> None:
    task = _project(root) / "tasks" / _PM_TASK
    task.mkdir(parents=True, exist_ok=True)
    (task / "status.md").write_text(f"# Status\n\n## State\n{state}\n\n## Summary\nx\n", encoding="utf-8")


def _defer_every_bug(root: pathlib.Path) -> None:
    with (_project(root) / "project.cfg").open("a", encoding="utf-8") as handle:
        handle.write("\n[release]\ndeferred_bugs = true\ndeferred_bug_level = medium\n")


def _new_bundles(root: pathlib.Path, *known: pathlib.Path) -> list[pathlib.Path]:
    found = sorted((_project(root) / "requirements").glob("*-bugfix-bundle-*.md"))
    return [path for path in found if path not in known]


def _status(path: pathlib.Path) -> Optional[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "## Status":
            return next((value.strip() for value in lines[index + 1:] if value.strip()), None)
    return None


def test_a_superseded_running_bundle_does_not_stop_the_next_bug_bundle(two_project_root) -> None:
    root = two_project_root
    superseded = _add_running_bundle(root, _SUPERSEDED)
    bug = _add_bug(root)

    result = _run_discovery(root)

    assert result.returncode == 0, result.stderr
    assert "declining to mint" not in result.stderr, result.stderr
    assert len(_new_bundles(root, superseded)) == 1, result.stderr
    assert _status(bug) == "running"


def test_a_superseded_running_bundle_does_not_withhold_the_hygiene_sweep(two_project_root) -> None:
    root = two_project_root
    superseded = _add_running_bundle(root, _SUPERSEDED)
    _defer_every_bug(root)
    bug = _add_bug(root, severity="low")

    result = _run_discovery(root)

    assert result.returncode == 0, result.stderr
    assert "hygiene sweep withheld" not in result.stderr.lower(), result.stderr
    assert len(_new_bundles(root, superseded)) == 1, result.stderr
    assert _status(bug) == "running"


def test_a_stuck_bundle_is_reported_and_the_next_bug_is_bundled(two_project_root) -> None:
    root = two_project_root
    stuck = _add_running_bundle(root, "v0.0.1-bugfix-bundle-20260101.md")
    _add_pm_task(root, "WONT-DO")
    bug = _add_bug(root)

    result = _run_discovery(root)

    assert result.returncode == 0, result.stderr
    fields = parse_error_contract(result.stderr)
    assert fields is not None and fields["error"] == "discovery.bundle-stuck", result.stderr
    assert f"--project {_PROJECT} --key {stuck.stem} --state wont-do" in fields["remedy"]
    assert len(_new_bundles(root, stuck)) == 1, result.stderr
    assert _status(bug) == "running"
    assert _status(stuck) == "running", "discovery reports a stuck bundle; it never closes one itself"


def test_a_running_bundle_whose_pm_task_is_live_keeps_the_slot(two_project_root) -> None:
    root = two_project_root
    pending = _add_running_bundle(root, "v0.0.1-bugfix-bundle-20260101.md")
    _add_pm_task(root, "BACKLOG")
    bug = _add_bug(root)

    result = _run_discovery(root)

    assert result.returncode == 0, result.stderr
    assert "discovery.bundle-stuck" not in result.stderr
    assert _new_bundles(root, pending) == [], result.stderr
    assert _status(bug) == "open"

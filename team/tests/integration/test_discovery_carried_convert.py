"""
test_discovery_carried_convert.py
=================================
Integration tests: the real discovery pipeline (discovery_run_pipeline in
team/scripts/lib/discovery.sh) never bundles a needs-operator item, and
parks a carried item on an operator question instead of bundling it again.

  - A needs-operator bug beside an open one: the pass bundles the open bug
    only and leaves the needs-operator file untouched.
  - A bug left ``running`` by a closed bundle: one pass sets it to
    ``needs-operator`` with exactly one open inbox entry whose Source names
    the bug and whose Question names the bundle and either the reason the
    bundle's PM task recorded or that none was recorded; no new bundle
    lists it, and a second pass writes nothing.
  - Once that entry is answered, the next pass bundles the bug.

Each test builds its own sandbox kanban root under tmp_path, in a project
whose name the operator inbox accepts, and runs the pipeline with all five
kanban-root variables set or cleared. Fixture identifiers use the
fictitious BUG-9xxx range.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.two_project import add_registered_project, build_two_project_root

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "carried-pipeline"
_PM_TASK = "PM-20990101-001-decompose-v9-9-9-bugfix-bundle"
_REASON = "BUG-9502 is left out: only the operator knows which mirror to use."


def _sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """``(kanban_root, project_root)`` for a fresh sandbox."""
    root = build_two_project_root(tmp_path)
    project_root = add_registered_project(root, _PROJECT)
    for sub in ("bugs", "requirements"):
        (project_root / sub).mkdir(exist_ok=True)
    (root / "tmp").mkdir(exist_ok=True)
    return root, project_root


def _env(root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_DEV_TREE_PATH", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    env["KANBAN_ROOT"] = str(root)
    env["TEAM_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    env["PGAI_DEFERRED_BUGS"] = "false"
    return env


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
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_env(root),
        cwd=str(root),
        timeout=60,
    )


def _bug(project_root: pathlib.Path, name: str, status: str) -> pathlib.Path:
    path = project_root / "bugs" / name
    path.write_text(
        f"# {name[:-3]}\n\n**Severity:** medium\n\n## Status\n{status}\n\n## Symptom\nsynthetic\n", encoding="utf-8"
    )
    return path


def _closed_bundle(project_root: pathlib.Path, items: list[pathlib.Path], pm_task: str) -> pathlib.Path:
    path = project_root / "requirements" / "v0.0.1-bugfix-bundle-20990101.md"
    lines = "".join(f"- {item.name} (`{item}`)\n" for item in items)
    path.write_text(
        "# v0.0.1-bugfix-bundle-20990101\n\n## Status\ndone\n\n## Target Version\nv0.0.1\n\n"
        f"## Workflow Type\nrelease\n\n## PM Task\n{pm_task}\n\n## Bundled Items\n\n{lines}",
        encoding="utf-8",
    )
    return path


def _bundles(project_root: pathlib.Path) -> list[pathlib.Path]:
    return sorted((project_root / "requirements").glob("*-bugfix-bundle-*.md"))


def _status(path: pathlib.Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().lower() == "## status")
    return next(line.strip() for line in lines[index + 1 :] if line.strip())


def _open_entries(root: pathlib.Path) -> tuple:
    return inbox.list_open(OpsContext(kanban_root=root), _PROJECT)


def _snapshot(*paths: pathlib.Path) -> dict[str, bytes]:
    files = [f for p in paths for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else [])]
    return {str(f): f.read_bytes() for f in files if f.is_file()}


def _carried_fixture(tmp_path: pathlib.Path, reason: bool):
    root, project_root = _sandbox(tmp_path)
    carried = _bug(project_root, "BUG-9502-carried-thing.md", "running")
    fixed = _bug(project_root, "BUG-9501-fixed-thing.md", "done")
    bundle = _closed_bundle(project_root, [fixed, carried], _PM_TASK)
    status_dir = project_root / "tasks" / _PM_TASK
    status_dir.mkdir(parents=True)
    summary = f"Agent: PM. Plan written with one ticket. {_REASON if reason else ''}"
    (status_dir / "status.md").write_text(f"## State\nDONE\n\n## Summary\n{summary.strip()}\n", encoding="utf-8")
    return root, project_root, carried, bundle


def test_a_needs_operator_bug_is_never_listed_in_a_bundle(tmp_path) -> None:
    root, project_root = _sandbox(tmp_path)
    waiting = _bug(project_root, "BUG-9503-waiting-thing.md", "needs-operator")
    open_bug = _bug(project_root, "BUG-9504-open-thing.md", "open")
    waiting_before = waiting.read_bytes()

    result = _run_discovery(root)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "DISCOVERY_STATUS=produced_work" in result.stdout, result.stdout + result.stderr
    [bundle] = _bundles(project_root)
    text = bundle.read_text(encoding="utf-8")
    assert open_bug.name in text
    assert waiting.name not in text
    assert waiting.read_bytes() == waiting_before
    assert f"skipping {waiting.name}: 'needs-operator'" in result.stderr


@pytest.mark.parametrize("reason", [True, False], ids=["reason-recorded", "no-reason-recorded"])
def test_a_carried_bug_is_parked_with_one_inbox_entry_and_a_second_pass_writes_nothing(tmp_path, reason) -> None:
    root, project_root, carried, bundle = _carried_fixture(tmp_path, reason)

    first = _run_discovery(root)

    assert first.returncode == 0, f"stdout: {first.stdout}\nstderr: {first.stderr}"
    assert _status(carried) == "needs-operator", first.stderr
    [entry] = _open_entries(root)
    assert entry.source == carried.stem
    assert bundle.name in entry.question
    if reason:
        assert _REASON in entry.question, entry.question
    else:
        assert f"No reason was recorded: PM task {_PM_TASK}'s status.md does not name BUG-9502." in entry.question
    assert _bundles(project_root) == [bundle], first.stderr

    inbox_file = project_root / "operator-inbox.md"
    before = _snapshot(project_root / "bugs", project_root / "requirements", inbox_file)
    second = _run_discovery(root)

    assert second.returncode == 0, f"stdout: {second.stdout}\nstderr: {second.stderr}"
    assert _snapshot(project_root / "bugs", project_root / "requirements", inbox_file) == before, second.stderr
    assert len(_open_entries(root)) == 1


def test_an_answered_carried_bug_is_bundled_by_the_next_pass(tmp_path) -> None:
    root, project_root, carried, bundle = _carried_fixture(tmp_path, reason=True)
    converted = _run_discovery(root)
    assert converted.returncode == 0, converted.stderr
    [entry] = _open_entries(root)

    answered = subprocess.run(
        [
            sys.executable,
            "-m",
            "pgai_agent_kanban.overwatch.inbox",
            "answer",
            "--project",
            _PROJECT,
            "--kanban-root",
            str(root),
            "--id",
            entry.entry_id,
            "--answer",
            "Use the primary mirror.",
        ],
        capture_output=True,
        text=True,
        env={**_env(root), "PYTHONPATH": str(_TEAM_DIR)},
        timeout=60,
    )
    assert answered.returncode == 0, answered.stderr
    assert _status(carried) == "open"

    result = _run_discovery(root)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "DISCOVERY_STATUS=produced_work" in result.stdout, result.stdout + result.stderr
    [new_bundle] = [b for b in _bundles(project_root) if b != bundle]
    assert carried.name in new_bundle.read_text(encoding="utf-8")
    assert _status(carried) == "running"

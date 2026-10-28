"""
test_discovery_refile_loop.py
=============================
Integration tests: the real discovery pipeline (discovery_run_pipeline in
team/scripts/lib/discovery.sh) collapses a bug re-file loop to one record
before it bundles.

  - Five live bugs whose slugs differ only by a re-file marker: one
    pass keeps the lowest-numbered, closes the other four as ``duplicate``
    with a close note naming the kept ID, narrates one line per closure
    naming both IDs, and bundles only the kept bug; a second pass changes
    no bug file.
  - A same-defect group holding a ``running`` bug keeps that bug and closes
    only the non-running members.
  - Two distinct bugs, one mentioning the other's ID in passing, are both
    left open and bundled.

Each test builds its own sandbox kanban root under tmp_path and runs the
pipeline with all five kanban-root variables set or cleared. Fixture
identifiers use the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

from pgai_agent_kanban.task_status import section_value
from tests.fixtures.two_project import add_registered_project, build_two_project_root

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "refile-pipeline"


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
        ["bash", "-c", script], capture_output=True, text=True, env=_env(root), cwd=str(root), timeout=60,
    )


def _bug(project_root: pathlib.Path, name: str, status: str = "open", symptom: str = "synthetic") -> pathlib.Path:
    path = project_root / "bugs" / name
    path.write_text(f"# {name[:-3]}\n\n**Severity:** medium\n\n## Status\n{status}\n\n## Symptom\n{symptom}\n",
                    encoding="utf-8")
    return path


def _status(path: pathlib.Path) -> str:
    return section_value(path.read_text(encoding="utf-8"), "Status")


def _close_note(path: pathlib.Path) -> str:
    return section_value(path.read_text(encoding="utf-8"), "Close Note")


def _closure_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("closed ")]


def _new_bundles(project_root: pathlib.Path) -> list[pathlib.Path]:
    return sorted((project_root / "requirements").glob("*-bugfix-bundle-*.md"))


def _snapshot(path: pathlib.Path) -> dict[str, bytes]:
    return {str(f): f.read_bytes() for f in sorted(path.rglob("*")) if f.is_file()}


def test_five_ordinal_slug_duplicates_collapse_to_the_lowest_numbered_bug(tmp_path) -> None:
    root, project_root = _sandbox(tmp_path)
    names = ["BUG-9301-lock-leak.md", "BUG-9302-lock-leak-again.md", "BUG-9303-second-lock-leak.md",
             "BUG-9304-lock-leak-3.md", "BUG-9305-lock-leak-redux.md"]
    kept, *duplicates = [_bug(project_root, name) for name in names]

    first = _run_discovery(root)

    assert first.returncode == 0, f"stdout: {first.stdout}\nstderr: {first.stderr}"
    for dup in duplicates:
        assert _status(dup) == "duplicate", first.stderr
        assert _close_note(dup).startswith("duplicate of BUG-9301 (BUG-9301-lock-leak): "), _close_note(dup)
    lines = _closure_lines(first.stderr)
    assert len(lines) == 4, first.stderr
    for dup, line in zip(duplicates, lines):
        assert line.startswith(f"closed {dup.name} as duplicate of BUG-9301 "), line
    [bundle] = _new_bundles(project_root)
    text = bundle.read_text(encoding="utf-8")
    assert kept.name in text
    assert not any(dup.name in text for dup in duplicates), text
    assert _status(kept) == "running"

    before = _snapshot(project_root / "bugs")
    second = _run_discovery(root)

    assert second.returncode == 0, f"stdout: {second.stdout}\nstderr: {second.stderr}"
    assert _closure_lines(second.stderr) == []
    assert _snapshot(project_root / "bugs") == before, second.stderr


def test_a_running_member_is_kept_and_only_non_running_members_close(tmp_path) -> None:
    root, project_root = _sandbox(tmp_path)
    older = _bug(project_root, "BUG-9311-cache-stale.md", "open")
    running = _bug(project_root, "BUG-9312-cache-stale-again.md", "running")
    newer = _bug(project_root, "BUG-9313-cache-stale-3.md", "needs-operator")

    result = _run_discovery(root)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert _status(running) == "running"
    for dup in (older, newer):
        assert _status(dup) == "duplicate", result.stderr
        assert _close_note(dup).startswith("duplicate of BUG-9312 (BUG-9312-cache-stale-again): ")
    assert [line.split()[1] for line in _closure_lines(result.stderr)] == [older.name, newer.name]
    assert all("duplicate of BUG-9312 " in line for line in _closure_lines(result.stderr))


def test_distinct_bugs_mentioning_each_other_in_passing_are_left_alone(tmp_path) -> None:
    root, project_root = _sandbox(tmp_path)
    first_bug = _bug(project_root, "BUG-9321-port-clash.md")
    second_bug = _bug(project_root, "BUG-9322-socket-timeout.md",
                      symptom="The socket times out, as BUG-9321 did before it was diagnosed.")

    result = _run_discovery(root)

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert _closure_lines(result.stderr) == []
    for bug in (first_bug, second_bug):
        assert _status(bug) == "running", result.stderr
        assert _close_note(bug) == ""
    [bundle] = _new_bundles(project_root)
    text = bundle.read_text(encoding="utf-8")
    assert first_bug.name in text and second_bug.name in text

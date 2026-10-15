"""
test_carried_bundle_item_rebundled.py
=====================================
End to end: a bundled item that a release ships without fixing comes back
as a candidate for the next bundle.

Three real stages run in order against one sandboxed kanban root:

  1. Bundle -- discovery_run_pipeline (team/scripts/lib/discovery.sh)
     bundles two open bugs and marks both ``running``.
  2. Release -- cm-release's bundle-close step
     (team/scripts/cm/promote_bundled_items.py, run as the release runs it)
     with one DONE task whose README fixes the first bug and names the
     second only to say it is not ticketed. The first bug closes, the
     second is carried at ``running``, and the bundle becomes ``done``.
  3. The next discovery pass -- the item scan offers the carried bug, and
     discovery_run_pipeline mints a second bundle that lists it and not
     the fixed one.

The bug-backlog cache (pm-agent/lib/bug_scanner.py) gives the same answer:
the carried bug renders ``[ ]`` and get_unbundled_bugs returns it.

Fixture identifiers use the fictitious BUG-9xxx range other suites use for
synthetic fixtures.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import textwrap

from tests.fixtures.two_project import PROJECT_A

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_PROMOTE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "promote_bundled_items.py"

_FIXED = "BUG-9201-fixed-thing.md"
_UNFIXED = "BUG-9202-unticketed-thing.md"


def _sandbox_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """Every kanban-root variable points at the sandbox; the two
    pre-resolved project exports are cleared, not repointed."""
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_DEV_TREE_PATH", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    env["KANBAN_ROOT"] = str(kanban_root)
    env["TEAM_ROOT"] = str(kanban_root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_DISCOVERY_REJECT_THRESHOLD"] = "3"
    env["PGAI_DEFERRED_BUGS"] = "false"
    return env


def _run_discovery(kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{PROJECT_A}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_sandbox_env(kanban_root),
        cwd=str(kanban_root),
        timeout=60,
    )


def _run_item_scan(kanban_root: pathlib.Path, bugs_dir: pathlib.Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        _disc_find_unhandled_items "{bugs_dir}" "{bugs_dir.parent / 'tasks' / 'queues' / 'bug_backlog.md'}" "BUG-"
    """)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_sandbox_env(kanban_root),
        timeout=60,
    )


def _status_of(path: pathlib.Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().lower() == "## status")
    return next(line.strip().lower() for line in lines[index + 1:] if line.strip())


def _write_bug(bugs_dir: pathlib.Path, name: str) -> pathlib.Path:
    path = bugs_dir / name
    path.write_text(
        f"# {name[:-3]}\n\n**Severity:** medium\n\n## Status\nopen\n\n## Symptom\nsynthetic\n",
        encoding="utf-8",
    )
    return path


def _bundles(req_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(req_dir.glob("*-bugfix-bundle-*.md"))


def test_item_carried_by_a_release_is_offered_to_the_next_bundle(two_project_root: pathlib.Path) -> None:
    kanban_root = two_project_root
    project_root = kanban_root / "projects" / PROJECT_A
    bugs_dir = project_root / "bugs"
    req_dir = project_root / "requirements"
    tasks_dir = project_root / "tasks"
    bugs_dir.mkdir(exist_ok=True)
    req_dir.mkdir(exist_ok=True)
    fixed = _write_bug(bugs_dir, _FIXED)
    unfixed = _write_bug(bugs_dir, _UNFIXED)

    # 1. Bundle.
    first = _run_discovery(kanban_root)
    assert first.returncode == 0, f"stdout: {first.stdout}\nstderr: {first.stderr}"
    assert "DISCOVERY_STATUS=produced_work" in first.stdout, first.stdout + first.stderr
    [first_bundle] = _bundles(req_dir)
    first_text = first_bundle.read_text(encoding="utf-8")
    assert _FIXED in first_text and _UNFIXED in first_text, first_text
    assert _status_of(fixed) == "running"
    assert _status_of(unfixed) == "running"

    # Between bundle and release the bundle is in flight -- discovery sets
    # it 'running' when it queues PM -- and the scan offers neither item.
    in_flight_bundle, replaced = re.subn(
        r"^(##\s+Status\s*\n\s*)open\b", r"\1running", first_bundle.read_text(encoding="utf-8"), count=1, flags=re.M
    )
    assert replaced == 1, in_flight_bundle
    first_bundle.write_text(in_flight_bundle, encoding="utf-8")
    in_flight = _run_item_scan(kanban_root, bugs_dir)
    assert in_flight.returncode == 0, in_flight.stderr
    assert in_flight.stdout.strip() == "", in_flight.stdout

    # 2. Release: one DONE fix task that names the second bug only to say
    # it is not ticketed.
    task_dir = tasks_dir / "CODER-20990101-001-fix-thing"
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(
        "# Task: fix the thing\n\n## Goal\n"
        "Fix BUG-9201. Bundle item BUG-9202 is not ticketed in this plan.\n\n"
        "## Release Version\nv9.9.9\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text("## State\nDONE\n", encoding="utf-8")
    release = subprocess.run(
        [sys.executable, str(_PROMOTE_SCRIPT), str(first_bundle), "v9.9.9", str(tasks_dir), str(bugs_dir)],
        capture_output=True,
        text=True,
        env={**_sandbox_env(kanban_root), "PYTHONPATH": str(_TEAM_DIR)},
        timeout=60,
    )
    assert release.returncode == 0, release.stderr
    assert _status_of(fixed) == "done"
    assert _status_of(unfixed) == "running"
    assert _status_of(first_bundle) == "done"
    assert f"carried (no DONE task claims it): {_UNFIXED}" in release.stdout, release.stdout
    assert "the next discovery pass offers each as a candidate" in release.stdout, release.stdout

    # 3. The next discovery pass: the scan offers the carried bug only ...
    scan = _run_item_scan(kanban_root, bugs_dir)
    assert scan.returncode == 0, scan.stderr
    assert scan.stdout.splitlines() == [str(unfixed.resolve())], scan.stdout + scan.stderr

    # ... and the pipeline bundles it into the next bundle.
    second = _run_discovery(kanban_root)
    assert second.returncode == 0, f"stdout: {second.stdout}\nstderr: {second.stderr}"
    assert "DISCOVERY_STATUS=produced_work" in second.stdout, second.stdout + second.stderr
    new_bundles = [b for b in _bundles(req_dir) if b != first_bundle]
    assert len(new_bundles) == 1, new_bundles
    second_text = new_bundles[0].read_text(encoding="utf-8")
    assert _UNFIXED in second_text, second_text
    assert _FIXED not in second_text, second_text

    # Once the next bundle lists it, the item is in flight again.
    again = _run_item_scan(kanban_root, bugs_dir)
    assert again.stdout.strip() == "", again.stdout


def test_backlog_cache_reopens_a_carried_bug(tmp_path: pathlib.Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bug_scanner_carried_items", _TEAM_DIR / "pm-agent" / "lib" / "bug_scanner.py"
    )
    bug_scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bug_scanner)

    project = tmp_path / "project"
    bugs_dir = project / "bugs"
    req_dir = project / "requirements"
    bugs_dir.mkdir(parents=True)
    req_dir.mkdir()
    carried = _write_bug(bugs_dir, _UNFIXED)
    in_flight = _write_bug(bugs_dir, "BUG-9203-in-flight-thing.md")
    never_listed = _write_bug(bugs_dir, "BUG-9204-never-listed-thing.md")
    for path in (carried, in_flight, never_listed):
        path.write_text(path.read_text(encoding="utf-8").replace("## Status\nopen", "## Status\nrunning"), encoding="utf-8")
    (req_dir / "v9.9.8-bugfix-bundle-a.md").write_text(
        f"## Status\ndone\n\n## Bundled Items\n\n- {_UNFIXED} (`{carried}`)\n- {in_flight.name} (`{in_flight}`)\n",
        encoding="utf-8",
    )
    (req_dir / "v9.9.9-bugfix-bundle-b.md").write_text(
        f"## Status\nrunning\n\n## Bundled Items\n\n- {in_flight.name} (`{in_flight}`)\n",
        encoding="utf-8",
    )
    cache = project / "tasks" / "queues" / "bug_backlog.md"

    unbundled = bug_scanner.get_unbundled_bugs(str(bugs_dir), str(cache))

    assert [bug["id"] for bug in unbundled] == ["BUG-9202-unticketed-thing"]
    cache_text = cache.read_text(encoding="utf-8")
    assert "- [ ] BUG-9202-unticketed-thing" in cache_text
    assert "- [x] BUG-9203-in-flight-thing" in cache_text
    assert "- [x] BUG-9204-never-listed-thing" in cache_text

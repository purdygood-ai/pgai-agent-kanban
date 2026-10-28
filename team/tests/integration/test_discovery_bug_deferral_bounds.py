"""
test_discovery_bug_deferral_bounds.py
======================================
Integration tests for the deferral bounds in discovery's bug step
(_disc_bug_deferral_filter in team/scripts/lib/discovery_defer.sh, run by
discovery_step_bugs): [release] deferred_max_count and
deferred_max_age_days open a bugfix bundle of the deferred bugs even though
no bug is above deferred_bug_level and the requirements queue is not empty.

Coverage:
  - Count: 6 low bugs at level=medium with deferred_max_count=5 -> one
    bundle listing all six, narrated naming the count bound; the same
    fixture with 5 bugs -> no bundle and the existing "hygiene sweep
    withheld" line.
  - Age: the oldest deferred bug's Date is older than
    deferred_max_age_days -> one bundle, narrated naming the age bound;
    every Date inside the bound -> no bundle.
  - A bug with no Date header is narrated by name and never fires the age
    bound.

Every fixture keeps an unprocessed requirements file queued, so the hygiene
sweep can never be what opens the bundle.

This file keeps its own local copies of the _run_discovery / _add_bug_file /
_add_requirements_file helper shapes used by
test_discovery_bug_deferral_gate.py (each integration test file owns its
helpers rather than importing another test module's private functions).

Design notes:
  - All tests use two_project_root (tmp_path-backed). No bare /tmp paths.
  - Fixture bug IDs are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import subprocess
import textwrap
from typing import Optional

from pgai_agent_kanban.cm.bundle_family import parse_bundled_item_basenames

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"


# ---------------------------------------------------------------------------
# Helpers (local to this module)
# ---------------------------------------------------------------------------


def _run_discovery(kanban_root: pathlib.Path, project_name: str) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree."""
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{project_name}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)
    env = dict(os.environ)
    env["KANBAN_ROOT"] = str(kanban_root)
    env["TEAM_ROOT"] = str(kanban_root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_PROJECT_NAME"] = project_name
    env.pop("PGAI_DEV_TREE_PATH", None)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_PROJECT_ROOT", None)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, env=env, cwd=str(kanban_root), timeout=120,
    )


def _today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str,
    slug: str,
    *,
    severity: str = "low",
    date: Optional[str] = "",
) -> pathlib.Path:
    """Create an open bug file. date="" stamps today (UTC); None omits the Date header."""
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    if date == "":
        date = _today().isoformat()
    date_line = f"**Date:** {date}\n" if date is not None else ""
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        "**Filed By:** tester\n"
        f"{date_line}"
        f"**Severity:** {severity}\n\n"
        "## Status\nopen\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    return bug_file


def _add_requirements_file(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / "v0.0.5-unrelated.md"
    req_file.write_text(
        "# v0.0.5: unrelated\n\n"
        "## Status\nopen\n\n"
        "## Target Version\nv0.0.5\n\n"
        "## Workflow Type\nrelease\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\nTest requirements file.\n",
        encoding="utf-8",
    )
    return req_file


def _install_pm_agent_stub(kanban_root: pathlib.Path, project_name: str, task_id: str) -> None:
    """A stub pm-agent.sh so the requirements step can queue PM without decomposing."""
    scripts_dir = kanban_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    pm_tasks_dir = kanban_root / "projects" / project_name / "tasks"
    pm_tasks_dir.mkdir(parents=True, exist_ok=True)
    stub_pm = scripts_dir / "pm-agent.sh"
    stub_pm.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            TASK_ID="{task_id}"
            TASK_DIR="{pm_tasks_dir}/${{TASK_ID}}"
            mkdir -p "$TASK_DIR"
            printf '# %s\\n## State\\nBACKLOG\\n' "$TASK_ID" > "$TASK_DIR/status.md"
            PM_BACKLOG="${{KANBAN_ROOT}}/projects/${{PGAI_PROJECT_NAME:-{project_name}}}/tasks/queues/pm_backlog.md"
            echo "- [ ] $TASK_ID" >> "$PM_BACKLOG"
            echo "  Folder   : $TASK_DIR"
        """),
        encoding="utf-8",
    )
    stub_pm.chmod(0o755)


def _set_release(kanban_root: pathlib.Path, project_name: str, **keys: str) -> None:
    """Append a [release] section to project.cfg with the given keys."""
    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    lines = ["", "[release]"] + [f"{key} = {value}" for key, value in keys.items()]
    with cfg_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _deferring_fixture(kanban_root: pathlib.Path, **bounds: str) -> None:
    """deferred_bugs=true, level=medium, the given bounds, a queued requirement."""
    _set_release(kanban_root, "project-a", deferred_bugs="true", deferred_bug_level="medium", **bounds)
    _add_requirements_file(kanban_root, "project-a")
    _install_pm_agent_stub(kanban_root, "project-a", "PM-20260909-001-decompose-v0-0-5-unrelated")


# Distinct slugs: ones differing only by a number would read as re-filed
# duplicates of each other and be closed by discovery's duplicate detector.
_SLUGS = ("stale-label", "wrong-colour", "typo-help", "loose-padding", "odd-sort", "dim-glyph")


def _bundle_files(kanban_root: pathlib.Path, project_name: str) -> list[pathlib.Path]:
    return list((kanban_root / "projects" / project_name / "requirements").glob("*-bugfix-bundle-*.md"))


def _bug_status(bug_file: pathlib.Path) -> str:
    lines = bug_file.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Status":
            return lines[i + 1].strip()
    return ""


# ---------------------------------------------------------------------------
# Count bound
# ---------------------------------------------------------------------------


class TestCountBound:
    def test_more_deferred_bugs_than_the_count_bound_open_a_bundle_of_all_of_them(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _deferring_fixture(root, deferred_max_count="5")
        bugs = [_add_bug_file(root, "project-a", f"BUG-900{i}", slug) for i, slug in enumerate(_SLUGS, 1)]

        result = _run_discovery(root, "project-a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project-a")
        assert len(bundles) == 1, f"Expected one bugfix bundle.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        for i in range(1, 7):
            assert any(f"BUG-900{i}" in item for item in item_ids), item_ids
        assert "deferral bound deferred_max_count=5 exceeded -- 6 bug(s) deferred" in result.stderr
        assert "deferral bound exceeded; bundling 6 deferred bug(s) held at level=medium" in result.stderr
        assert "hygiene sweep" not in result.stderr.lower()
        for bug in bugs:
            assert _bug_status(bug) == "running"

    def test_deferred_bugs_at_the_count_bound_stay_deferred_and_the_sweep_is_withheld(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _deferring_fixture(root, deferred_max_count="5")
        bugs = [_add_bug_file(root, "project-a", f"BUG-900{i}", slug) for i, slug in enumerate(_SLUGS[:5], 1)]

        result = _run_discovery(root, "project-a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project-a"), result.stderr
        assert "deferral bound" not in result.stderr
        assert "hygiene sweep withheld" in result.stderr.lower(), result.stderr
        for bug in bugs:
            assert _bug_status(bug) == "open"


# ---------------------------------------------------------------------------
# Age bound
# ---------------------------------------------------------------------------


class TestAgeBound:
    def test_an_oldest_deferred_bug_past_the_age_bound_opens_a_bundle(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _deferring_fixture(root, deferred_max_age_days="30")
        old_date = (_today() - datetime.timedelta(days=45)).isoformat()
        old = _add_bug_file(root, "project-a", "BUG-9001", "old", date=old_date)
        new = _add_bug_file(root, "project-a", "BUG-9002", "new")

        result = _run_discovery(root, "project-a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project-a")
        assert len(bundles) == 1, f"Expected one bugfix bundle.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-9001" in item for item in item_ids), item_ids
        assert any("BUG-9002" in item for item in item_ids), item_ids
        assert (
            f"deferral bound deferred_max_age_days=30 exceeded -- oldest deferred bug "
            f"BUG-9001-old dated {old_date} is 45 day(s) old"
        ) in result.stderr
        assert _bug_status(old) == "running"
        assert _bug_status(new) == "running"

    def test_every_date_inside_the_age_bound_opens_no_bundle(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _deferring_fixture(root, deferred_max_age_days="30")
        inside = (_today() - datetime.timedelta(days=30)).isoformat()
        bugs = [
            _add_bug_file(root, "project-a", "BUG-9001", "edge", date=inside),
            _add_bug_file(root, "project-a", "BUG-9002", "fresh"),
        ]

        result = _run_discovery(root, "project-a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project-a"), result.stderr
        assert "deferral bound" not in result.stderr
        assert "hygiene sweep withheld" in result.stderr.lower(), result.stderr
        for bug in bugs:
            assert _bug_status(bug) == "open"

    def test_an_undated_bug_is_narrated_and_never_fires_the_age_bound(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _deferring_fixture(root, deferred_max_age_days="0")
        undated = _add_bug_file(root, "project-a", "BUG-9001", "undated", date=None)

        result = _run_discovery(root, "project-a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project-a"), result.stderr
        assert "Date field in BUG-9001-undated.md is missing or unparseable" in result.stderr
        assert _bug_status(undated) == "open"

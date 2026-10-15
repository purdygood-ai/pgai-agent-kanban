"""
test_discovery_bug_deferral_gate.py
====================================
Integration tests for the discovery bug-step deferral gate
(team/scripts/lib/discovery_defer.sh, consumed by discovery_step_bugs in
team/scripts/lib/discovery_steps.sh).

Coverage:
  - deferred_bugs=false: today's behaviour, unchanged, no deferral line.
  - level=low with only low-severity bugs open: no bundle, files stay
    open, bug_backlog.md unchanged, narration names the count and level.
  - level=low with a medium-severity bug alongside low ones: only the
    medium bug is bundled; the low ones are left completely alone.
  - level=medium / level=high with a bug at that exact severity: deferred,
    no bundle.
  - An unprocessed requirements file is still picked up by the
    requirements step in every deferred state above.
  - Hygiene sweep: fires only when the bug queue holds no non-deferred bug
    AND the requirements queue holds no unprocessed requirement; withheld
    when a requirements file is still pending.
  - A bug file with a missing/unparseable severity field is never
    deferred, and the run narrates the file by name.
  - A '**Severity:** critical' bug file is ranked as its own rung, never
    reported as missing or unparseable, and is still selected as the
    bundling trigger at every accepted deferred_bug_level.

These tests exercise the real discovery pipeline against temporary kanban
trees, reusing the two_project_root fixture and the _run_discovery /
_add_bug_file / _add_requirements_file helper shapes established in
test_discovery_pipeline.py (each integration test file keeps its own local
copies of these helpers rather than importing another test module's
private functions -- see test_discovery_race_guard.py for the same
convention).

Design notes:
  - All tests use two_project_root (tmp_path-backed). No bare /tmp paths.
  - Test names describe the behaviour under test, not the ticket that
    motivated them (roles/TESTER.md Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap
from typing import Optional

from pgai_agent_kanban.cm.bundle_family import parse_bundled_item_basenames

# ---------------------------------------------------------------------------
# Path constants (resolve from this file's own location)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"


# ---------------------------------------------------------------------------
# Shared helpers (local to this module)
# ---------------------------------------------------------------------------


def _run_discovery(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    extra_env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    Echoes DISCOVERY_LAST_STATUS on stdout after the run.
    """
    import os

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

    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    if extra_env:
        base_env.update(extra_env)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=60,
    )


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str = "BUG-0001",
    slug: str = "test-fix",
    status: str = "open",
    severity: Optional[str] = "low",
) -> pathlib.Path:
    """Create a bug file. severity=None omits the **Severity:** field entirely."""
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    severity_line = f"**Severity:** {severity}\n\n" if severity is not None else ""
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        f"{severity_line}"
        f"## Status\n{status}\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    return bug_file


def _add_requirements_file(
    kanban_root: pathlib.Path,
    project_name: str,
    version: str = "v0.0.5",
    slug: str = "unrelated-feature",
    status: str = "open",
    workflow_type: str = "release",
) -> pathlib.Path:
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / f"{version}-{slug}.md"
    req_file.write_text(
        f"# {version}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        f"## Target Version\n{version}\n\n"
        f"## Workflow Type\n{workflow_type}\n\n"
        f"## PM Task\nnone\n\n"
        "## Summary\nTest requirements file.\n",
        encoding="utf-8",
    )
    return req_file


def _requirements_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    return kanban_root / "projects" / project_name / "requirements"


def _set_defer_posture(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    deferred_bugs: Optional[bool] = None,
    deferred_bug_level: Optional[str] = None,
    defer_piggyback: Optional[bool] = None,
) -> None:
    """Append a [release] section to project.cfg setting the deferral posture.

    Leaves project.cfg untouched for any field left as None (falls through
    to the resolver's kanban.cfg/default tiers, both of which resolve to
    deferred_bugs=false / deferred_bug_level=low / defer_piggyback=true on
    this fixture).
    """
    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    lines = ["", "[release]"]
    if deferred_bugs is not None:
        lines.append(f"deferred_bugs = {'true' if deferred_bugs else 'false'}")
    if deferred_bug_level is not None:
        lines.append(f"deferred_bug_level = {deferred_bug_level}")
    if defer_piggyback is not None:
        lines.append(f"defer_piggyback = {'true' if defer_piggyback else 'false'}")
    with cfg_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _install_pm_agent_stub(kanban_root: pathlib.Path, project_name: str, task_id: str) -> None:
    """Install a stub pm-agent.sh so discovery_step_requirements can queue PM
    without invoking the real decomposition pipeline.
    """
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


def _bundle_files(kanban_root: pathlib.Path, project_name: str) -> list[pathlib.Path]:
    return list(_requirements_dir(kanban_root, project_name).glob("*-bugfix-bundle-*.md"))


def _bug_status(bug_file: pathlib.Path) -> str:
    text = bug_file.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines()):
        if line.strip() == "## Status":
            return text.splitlines()[i + 1].strip()
    return ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeferralOff:
    def test_deferred_bugs_false_bundles_every_open_bug_without_deferral_narration(
        self, two_project_root: pathlib.Path
    ) -> None:
        """With deferred_bugs unset (defaults to false), the bug step behaves
        exactly as it does today: every open bug is bundled and no deferral
        line appears in the run's narration.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="low")
        _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="two", severity="high")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected a bug bundle; stderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids)
        assert any("BUG-0002" in i for i in item_ids)
        stderr_lower = result.stderr.lower()
        assert "deferring" not in stderr_lower and "hygiene sweep" not in stderr_lower, (
            f"Expected no deferral narration when deferred_bugs=false.\nstderr:\n{result.stderr}"
        )


class TestLevelLowDefersLowSeverityBugs:
    def test_low_severity_bugs_held_back_leave_no_bundle_and_narrate_the_count(
        self, two_project_root: pathlib.Path
    ) -> None:
        """level=low with only low-severity bugs open: no bundle is written,
        every bug file keeps ## Status: open, bug_backlog.md is unchanged, and
        the run narrates the count of deferred bugs and the resolved level.

        A pending requirements file is present so the hygiene sweep condition
        (both queues empty) is not satisfied -- this is also the AC5 pairing:
        the requirements step must still pick the file up for PM in the same
        run.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="low")
        bug1 = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="low")
        bug2 = _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="two", severity="low")
        _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a", "PM-20260909-001-decompose-v0-0-5-unrelated")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project_a"), (
            f"Expected no bug bundle while both bugs are deferred.\n"
            f"requirements/ contents: {list(_requirements_dir(root, 'project_a').iterdir())}\n"
            f"stderr:\n{result.stderr}"
        )
        assert _bug_status(bug1) == "open"
        assert _bug_status(bug2) == "open"

        bug_backlog = root / "projects" / "project_a" / "tasks" / "queues" / "bug_backlog.md"
        backlog_text = bug_backlog.read_text(encoding="utf-8")
        assert "[x]" not in backlog_text, f"bug_backlog.md should be unchanged:\n{backlog_text}"

        assert "deferring 2 bug(s) at or below level=low" in result.stderr, (
            f"Expected the run to narrate the deferred count and level.\nstderr:\n{result.stderr}"
        )

        # AC5: the requirements step still processes the pending file in the
        # same run, untouched by the deferral gate.
        req_file = _requirements_dir(root, "project_a") / "v0.0.5-unrelated.md"
        assert "running" in req_file.read_text(encoding="utf-8").lower(), (
            "Requirements step should still queue PM while bugs are deferred"
        )


class TestLevelLowStillBundlesAboveLevelSeverity:
    def test_medium_bug_bundles_while_low_bugs_alongside_it_stay_deferred(
        self, two_project_root: pathlib.Path
    ) -> None:
        """level=low with one medium-severity bug open alongside low ones: a
        bundle is written and only the medium bug is in it.

        defer_piggyback is pinned to false here: this test isolates the
        severity-partitioning decision (which items are "kept" vs
        "deferred") from the piggyback carry (which currently-deferred
        items ride along with a trigger) -- the piggyback carry itself is
        covered by test_discovery_bug_deferral_piggyback.py.
        """
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low", defer_piggyback=False
        )
        low1 = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="low")
        low2 = _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="two", severity="low")
        _add_bug_file(root, "project_a", bug_id="BUG-0003", slug="three", severity="medium")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected a bundle for the medium bug.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0003" in i for i in item_ids), item_ids
        assert not any("BUG-0001" in i for i in item_ids), item_ids
        assert not any("BUG-0002" in i for i in item_ids), item_ids

        assert _bug_status(low1) == "open"
        assert _bug_status(low2) == "open"


class TestLevelMediumAndHighDeferAtTheirOwnRung:
    def test_medium_severity_bug_deferred_at_level_medium(
        self, two_project_root: pathlib.Path
    ) -> None:
        """level=medium with a medium-severity bug open: the bug is deferred
        and no bundle is written; an unprocessed requirements file is still
        queued for PM (AC5).
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="medium")
        bug = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="medium")
        _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a", "PM-20260909-002-decompose-v0-0-5-unrelated")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project_a"), result.stderr
        assert _bug_status(bug) == "open"

        req_file = _requirements_dir(root, "project_a") / "v0.0.5-unrelated.md"
        assert "running" in req_file.read_text(encoding="utf-8").lower()

    def test_high_severity_bug_deferred_at_level_high(
        self, two_project_root: pathlib.Path
    ) -> None:
        """level=high with a high-severity bug open: the bug is deferred and
        no bundle is written; an unprocessed requirements file is still
        queued for PM (AC5). level=high is the operator-knows-best case: it
        defers every bug, by design.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="high")
        bug = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="high")
        _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a", "PM-20260909-003-decompose-v0-0-5-unrelated")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project_a"), result.stderr
        assert _bug_status(bug) == "open"

        req_file = _requirements_dir(root, "project_a") / "v0.0.5-unrelated.md"
        assert "running" in req_file.read_text(encoding="utf-8").lower()


class TestHygieneSweep:
    def test_sweep_bundles_deferred_bugs_once_bug_and_requirements_queues_are_both_empty(
        self, two_project_root: pathlib.Path
    ) -> None:
        """With deferred bugs held back and both the non-deferred bug queue
        and the requirements queue empty, the hygiene sweep bundles the
        deferred bugs.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="low")
        bug1 = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="low")
        bug2 = _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="two", severity="low")
        # No requirements file: requirements queue is empty.

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert "hygiene sweep" in result.stderr.lower(), (
            f"Expected the hygiene sweep to fire and narrate.\nstderr:\n{result.stderr}"
        )
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected the hygiene sweep to bundle the deferred bugs.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        assert any("BUG-0002" in i for i in item_ids), item_ids
        assert _bug_status(bug1) == "running"
        assert _bug_status(bug2) == "running"

    def test_sweep_is_withheld_while_an_unprocessed_requirement_is_present(
        self, two_project_root: pathlib.Path
    ) -> None:
        """The hygiene sweep does not fire when the requirements queue holds
        an unprocessed requirement, even though every open bug is deferred.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="low")
        bug1 = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="low")
        bug2 = _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="two", severity="low")
        _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a", "PM-20260909-004-decompose-v0-0-5-unrelated")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        # The withheld narration itself says "hygiene sweep withheld" -- assert
        # on the firing phrasing specifically ("hygiene sweep --") so this
        # assertion cannot be satisfied by the withheld line's own wording.
        assert "hygiene sweep --" not in result.stderr.lower(), (
            f"Hygiene sweep should be withheld while a requirement is pending.\nstderr:\n{result.stderr}"
        )
        assert "hygiene sweep withheld" in result.stderr.lower(), (
            f"Expected the run to narrate why the sweep did not fire.\nstderr:\n{result.stderr}"
        )
        assert not _bundle_files(root, "project_a"), result.stderr
        assert _bug_status(bug1) == "open"
        assert _bug_status(bug2) == "open"


class TestUnparseableSeverityIsNeverDeferred:
    def test_missing_severity_field_is_kept_and_the_run_names_the_file(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A bug file whose severity field is missing is not deferred -- it
        remains a candidate that can trigger an RC -- and the run narrates
        the unparseable field, naming the file.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="low")
        bug = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="no-severity", severity=None)

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected the unparseable-severity bug to be bundled, not deferred.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        assert bug.name.replace(".md", "") in result.stderr or "BUG-0001" in result.stderr, (
            f"Expected the run to narrate the file by name.\nstderr:\n{result.stderr}"
        )
        assert "unparseable" in result.stderr.lower()

    def test_unrecognised_severity_value_is_kept_and_the_run_names_the_file(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A severity value outside the recognised vocabulary (low/medium/
        high/critical) is treated as above the level -- never silently
        deferred.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="high")
        bug = _add_bug_file(
            root, "project_a", bug_id="BUG-0001", slug="urgent-bug", severity="urgent"
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected the unparseable-severity bug to be bundled, not deferred.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        assert "unparseable" in result.stderr.lower()


class TestCriticalSeverityIsRankedNotUnparseable:
    def test_critical_bug_is_the_bundling_trigger_with_no_unparseable_warning(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A correctly written '**Severity:** critical' bug file is ranked on
        purpose: it is selected as the bundling trigger at every accepted
        deferred_bug_level, and the run logs no 'missing or unparseable'
        warning for it. The guarded regression is a critical bug reaching
        the same functional outcome through the unparseable error path,
        narrated as if its severity field were broken.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="high")
        bug = _add_bug_file(
            root, "project_a", bug_id="BUG-0001", slug="critical-bug", severity="critical"
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected the critical bug to be bundled as the trigger.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        assert bug.exists()
        assert "missing or unparseable" not in result.stderr.lower(), (
            f"A correctly written critical severity field must never be reported "
            f"as missing or unparseable.\nstderr:\n{result.stderr}"
        )

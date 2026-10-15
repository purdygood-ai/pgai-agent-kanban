"""
test_discovery_bug_deferral_piggyback.py
=========================================
Integration tests for the discovery bug-step piggyback carry
(team/scripts/lib/discovery_defer.sh's _disc_bug_deferral_filter, consumed
by discovery_step_bugs in team/scripts/lib/discovery_steps.sh; the
ride-along suffix is rendered by _disc_write_bundle in
team/scripts/lib/discovery_state.sh).

Coverage:
  - defer_piggyback=true with a trigger present: every currently-deferred
    bug rides along in the same bundle, each annotated with the exact
    ride-along suffix team/roles/PM.md specifies, naming the trigger; the
    trigger's own line carries no annotation; the run narrates the count
    and the trigger's id and severity; all bundled items (trigger and
    ride-alongs alike) are marked [x] / ## Status: running.
  - defer_piggyback=false: the deferred bugs stay deferred even though a
    trigger is present -- today's pre-piggyback behaviour.
  - No trigger present (every open bug is deferred): piggyback never
    fires -- a deferred bug never becomes a trigger, so there is nothing
    to piggyback on; the hygiene sweep (covered by
    test_discovery_bug_deferral_gate.py) is the only other way deferred
    bugs get bundled.

This file reuses the two_project_root fixture and the _run_discovery /
_add_bug_file / _bundle_files / _bug_status helper shapes established in
test_discovery_bug_deferral_gate.py (each integration test file keeps its
own local copies of these helpers rather than importing another test
module's private functions -- see test_discovery_race_guard.py for the
same convention).

Design notes:
  - All tests use two_project_root (tmp_path-backed). No bare /tmp paths.
  - Test names describe the behaviour under test, not the ticket that
    motivated them (roles/TESTER.md Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap
from typing import Optional

from pgai_agent_kanban.cm.bundle_family import parse_bundled_item_basenames

# ---------------------------------------------------------------------------
# Path constants (resolve from this file's own location)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_PM_ROLE_FILE = _TEAM_DIR / "roles" / "PM.md"


def _ride_along_wording_from_pm_role_file() -> str:
    """Extract the backtick-quoted ride-along wording verbatim from
    team/roles/PM.md, so this test compares the producer's output against
    the actual source of truth rather than a hand-typed copy that could
    drift from it unnoticed.
    """
    text = _PM_ROLE_FILE.read_text(encoding="utf-8")
    m = re.search(r"`(hygiene ride-along \(piggyback on <TRIGGER-ID>\))`", text)
    assert m, (
        f"Could not find the ride-along wording in {_PM_ROLE_FILE}; "
        "has the 'Ride-alongs consolidate into at most two hygiene tickets' "
        "section been reworded?"
    )
    return m.group(1)


def _expected_ride_along_suffix(trigger_id: str) -> str:
    wording = _ride_along_wording_from_pm_role_file().replace("<TRIGGER-ID>", trigger_id)
    return f" — {wording}"


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
    to the resolver's kanban.cfg/default tiers).
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


def _bug_backlog_text(kanban_root: pathlib.Path, project_name: str) -> str:
    backlog = kanban_root / "projects" / project_name / "tasks" / "queues" / "bug_backlog.md"
    return backlog.read_text(encoding="utf-8") if backlog.exists() else ""


def _bundled_items_line_for(bundle_text: str, item_id: str) -> str:
    """Return the ## Bundled Items line naming <item_id> (without .md)."""
    for line in bundle_text.splitlines():
        if line.startswith("- ") and item_id in line:
            return line
    return ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPiggybackCarriesDeferredBugsOntoATrigger:
    def test_all_five_bugs_bundle_with_ride_along_suffix_on_the_four_low_ones(
        self, two_project_root: pathlib.Path
    ) -> None:
        """level=low, piggyback=true, one medium bug and four low bugs open:
        the bundle contains all five items; the medium bug's line carries no
        annotation and each of the four low lines ends with the exact
        ride-along suffix naming the medium bug's id. The narration names
        the count and the trigger. All five bugs end up marked [x] /
        ## Status: running.
        """
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low", defer_piggyback=True
        )
        trigger = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="trigger", severity="medium")
        low_bugs = [
            _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="low-a", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0003", slug="low-b", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0004", slug="low-c", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0005", slug="low-d", severity="low"),
        ]

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected a bundle for the trigger bug.\nstderr:\n{result.stderr}"
        bundle_text = bundles[0].read_text(encoding="utf-8")

        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        for low_bug in low_bugs:
            bug_id = low_bug.name.split("-")[0] + "-" + low_bug.name.split("-")[1]
            assert any(bug_id in i for i in item_ids), item_ids

        trigger_id = trigger.name.replace(".md", "")
        trigger_line = _bundled_items_line_for(bundle_text, "BUG-0001")
        assert trigger_line, bundle_text
        assert "hygiene ride-along" not in trigger_line, trigger_line

        expected_suffix = _expected_ride_along_suffix(trigger_id)
        for low_bug in low_bugs:
            item_id = low_bug.name.replace(".md", "")
            line = _bundled_items_line_for(bundle_text, item_id)
            assert line, f"Expected a bundled-items line for {item_id}.\n{bundle_text}"
            assert line.endswith(expected_suffix), (
                f"Line for {item_id} does not end with the exact ride-along suffix.\n"
                f"line: {line!r}\nexpected suffix: {expected_suffix!r}"
            )

        assert f"piggybacking 4 deferred bugs (trigger: {trigger_id} medium)" in result.stderr, (
            f"Expected the piggyback narration naming count/trigger/severity.\nstderr:\n{result.stderr}"
        )

        assert _bug_status(trigger) == "running"
        backlog_text = _bug_backlog_text(root, "project_a")
        assert "[x] BUG-0001" in backlog_text, backlog_text
        for low_bug in low_bugs:
            assert _bug_status(low_bug) == "running", low_bug
            item_id = low_bug.name.replace(".md", "")
            assert f"[x] {item_id}" in backlog_text, backlog_text

    def test_piggyback_false_leaves_deferred_bugs_untouched_while_trigger_bundles_alone(
        self, two_project_root: pathlib.Path
    ) -> None:
        """level=low, piggyback=false, one medium bug and four low bugs open:
        the bundle contains only the medium bug, and the four low bug files
        still read ## Status: open and are unmarked in the cache.
        """
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low", defer_piggyback=False
        )
        _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="trigger", severity="medium")
        low_bugs = [
            _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="low-a", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0003", slug="low-b", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0004", slug="low-c", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0005", slug="low-d", severity="low"),
        ]

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, result.stderr
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        for low_bug in low_bugs:
            item_id = low_bug.name.replace(".md", "")
            assert not any(item_id in i for i in item_ids), item_ids

        assert "piggybacking" not in result.stderr.lower(), result.stderr

        backlog_text = _bug_backlog_text(root, "project_a")
        for low_bug in low_bugs:
            assert _bug_status(low_bug) == "open", low_bug
            item_id = low_bug.name.replace(".md", "")
            assert f"[x] {item_id}" not in backlog_text, backlog_text


class TestPiggybackNeverFiresWithoutATrigger:
    def test_only_low_bugs_open_writes_no_bundle_even_with_piggyback_true(
        self, two_project_root: pathlib.Path
    ) -> None:
        """level=low, piggyback=true, only low bugs open: no bundle is
        written at all -- there is no trigger, so there is nothing to
        piggyback on. (The hygiene sweep, covered elsewhere, is the only
        other path that can bundle this litter.)
        """
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low", defer_piggyback=True
        )
        bug1 = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="low")
        bug2 = _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="two", severity="low")
        # Keep the requirements queue non-empty so the hygiene sweep is
        # withheld -- this test isolates "no trigger" from "hygiene sweep
        # fired instead," which is exercised by
        # test_discovery_bug_deferral_gate.py.
        req_dir = _requirements_dir(root, "project_a")
        req_dir.mkdir(parents=True, exist_ok=True)
        (req_dir / "v0.0.5-unrelated.md").write_text(
            "# v0.0.5: unrelated\n\n"
            "## Status\nopen\n\n"
            "## Target Version\nv0.0.5\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nTest requirements file.\n",
            encoding="utf-8",
        )
        _install_pm_agent_stub(root, "project_a", "PM-20260909-005-decompose-v0-0-5-unrelated")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project_a"), (
            f"Expected no bug bundle -- no trigger is present to piggyback on.\nstderr:\n{result.stderr}"
        )
        assert "piggybacking" not in result.stderr.lower(), result.stderr
        assert _bug_status(bug1) == "open"
        assert _bug_status(bug2) == "open"

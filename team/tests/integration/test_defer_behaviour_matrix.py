"""
test_defer_behaviour_matrix.py
================================
Closing-verification integration fixtures for the deferral behaviour
matrix stated verbatim in the release's acceptance section:

  level=low + a medium bug     -> a bundle opens; deferred lows ride
                                   along iff piggyback=true
  level=medium + a medium bug  -> deferred, no bundle
  level=high + any bug         -> deferred, no bundle

Every one of those states additionally asserts that an unprocessed
requirements file already sitting in requirements/ is still picked up
and queued for PM in the same discovery run -- deferral must never
touch the requirements step, in any of the four matrix cells.

This file drives the real discovery pipeline
(team/scripts/lib/discovery.sh) against a temporary project tree and
asserts on the bundle's actual contents and the bug files' actual
## Status values after the run, not on log narration alone (narration
is checked too, but only as a second signal, never the only one).

The campaign file's spend/resume arc, the undefer verb, and the
icon-truth agreement between the rendered surfaces and discovery's own
behaviour are exercised in their own files
(test_defer_campaign_lifecycle.py, test_defer_icon_truth.py) -- this
file is the level x severity x piggyback matrix alone.

This file keeps its own local copies of the _run_discovery /
_add_bug_file / _add_requirements_file helper shapes established in
test_discovery_pipeline.py, matching the convention already followed
by test_discovery_bug_deferral_gate.py and
test_discovery_bug_deferral_piggyback.py (each integration test file
owns its own helper shapes rather than importing another test module's
private functions -- see test_discovery_race_guard.py for the same
convention).

Design notes:
  - All tests use two_project_root (tmp_path-backed). No bare /tmp paths.
  - Test names describe the behaviour under test, not the ticket that
    motivated them (docs/test-authoring-guidelines.md, Anti-pattern 6).
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


def _finder_stdout_capture_fragment(capture_file: pathlib.Path) -> str:
    """Bash fragment that wraps _disc_find_unhandled_items so its raw stdout
    is copied, byte for byte, into *capture_file* on its way to the caller.

    The original function is kept under a new name via declare -f and the
    wrapper tees through it, so the pipeline still consumes exactly what the
    finder produced in THIS run -- which is what a failed bundle assertion
    needs to show: not a re-run of the finder afterwards, but the stdout the
    step actually read when it decided. Each call is preceded by a header
    line naming the directory scanned, so the bug-step and priority-step
    calls of one tick stay distinguishable.

    A function re-declared through eval no longer has discovery_items.sh as
    its BASH_SOURCE, and the finder self-locates team/ from that to import
    the ledger filename contract; the copy therefore has the literal library
    path substituted for ${BASH_SOURCE[0]} so it resolves exactly as the
    original does.
    """
    items_lib = _SCRIPTS_LIB / "discovery_items.sh"
    return textwrap.dedent(f"""\
        eval "_disc_find_unhandled_items_unwrapped() $(declare -f _disc_find_unhandled_items | sed '1d' | sed 's|\\${{BASH_SOURCE\\[0\\]}}|{items_lib}|g')"
        _disc_find_unhandled_items() {{
            printf -- '--- _disc_find_unhandled_items %s ---\\n' "$1" >> "{capture_file}"
            _disc_find_unhandled_items_unwrapped "$@" | tee -a "{capture_file}"
        }}
    """)


def _run_discovery(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    extra_env: Optional[dict] = None,
    finder_stdout_capture: Optional[pathlib.Path] = None,
) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    Echoes DISCOVERY_LAST_STATUS on stdout after the run.

    When *finder_stdout_capture* is given, the raw stdout of every
    _disc_find_unhandled_items call made during the run is appended to that
    file (see _finder_stdout_capture_fragment) so an assertion that fails on
    the bundle's absence can quote what the item scan actually returned.
    """
    import os

    capture_fragment = (
        _finder_stdout_capture_fragment(finder_stdout_capture)
        if finder_stdout_capture is not None
        else ""
    )
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
    """) + capture_fragment + textwrap.dedent(f"""\
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


def _run_discovery_until_requirements_decompose(
    kanban_root: pathlib.Path,
    project_name: str,
    req_file: pathlib.Path,
    *,
    max_ticks: int = 5,
) -> None:
    """Drive additional discovery ticks (each a full discovery_run_pipeline
    call) until <req_file> reads ## Status: running, or give up after
    <max_ticks>. Used by the matrix cells whose bug step opens a bundle:
    category-first ordering means requirements is reached only once the
    bug step itself produces no NEW work that tick, which — for a
    bundle-producing cell — is the tick right after the one that bundled
    (once the bundled item(s) are no longer open candidates).

    A bundle-producing cell's own bug bundle is ITSELF a requirements
    file, and its target version is always lower than a hand-authored
    "unrelated" fixture file's -- Step 3 always queues the lowest-version
    unprocessed file first, so the bug bundle's own PM task is queued
    ahead of <req_file>'s. discovery_step_requirements then finds that
    bundle's own PM task still open (not "[x]" in pm_backlog.md) and
    aborts its whole scan rather than moving on to <req_file> -- exactly
    as it would for two real, competing requirements files racing for
    the single PM slot. Between ticks this helper marks any still-open
    PM-STUB-* task DONE in BOTH places discovery consults (its own
    status.md AND its pm_backlog.md marker) so the next tick can reach
    <req_file> in turn -- simulating "PM finished decomposing the
    bundle" without running PM.
    """
    tasks_dir = kanban_root / "projects" / project_name / "tasks"
    pm_backlog = kanban_root / "projects" / project_name / "tasks" / "queues" / "pm_backlog.md"
    for _ in range(max_ticks):
        if "running" in req_file.read_text(encoding="utf-8").lower():
            return
        if tasks_dir.is_dir():
            for task_dir in sorted(tasks_dir.glob("PM-STUB-*")):
                status_file = task_dir / "status.md"
                if not status_file.exists():
                    continue
                if "BACKLOG" in status_file.read_text(encoding="utf-8"):
                    status_file.write_text(
                        f"# {task_dir.name}\n## State\nDONE\n", encoding="utf-8"
                    )
                    if pm_backlog.exists():
                        backlog_text = pm_backlog.read_text(encoding="utf-8")
                        backlog_text = backlog_text.replace(
                            f"[ ] {task_dir.name}", f"[x] {task_dir.name}"
                        )
                        pm_backlog.write_text(backlog_text, encoding="utf-8")
        result = _run_discovery(kanban_root, project_name)
        assert result.returncode == 0, result.stderr


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
    """Append a [release] section to project.cfg setting the deferral posture."""
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


def _install_pm_agent_stub(kanban_root: pathlib.Path, project_name: str) -> None:
    """Install a stub pm-agent.sh so discovery_step_requirements can queue PM
    without invoking the real decomposition pipeline.

    The stub derives its task ID from the requirements file basename
    discovery_step_requirements passes as $1 (real pm-agent.sh takes the
    same argument) -- one PM task per distinct requirements file, so a
    test that drives several ticks over several requirements files (a
    bug-generated bundle AND a pre-existing, independent file) gets a
    distinct, inspectable task per file rather than a single fixed ID
    colliding across them. See _pm_stub_task_id / _mark_pm_task_done below.
    """
    scripts_dir = kanban_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    pm_tasks_dir = kanban_root / "projects" / project_name / "tasks"
    pm_tasks_dir.mkdir(parents=True, exist_ok=True)

    stub_pm = scripts_dir / "pm-agent.sh"
    stub_pm.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            REQ_FILE="$1"
            SLUG="$(basename "$REQ_FILE" .md)"
            TASK_ID="PM-STUB-${{SLUG}}"
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
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Status":
            return lines[i + 1].strip()
    return ""


def _bundled_items_line_for(bundle_text: str, item_id: str) -> str:
    """Return the ## Bundled Items line naming <item_id> (without .md)."""
    for line in bundle_text.splitlines():
        if line.startswith("- ") and item_id in line:
            return line
    return ""


def _assert_requirements_file_still_decomposes(req_file: pathlib.Path, matrix_cell: str) -> None:
    """AC5: an unprocessed requirements file present in requirements/ is
    still picked up and queued for PM (## Status -> running), unimpeded by
    deferral. Discovery's own category-first ordering ("bugs take priority
    over priorities and requirements regardless of version numbers" --
    team/scripts/lib/discovery.sh's pipeline header) means requirements is
    only reached in the SAME tick as the bug step when the bug step
    produced no new bundle that tick (a deferred-with-no-bundle cell); a
    cell whose bug step DOES open a bundle reaches requirements on the
    NEXT tick instead, once the bundled item(s) are no longer open
    candidates -- see _run_discovery_until_requirements_decompose below,
    which drives however many ticks that takes and is what the
    bundle-producing matrix cells call.
    """
    text = req_file.read_text(encoding="utf-8").lower()
    assert "running" in text, (
        f"[{matrix_cell}] Expected the unprocessed requirements file to still "
        f"decompose normally while bugs were deferred.\nfile contents:\n{text}"
    )


# ---------------------------------------------------------------------------
# Matrix cell 1: level=low + medium bug, piggyback=true
# -> one bundle: the medium bug plus every deferred low, each low carrying
#    the exact ride-along annotation.
# ---------------------------------------------------------------------------


class TestLevelLowMediumBugPiggybackTrue:
    def test_bundle_carries_the_trigger_and_every_deferred_low_with_ride_along_annotation(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low", defer_piggyback=True
        )
        trigger = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="trigger", severity="medium")
        low_bugs = [
            _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="low-a", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0003", slug="low-b", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0004", slug="low-c", severity="low"),
        ]
        req_file = _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a")
        finder_stdout = root / "finder-stdout.log"

        result = _run_discovery(root, "project_a", finder_stdout_capture=finder_stdout)

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        finder_stdout_text = (
            finder_stdout.read_text(encoding="utf-8") if finder_stdout.exists() else ""
        )
        assert bundles, (
            "Expected one bundle carrying the trigger.\n"
            f"stderr:\n{result.stderr}\n"
            f"item scan raw stdout (repr, one call per '---' header):\n{finder_stdout_text!r}"
        )
        bundle_text = bundles[0].read_text(encoding="utf-8")

        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        for low_bug in low_bugs:
            item_id = low_bug.name.replace(".md", "")
            assert any(item_id in i for i in item_ids), item_ids

        trigger_id = trigger.name.replace(".md", "")
        trigger_line = _bundled_items_line_for(bundle_text, "BUG-0001")
        assert trigger_line, bundle_text
        assert "hygiene ride-along" not in trigger_line, (
            f"The trigger's own line must carry no annotation.\n{trigger_line}"
        )

        expected_suffix = _expected_ride_along_suffix(trigger_id)
        for low_bug in low_bugs:
            item_id = low_bug.name.replace(".md", "")
            line = _bundled_items_line_for(bundle_text, item_id)
            assert line, f"Expected a bundled-items line for {item_id}.\n{bundle_text}"
            assert line.endswith(expected_suffix), (
                f"Line for {item_id} does not carry the exact ride-along annotation.\n"
                f"line: {line!r}\nexpected suffix: {expected_suffix!r}"
            )

        assert _bug_status(trigger) == "running"
        for low_bug in low_bugs:
            assert _bug_status(low_bug) == "running", low_bug

        _run_discovery_until_requirements_decompose(root, "project_a", req_file)
        _assert_requirements_file_still_decomposes(req_file, "low+medium+piggyback=true")


# ---------------------------------------------------------------------------
# Matrix cell 2: level=low + medium bug, piggyback=false
# -> bundle contains only the medium bug; the low bug files stay open and
#    uncached.
# ---------------------------------------------------------------------------


class TestLevelLowMediumBugPiggybackFalse:
    def test_bundle_carries_only_the_trigger_while_low_bugs_stay_open_and_uncached(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _set_defer_posture(
            root, "project_a", deferred_bugs=True, deferred_bug_level="low", defer_piggyback=False
        )
        _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="trigger", severity="medium")
        low_bugs = [
            _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="low-a", severity="low"),
            _add_bug_file(root, "project_a", bug_id="BUG-0003", slug="low-b", severity="low"),
        ]
        req_file = _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected a bundle for the trigger bug alone.\nstderr:\n{result.stderr}"
        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        assert any("BUG-0001" in i for i in item_ids), item_ids
        for low_bug in low_bugs:
            item_id = low_bug.name.replace(".md", "")
            assert not any(item_id in i for i in item_ids), (
                f"{item_id} must not ride along when piggyback=false.\n{item_ids}"
            )

        backlog_text = (
            root / "projects" / "project_a" / "tasks" / "queues" / "bug_backlog.md"
        ).read_text(encoding="utf-8")
        for low_bug in low_bugs:
            assert _bug_status(low_bug) == "open", low_bug
            item_id = low_bug.name.replace(".md", "")
            assert f"[x] {item_id}" not in backlog_text, (
                f"{item_id} must remain uncached in bug_backlog.md while deferred.\n{backlog_text}"
            )

        _run_discovery_until_requirements_decompose(root, "project_a", req_file)
        _assert_requirements_file_still_decomposes(req_file, "low+medium+piggyback=false")


# ---------------------------------------------------------------------------
# Matrix cell 3: level=medium + medium bug -> deferred, no bundle.
# ---------------------------------------------------------------------------


class TestLevelMediumMediumBug:
    def test_medium_bug_defers_and_opens_no_bundle(self, two_project_root: pathlib.Path) -> None:
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="medium")
        bug = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="medium")
        req_file = _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project_a"), (
            f"level=medium with a medium bug must open no bundle.\nstderr:\n{result.stderr}"
        )
        assert _bug_status(bug) == "open"

        _assert_requirements_file_still_decomposes(req_file, "medium+medium")


# ---------------------------------------------------------------------------
# Matrix cell 4: level=high + any bug -> deferred, no bundle.
# ---------------------------------------------------------------------------


class TestLevelHighAnyBug:
    def test_high_severity_bug_defers_and_opens_no_bundle(self, two_project_root: pathlib.Path) -> None:
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="high")
        bug = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="one", severity="high")
        req_file = _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        _install_pm_agent_stub(root, "project_a")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project_a"), (
            f"level=high defers every bug -- no bundle should open.\nstderr:\n{result.stderr}"
        )
        assert _bug_status(bug) == "open"

        _assert_requirements_file_still_decomposes(req_file, "high+any")

    def test_low_severity_bug_also_defers_at_level_high(self, two_project_root: pathlib.Path) -> None:
        """level=high defers ALL bugs, including a low-severity one -- the
        'operator-knows-best' consequence documented beside the key.
        """
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="high")
        bug = _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="two", severity="low")
        req_file = _add_requirements_file(root, "project_a", version="v0.0.6", slug="unrelated")
        _install_pm_agent_stub(root, "project_a")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, result.stderr
        assert not _bundle_files(root, "project_a"), result.stderr
        assert _bug_status(bug) == "open"

        _assert_requirements_file_still_decomposes(req_file, "high+low")

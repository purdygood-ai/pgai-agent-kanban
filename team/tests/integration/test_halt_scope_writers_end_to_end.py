"""
test_halt_scope_writers_end_to_end.py
=======================================
End-to-end behavioral coverage: a project-scoped autonomous repair must
never park a sibling project's chain, and an operator-placed fleet-wide
HALT must still stop every project.

Uses the shared two_project_root fixture (team/tests/fixtures/two_project.py)
so both guarantees are exercised against a real, throwaway, two-project
kanban root — no seam is mocked.

Coverage:

  1. A project-scoped repair via overwatch_halt_first_fix (the real
     autonomous repair wrapper, real per-project lock, real HALT sentinel)
     leaves a sibling project's discovery pipeline able to run to
     completion WHILE the repair's own project is halted — asserted from
     inside the wrapped mutation's own execution window, and via the
     shared reader (pp_project_halted) both wake dispatch and discovery.sh
     use.
  2. The fleet-wide $KANBAN_ROOT/HALT is never touched by that repair, at
     any point (during the mutation or after cleanup).
  3. An operator-placed fleet-wide HALT still blocks every registered
     project's discovery pipeline (the audit did not weaken the fleet
     stop) — the companion guarantee, asserted against the same fixture.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

# ---------------------------------------------------------------------------
# Path to the dev tree (three levels up from team/tests/integration/).
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"


# ---------------------------------------------------------------------------
# Helpers (same shape as test_cross_cutting.py's file-scoped helpers)
# ---------------------------------------------------------------------------


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str = "BUG-0001",
    slug: str = "test-fix",
    status: str = "open",
) -> pathlib.Path:
    """Create a well-formed bug file in the project's bugs/ directory."""
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    return bug_file


def _requirements_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    return kanban_root / "projects" / project_name / "requirements"


def _run_discovery(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    threshold: int = 3,
) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree (standalone
    subprocess — used for the operator-HALT companion test, which does not
    need to run inside an overwatch_halt_first_fix mutation window)."""
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{project_name}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
        """
    )
    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    base_env["PGAI_DISCOVERY_REJECT_THRESHOLD"] = str(threshold)
    base_env.pop("PGAI_DEV_TREE_PATH", None)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=60,
    )


# ---------------------------------------------------------------------------
# AC1 + AC2 (mutation-window half) — project-scoped repair leaves the
# sibling project's chain able to run, and never touches the root HALT.
# ---------------------------------------------------------------------------


def test_project_scoped_repair_leaves_sibling_project_running(
    two_project_root: pathlib.Path,
) -> None:
    """overwatch_halt_first_fix repairing project_a must not block project_b.

    Runs the REAL wrapper (real per-project lock, real HALT sentinel
    write/restore) with OVERWATCH_PROJECT=project_a, and from inside the
    wrapped mutation itself: (1) confirms project_a reads as halted via the
    same reader wake dispatch and discovery.sh use (pp_project_halted),
    (2) confirms project_b reads as NOT halted, (3) actually runs
    project_b's discovery pipeline to completion and confirms it bundled
    its pending bug -- proof project_b's chain can run end to end during
    project_a's repair window, not just that a flag file is absent.
    """
    root = two_project_root
    _add_bug_file(root, "project_b", bug_id="BUG-0201", slug="sibling-runs-during-repair")

    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        source "{_SCRIPTS_LIB}/overwatch_lib.sh"
        source "{_SCRIPTS_LIB}/overwatch_protocol.sh"

        export KANBAN_ROOT="{root}"
        export TEAM_ROOT="{root}"
        export OVERWATCH_PROJECT="project_a"

        _probe_and_run_sibling() {{
            if pp_project_halted "project_a"; then
                echo "PROJECT_A_HALTED=yes"
            else
                echo "PROJECT_A_HALTED=no"
            fi
            if pp_project_halted "project_b"; then
                echo "PROJECT_B_HALTED=yes"
            else
                echo "PROJECT_B_HALTED=no"
            fi
            if [[ -f "$KANBAN_ROOT/HALT" ]]; then
                echo "ROOT_HALT_DURING=yes"
            else
                echo "ROOT_HALT_DURING=no"
            fi
            discovery_run_pipeline "project_b"
            echo "PROJECT_B_DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
        }}

        overwatch_halt_first_fix _probe_and_run_sibling
        echo "WRAPPER_EXIT=$?"

        if [[ -f "$KANBAN_ROOT/HALT" ]]; then
            echo "ROOT_HALT_AFTER=yes"
        else
            echo "ROOT_HALT_AFTER=no"
        fi
        if [[ -f "$KANBAN_ROOT/projects/project_a/HALT" ]]; then
            echo "PROJECT_A_HALT_AFTER=yes"
        else
            echo "PROJECT_A_HALT_AFTER=no"
        fi
        """
    )

    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(root)
    base_env["TEAM_ROOT"] = str(root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    base_env["PGAI_DISCOVERY_REJECT_THRESHOLD"] = "3"
    base_env.pop("PGAI_DEV_TREE_PATH", None)

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(root),
        timeout=60,
    )

    assert result.returncode == 0, (
        f"Repair script exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    out = result.stdout

    assert "PROJECT_A_HALTED=yes" in out, out
    assert "PROJECT_B_HALTED=no" in out, out
    assert "ROOT_HALT_DURING=no" in out, (
        f"The fleet-wide root HALT must never exist during a single-project "
        f"repair.\nstdout: {out}"
    )
    assert "PROJECT_B_DISCOVERY_STATUS=produced_work" in out, (
        f"project_b's discovery pipeline must run to completion and bundle "
        f"its pending bug while project_a is being repaired.\nstdout: {out}"
    )
    bundles_b = list(_requirements_dir(root, "project_b").glob("*-bugfix-bundle-*.md"))
    assert bundles_b, f"Expected project_b to have bundled its bug.\nstdout: {out}"

    assert "WRAPPER_EXIT=0" in out, out
    assert "ROOT_HALT_AFTER=no" in out, (
        f"The fleet-wide root HALT must not exist after the repair completes "
        f"either.\nstdout: {out}"
    )
    assert "PROJECT_A_HALT_AFTER=no" in out, (
        f"The repair must remove the project-scoped HALT it created once "
        f"done.\nstdout: {out}"
    )


# ---------------------------------------------------------------------------
# AC5 — an operator-placed root HALT still stops every project.
# ---------------------------------------------------------------------------


def test_operator_root_halt_still_blocks_every_project(
    two_project_root: pathlib.Path,
) -> None:
    """A HALT file at the kanban root blocks discovery for both projects.

    Companion to the repair test above: proves the audit did not weaken
    the fleet-wide stop while narrowing the autonomous repair path's own
    write target.
    """
    root = two_project_root
    _add_bug_file(root, "project_a", bug_id="BUG-0301", slug="root-halt-blocks-a")
    _add_bug_file(root, "project_b", bug_id="BUG-0302", slug="root-halt-blocks-b")

    (root / "HALT").write_text(
        "operator-placed fleet-wide halt for end-to-end test\n", encoding="utf-8"
    )

    result_a = _run_discovery(root, "project_a")
    result_b = _run_discovery(root, "project_b")

    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    assert "DISCOVERY_STATUS=blocked" in result_a.stdout, (
        f"Expected project_a blocked by the root HALT.\nstdout: {result_a.stdout}"
    )
    assert "DISCOVERY_STATUS=blocked" in result_b.stdout, (
        f"Expected project_b blocked by the root HALT too.\nstdout: {result_b.stdout}"
    )
    assert not list(_requirements_dir(root, "project_a").glob("*-bugfix-bundle-*.md"))
    req_b = _requirements_dir(root, "project_b")
    assert not (req_b.exists() and list(req_b.glob("*-bugfix-bundle-*.md")))

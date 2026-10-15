"""
test_defer_campaign_lifecycle.py
==================================
Integration fixtures for the DEFER-LOW-BUGS campaign file's whole life:
armed (team/scripts/defer.sh), honoured (the real discovery pipeline
holds the deferred bug back), spent by a qualifying ship
(team/scripts/lib/cm/release_campaign_spend.sh), and the deferral
posture resuming from the cfg tier with no operator action -- plus the
undefer verb (team/scripts/undefer.sh) lifting deferral early and the
previously held bug becoming eligible again on the very next discovery
iteration.

Both fixtures run their arc end to end in one test each (arm -> confirm
honoured -> act -> confirm again). Unlike
team/tests/unit/test_release_campaign_spend.py (which hand-writes the
campaign file's body directly), this file arms the file through the
real team/scripts/defer.sh CLI and drives the real discovery pipeline
against it -- the integration half of the same behaviour the unit
suite covers in isolation.

This file keeps its own local copies of the discovery-driving helper
shapes established in test_discovery_bug_deferral_gate.py -- each
integration test file owns its own helper shapes rather than importing
another module's private functions.

Design notes:
  - All tests use two_project_root (tmp_path-backed). No bare /tmp paths.
  - Test names describe the behaviour under test, not the ticket that
    motivated them (docs/test-authoring-guidelines.md, Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap
from typing import Optional

# ---------------------------------------------------------------------------
# Path constants (resolve from this file's own location)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_SCRIPTS_LIB = _SCRIPTS_DIR / "lib"
_DEFER_SH = _SCRIPTS_DIR / "defer.sh"
_UNDEFER_SH = _SCRIPTS_DIR / "undefer.sh"
_WHY_SH = _SCRIPTS_DIR / "why.sh"
_CAMPAIGN_SPEND_LIB = _SCRIPTS_LIB / "cm" / "release_campaign_spend.sh"


# ---------------------------------------------------------------------------
# Shared helpers (local to this module)
# ---------------------------------------------------------------------------


def _operator_env(kanban_root: pathlib.Path, project_name: str) -> dict:
    env = dict(os.environ)
    env["KANBAN_ROOT"] = str(kanban_root)
    env["TEAM_ROOT"] = str(kanban_root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_PROJECT_NAME"] = project_name
    env.pop("PGAI_DEV_TREE_PATH", None)
    return env


def _run_defer(kanban_root: pathlib.Path, project_name: str, until: str) -> subprocess.CompletedProcess:
    """Arm the campaign via the real defer.sh CLI."""
    return subprocess.run(
        ["bash", str(_DEFER_SH), "--project", project_name, "--until", until],
        capture_output=True,
        text=True,
        env=_operator_env(kanban_root, project_name),
        cwd=str(kanban_root),
        timeout=30,
    )


def _run_undefer(kanban_root: pathlib.Path, project_name: str) -> subprocess.CompletedProcess:
    """Clear the campaign via the real undefer.sh CLI."""
    return subprocess.run(
        ["bash", str(_UNDEFER_SH), "--project", project_name],
        capture_output=True,
        text=True,
        env=_operator_env(kanban_root, project_name),
        cwd=str(kanban_root),
        timeout=30,
    )


def _run_why(kanban_root: pathlib.Path, project_name: str) -> subprocess.CompletedProcess:
    """Read the resolved posture via the real why.sh CLI.

    --details is required since the v2.15.0 verdict-engine change: the four
    deferral-posture lines this helper reads moved behind that flag, and
    `why.sh --project NAME` alone now prints a one-line lane verdict
    instead (team/pgai_agent_kanban/ops/verdict.py).
    """
    return subprocess.run(
        ["bash", str(_WHY_SH), "--project", project_name, "--details"],
        capture_output=True,
        text=True,
        env=_operator_env(kanban_root, project_name),
        cwd=str(kanban_root),
        timeout=30,
    )


def _spend_campaign_file(
    kanban_root: pathlib.Path,
    project_name: str,
    shipped_version: str,
    project_root: pathlib.Path,
    release_state: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Ship a release at <shipped_version>, driving the real
    cm_release_spend_campaign_file production entry point -- the same
    function team/scripts/lib/cm/release_finalize.sh's Step 12 calls at
    ship time (see test_release_campaign_spend.py for the unit-level
    coverage of this same function against a hand-authored file; here it
    runs against a file armed by the real defer.sh CLI).
    """
    script = (
        f"source {str(_CAMPAIGN_SPEND_LIB)!r} && "
        f"cm_release_spend_campaign_file {project_name!r} {shipped_version!r} "
        f"{str(project_root)!r} {str(release_state)!r}"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_operator_env(kanban_root, project_name),
        cwd=str(kanban_root),
        timeout=30,
    )


def _run_discovery(kanban_root: pathlib.Path, project_name: str) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    Echoes DISCOVERY_LAST_STATUS on stdout after the run.
    """
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
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_operator_env(kanban_root, project_name),
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
) -> pathlib.Path:
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / f"{version}-{slug}.md"
    req_file.write_text(
        f"# {version}: {slug}\n\n"
        "## Status\nopen\n\n"
        f"## Target Version\n{version}\n\n"
        "## Workflow Type\nrelease\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\nTest requirements file.\n",
        encoding="utf-8",
    )
    return req_file


def _bundle_files(kanban_root: pathlib.Path, project_name: str) -> list[pathlib.Path]:
    return list(
        (kanban_root / "projects" / project_name / "requirements").glob("*-bugfix-bundle-*.md")
    )


def _bug_status(bug_file: pathlib.Path) -> str:
    text = bug_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Status":
            return lines[i + 1].strip()
    return ""


def _why_fields(result: subprocess.CompletedProcess) -> dict:
    """Parse why.sh's stdout ("field=value (tier)" per line) into a dict of
    field -> (value, tier).
    """
    fields: dict[str, tuple] = {}
    for line in result.stdout.strip().splitlines():
        if "=" not in line:
            continue
        field, rest = line.split("=", 1)
        rest = rest.strip()
        if rest.endswith(")") and " (" in rest:
            value, tier = rest.rsplit(" (", 1)
            tier = tier.rstrip(")")
        else:
            value, tier = rest, ""
        fields[field] = (value, tier)
    return fields


# ---------------------------------------------------------------------------
# The campaign file's whole life: armed -> honoured -> spent -> resumed.
# ---------------------------------------------------------------------------


class TestCampaignFileSpendArc:
    def test_arming_honouring_spending_and_resuming_all_happen_in_one_run(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        project_root = root / "projects" / "project_a"
        release_state = project_root / "release-state.md"
        campaign_file = project_root / "DEFER-LOW-BUGS"

        # --- Armed: defer.sh writes a real campaign file. ---
        arm_result = _run_defer(root, "project_a", "v0.1.0")
        assert arm_result.returncode == 0, arm_result.stderr
        assert campaign_file.exists(), "defer.sh should have written the campaign file"
        assert "until=v0.1.0" in campaign_file.read_text(encoding="utf-8")

        # --- Honoured: discovery holds the low bug back while the
        #     campaign forces deferred_bugs=true, deferred_bug_level=low.
        #     A pending requirements file withholds the hygiene sweep, so
        #     this run isolates "deferral is honoured" from "the sweep
        #     also bundles the litter" -- the sweep path is covered by
        #     test_discovery_bug_deferral_gate.py. ---
        bug = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="low-one", severity="low")
        _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")
        disc_result = _run_discovery(root, "project_a")
        assert disc_result.returncode == 0, disc_result.stderr
        assert not _bundle_files(root, "project_a"), (
            f"The armed campaign should defer the low bug -- no bundle expected.\n"
            f"stderr:\n{disc_result.stderr}"
        )
        assert _bug_status(bug) == "open"

        why_before = _run_why(root, "project_a")
        # project_a has no non-terminal task in this fixture, so the
        # resolved verdict is legitimately IDLE (exit 2) -- --details exits
        # with that verdict's own code rather than always 0; != 1 is the
        # correct check since a rendering refusal, not a specific verdict,
        # is what this assertion guards against.
        assert why_before.returncode != 1, why_before.stderr
        fields_before = _why_fields(why_before)
        assert fields_before["deferred_bugs"] == ("true", "campaign file")
        assert fields_before["until"][0] == "v0.1.0"

        # --- Spent: ship a release at or past the until target through
        #     the real production spend entry point. ---
        spend_result = _spend_campaign_file(
            root, "project_a", "v0.1.0", project_root, release_state
        )
        assert spend_result.returncode == 0, spend_result.stderr
        assert not campaign_file.exists(), "a qualifying ship should remove the campaign file"

        audit_text = release_state.read_text(encoding="utf-8")
        assert "## Campaign Spend Event" in audit_text
        assert "Until target satisfied: v0.1.0" in audit_text

        # --- Resumed: the posture now resolves from the cfg tier, no
        #     operator action taken beyond the ship itself. project.cfg
        #     carries no [release] section in this fixture, so the cfg
        #     tier IS the built-in default -- the why verb must say so
        #     rather than still claiming "campaign file". ---
        why_after = _run_why(root, "project_a")
        # See the why_before comment above: this fixture's project has no
        # non-terminal task, so IDLE (exit 2) is the legitimate verdict.
        assert why_after.returncode != 1, why_after.stderr
        fields_after = _why_fields(why_after)
        assert fields_after["deferred_bugs"] == ("false", "default"), fields_after
        assert fields_after["until"][0] == "(none)", fields_after
        assert fields_after["until"][1] == "default", fields_after


# ---------------------------------------------------------------------------
# The undefer verb: cleared early, previously-deferred bugs eligible again.
# ---------------------------------------------------------------------------


class TestUndeferLiftsDeferralEarly:
    def test_clearing_the_campaign_makes_the_previously_deferred_bug_eligible_on_the_next_tick(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        campaign_file = root / "projects" / "project_a" / "DEFER-LOW-BUGS"

        arm_result = _run_defer(root, "project_a", "v9.9.9")
        assert arm_result.returncode == 0, arm_result.stderr
        assert campaign_file.exists()

        bug = _add_bug_file(root, "project_a", bug_id="BUG-0001", slug="low-one", severity="low")
        # A pending requirements file withholds the hygiene sweep, isolating
        # "deferral is honoured" from "the sweep also bundles the litter" --
        # the sweep path is covered by test_discovery_bug_deferral_gate.py.
        _add_requirements_file(root, "project_a", version="v0.0.5", slug="unrelated")

        honoured_result = _run_discovery(root, "project_a")
        assert honoured_result.returncode == 0, honoured_result.stderr
        assert not _bundle_files(root, "project_a"), (
            f"The armed campaign should defer the low bug before undefer.\n"
            f"stderr:\n{honoured_result.stderr}"
        )
        assert _bug_status(bug) == "open"

        undefer_result = _run_undefer(root, "project_a")
        assert undefer_result.returncode == 0, undefer_result.stderr
        assert not campaign_file.exists(), "undefer.sh should have removed the campaign file"

        lifted_result = _run_discovery(root, "project_a")
        assert lifted_result.returncode == 0, lifted_result.stderr
        bundles = _bundle_files(root, "project_a")
        assert bundles, (
            f"Once undefer clears the campaign, project.cfg carries no explicit "
            f"[release] deferral -- deferred_bugs falls back to its default "
            f"'false', and the previously held bug must be bundled on the very "
            f"next discovery iteration with no further operator action.\n"
            f"stderr:\n{lifted_result.stderr}"
        )
        assert _bug_status(bug) == "running"

"""
ship_policy_sandbox.py -- drive the release's ship-policy stage in a sandbox.

Unit tests that need the real cm_release_apply_ship_policy (Steps 4c, 4d,
4b, 4e and 4f of cm/release.sh, in team/scripts/lib/cm/release_ship_policy.sh)
build a lane here: a throwaway kanban root with one project, a TESTER task
and report for the RC, the bug files the report's findings were filed as,
and a local git repository standing in for the dev tree with the RC branch
checked out. ``run_ship_policy`` sources the shipped libraries in a real
bash subprocess, sets the globals cm_release_preflight would have set, and
calls the stage with a no-op pre-squash hook; any bash appended through
``after`` runs in the same process, so it sees every global the stage set
(_SHIP_DECISION, _RELEASE_NOTES_STATUS, _CM_AUTO_SHIP_EVENTS, ...).

Every path resolves under the caller's tmp_path. The repository has no
remote and nothing here fetches, pushes or touches the operator's kanban
root: KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH both point at the sandbox
and the pre-resolved PGAI_PROJECT_ROOT / PGAI_TASKS_DIR / PGAI_PROJECT_NAME
are unset inside the script.

Fixtures (``FIXTURES``) name a TESTER report shape and, for the pattern
stop, the earlier release notes:

``trigger-2``      SHIP-WITH-SERIOUS-CONCERNS, Systemic Risk high, the high
                   finding this RC's regression with integrity breakage.
``trigger-3``      SHIP-WITH-SERIOUS-CONCERNS with a large-fix finding.
``trigger-8``      a PASS report, with the last three releases' notes all
                   NON-FUNCTIONAL.
``pass``           PASS, low risk (ships FUNCTIONAL).
``concerns``       SHIP-WITH-CONCERNS, low risk (ships KNOWN-BUGS).
``serious-small``  SHIP-WITH-SERIOUS-CONCERNS, small fixes (ships NON-FUNCTIONAL).
``known-issue``    a high-risk finding that predates the RC with a small fix
                   (ships KNOWN-ISSUE).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from tests.unit.shell_harness import BashResult, run_bash

TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
PROJECT = "posture-lane"
VERSION = "v1.4.0"
RC_BRANCH = f"rc/{VERSION}"
# provenance-allowlist: synthetic fixture task ID (test DATA the release
# engine's task-scan logic reads), not a reference to a real task.
TESTER_TASK = "TESTER-20260601-001-verify-release"

KEY_T2 = "cm-release.trigger-2.systemic-risk-high"
KEY_T3 = "cm-release.trigger-3.serious-concerns-large-fix"
KEY_T8 = "cm-release.trigger-8.last-3-rcs-non-functional"

_LIBS = (
    "scripts/lib/error_contract.sh",
    "scripts/lib/pp_run_ops.sh",
    "scripts/lib/project_paths.sh",
    "scripts/lib/cm/release_preflight.sh",
    "scripts/lib/cm/release_known_issue.sh",
    "scripts/lib/cm/release_operator_override.sh",
    "scripts/lib/cm/release_ship_posture.sh",
    "scripts/lib/cm/release_ship_policy.sh",
)


_TRIGGER_2_FINDINGS = """### Finding 1: Gated unit runner aborts at collection

**Category:** bug
**Fix Effort:** small (test-only change)
**Systemic Risk:** high (the one authoritative unit signal is dead on every run)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0058
"""

_TRIGGER_3_FINDINGS = """### Finding 1: Convergence target unmet

**Category:** gap
**Fix Effort:** large (spans several RCs)
**Systemic Risk:** medium
**Filed As:** BUG-0071
"""

_KNOWN_ISSUE_FINDINGS = """### Finding 1: Dashboard legend misaligned

**Category:** bug
**Fix Effort:** small
**Systemic Risk:** high
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0090
"""

# fixture -> (recommendation, systemic risk, findings, {bug id: symptom})
_REPORTS = {
    "trigger-2": ("SHIP-WITH-SERIOUS-CONCERNS", "high", _TRIGGER_2_FINDINGS,
                  {"BUG-0058": "The parallel unit lane collects different tests in each worker."}),
    "trigger-3": ("SHIP-WITH-SERIOUS-CONCERNS", "medium", _TRIGGER_3_FINDINGS,
                  {"BUG-0071": "Two shell verbs still have no module behind them."}),
    "trigger-8": ("PASS", "low", "", {}),
    "pass": ("PASS", "low", "", {}),
    "concerns": ("SHIP-WITH-CONCERNS", "low", "", {}),
    "serious-small": ("SHIP-WITH-SERIOUS-CONCERNS", "medium", "", {}),
    "known-issue": ("SHIP-WITH-SERIOUS-CONCERNS", "high", _KNOWN_ISSUE_FINDINGS,
                    {"BUG-0090": "The dashboard legend is misaligned on narrow terminals."}),
}
FIXTURES = tuple(_REPORTS)

WRITER_NOTES = (
    f"# Release Notes: {PROJECT} {VERSION}\n\n## Status\nPENDING-RELEASE\n\n"
    "## Features\n- Faster queue rendering\n\n## Known Issues\nNone.\n"
)


@dataclass(frozen=True)
class ShipPolicyLane:
    """One sandboxed project lane, ready for run_ship_policy."""

    root: pathlib.Path
    project_root: pathlib.Path
    repo: pathlib.Path

    @property
    def halt_file(self) -> pathlib.Path:
        return self.project_root / "HALT"

    @property
    def notes_file(self) -> pathlib.Path:
        return self.repo / "release-notes" / f"{VERSION}.md"


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def build_lane(
    tmp_path: pathlib.Path,
    fixture: str,
    posture: Optional[str],
    *,
    writer_notes: bool = False,
) -> ShipPolicyLane:
    """Build a lane for *fixture* under the ship posture *posture*.

    *posture* None leaves the ship_posture key out of both config files.
    With *writer_notes*, WRITER-authored notes carrying the PENDING-RELEASE
    placeholder are committed on the RC branch, so Step 4e stamps them.
    """
    recommendation, risk, findings, bugs = _REPORTS[fixture]
    root = tmp_path / "kanban"
    project_root = root / "projects" / PROJECT
    (project_root / "bugs").mkdir(parents=True)
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    project_cfg = "[project]\npush_to_remote = false\n"
    if posture is not None:
        project_cfg += f"\n[release]\nship_posture = {posture}\n"
    (project_root / "project.cfg").write_text(project_cfg, encoding="utf-8")
    (project_root / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{VERSION}\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    for bug_id, symptom in bugs.items():
        (project_root / "bugs" / f"{bug_id}-fixture.md").write_text(
            f"# {bug_id}-fixture\n\n## Status\nopen\n\n## Symptom\n{symptom}\n\n## Severity\nhigh\n",
            encoding="utf-8",
        )
    task_dir = project_root / "tasks" / TESTER_TASK
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# Task: {TESTER_TASK}\n\n## Role\nTESTER\n\n## Release Version\n{VERSION}\n", encoding="utf-8",
    )
    (task_dir / "status.md").write_text("# Status\n\n## State\nDONE\n", encoding="utf-8")
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {VERSION}\n\n## Recommendation\n{recommendation}\n\n"
        f"## Systemic Risk\n{risk}\n\n## Findings\n\n{findings or 'No findings.'}\n",
        encoding="utf-8",
    )

    repo = tmp_path / "dev_repo"
    (repo / "release-notes").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    if fixture == "trigger-8":
        for earlier in ("v1.1.0", "v1.2.0", "v1.3.0"):
            (repo / "release-notes" / f"{earlier}.md").write_text(
                f"# Release Notes: {PROJECT} {earlier}\n\n## Status\nNON-FUNCTIONAL\n", encoding="utf-8",
            )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", RC_BRANCH)
    if writer_notes:
        (repo / "release-notes" / f"{VERSION}.md").write_text(WRITER_NOTES, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "docs: release notes")
    return ShipPolicyLane(root=root, project_root=project_root, repo=repo)


def _q(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def run_ship_policy(
    tmp_path: pathlib.Path, lane: ShipPolicyLane, after: str = "", *, before: str = "",
) -> BashResult:
    """Run cm_release_apply_ship_policy for *lane*, then the bash in *after*.

    *before* runs after the libraries are sourced and before the stage, so a
    test can replace one library function with a stub.
    """
    script = "\n".join([
        "set -euo pipefail",
        "unset PGAI_PROJECT_ROOT PGAI_TASKS_DIR PGAI_PROJECT_NAME",
        *(f"source {lib}" for lib in _LIBS),
        before,
        "git_step() { shift; \"$@\"; }",
        "_noop_pre_squash_hook() { :; }",
        f"PROJECT_NAME={_q(PROJECT)} ACTIVE_RC={_q(VERSION)} REPO_ROOT={_q(str(lane.repo))}",
        f"MAIN_BRANCH=main RC_BRANCH={_q(RC_BRANCH)} _CM_PROJECT_ROOT={_q(str(lane.project_root))}",
        "PROJECT_HALT_FILE=\"$_CM_PROJECT_ROOT/HALT\" RELEASE_STATE=\"$_CM_PROJECT_ROOT/release-state.md\"",
        "cm_release_apply_ship_policy _noop_pre_squash_hook",
        after,
    ])
    env = {
        "KANBAN_ROOT": str(lane.root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(lane.root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
    }
    (tmp_path / "scratch").mkdir(exist_ok=True)
    return run_bash(tmp_path, script, extra_env=env, timeout=60)


def write_notes_generator(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the path of the Step 11a generator, the shipped module
    pgai_agent_kanban/cm/release_notes_generator.py, so a test can run it
    with the argv cm_release_generate_notes passes. Nothing is written:
    *tmp_path* is accepted so every caller keeps its one call shape."""
    del tmp_path
    return TEAM_DIR / "pgai_agent_kanban" / "cm" / "release_notes_generator.py"


def generate_notes_bash(generator: pathlib.Path, out_root: pathlib.Path) -> str:
    """Bash that runs the Step 11a generator into *out_root*/release-notes/,
    with the argv cm_release_generate_notes builds from the globals the
    ship-policy stage set (no bundle file and no merge base, so the sections
    other than Status, Bugs Skipped and Known Issues read "None")."""
    return (
        f"mkdir -p {_q(str(out_root))}\n"
        "PGAI_CM_INTERNAL_ID_SCRUB_DIR=\"$PWD/scripts/lib/cm\" python3 "
        f"{_q(str(generator))} none {_q(str(out_root))} \"$ACTIVE_RC\" "
        "\"${_RELEASE_NOTES_STATUS:-FUNCTIONAL}\" 2026-01-01 \"$PROJECT_NAME\" none \"$RC_BRANCH\" "
        "\"${_KNOWN_ISSUE_NOTES_BLOCK:-}\" \"${_BUGS_SKIPPED_NOTES_BLOCK:-}\""
    )


def section(notes: str, heading: str) -> str:
    """Body of the '## <heading>' section in *notes*, stripped."""
    match = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", notes, re.MULTILINE | re.DOTALL)
    assert match, f"no '## {heading}' section in:\n{notes}"
    return match.group(1).strip()

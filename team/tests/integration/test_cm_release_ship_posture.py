"""
test_cm_release_ship_posture.py
===============================
End-to-end tests for cm-release.sh under the per-project ship posture
([release] ship_posture in project.cfg), driving the real cm-open-rc.sh and
cm-release.sh against a synthetic git repository and kanban root under
tmp_path.

Coverage:
  - Trigger 2 (the shape of a real report: SHIP-WITH-SERIOUS-CONCERNS,
    Systemic Risk high, the high finding a regression with integrity
    breakage) halts under matrix; under always the same tree ships with
    release-notes Status NON-FUNCTIONAL, a Known Issues section carrying the
    non-functional warning and naming the high-risk finding's bug by
    substance, an Auto-Ship Event in release-state.md naming trigger 2 and
    its error key, and no project HALT file. This release's notes come from
    the Step 11a generator; their Known Issues section opens with the line
    'Auto-shipped under ship_posture=always (would have halted: trigger 2 —
    <error key>)'.
  - Trigger 3 (SHIP-WITH-SERIOUS-CONCERNS plus a large-fix finding) gives
    the same outcome with the event naming trigger 3. This release carries
    WRITER-authored notes, so the Known Issues block, auto-ship line
    included, comes from the Step 4e stamp.
  - Trigger 8 (the last three releases all NON-FUNCTIONAL) halts under
    matrix; under always it files exactly one bug naming the pattern
    through the ship-policy bug filer, records that bug in the Auto-Ship
    Event, writes no HALT file and ships FUNCTIONAL, with the trigger-8
    auto-ship line as its whole Known Issues section.
  - Trigger 1 (TESTER BLOCKED), the unrecognized-TESTER-state row and the
    mechanical triggers reachable on a local-only lane (4 pre-squash hook,
    5 squash conflict, 9 notes status heading, 10 strict pollution guard,
    11 fidelity gate) write the same project HALT file with the same error
    key under always as with the posture key absent, ship nothing and
    record no Auto-Ship Event.

Fixture builders come from test_cm_release_synthetic.py, as in
test_ship_policy_acceptance.py. The operator's live install is never
referenced: every path resolves under tmp_path, and push_to_remote is false
on every fixture project.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from typing import Callable, Optional

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.integration.test_cm_release_synthetic import (
    _build_kanban_root,
    _commit_in_repo_hook,
    _commit_on_branch,
    _git_local,
    _git_tag_exists,
    _init_git_repo_with_remote,
    _run_cm_script,
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH."""
    require_binary("git", label="cm-release-ship-posture")


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_OPEN_RC_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "open-rc.sh"
_RELEASE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "release.sh"
_PROJECT = "posture_lane"

_KEY_T2 = "cm-release.trigger-2.systemic-risk-high"
_KEY_T3 = "cm-release.trigger-3.serious-concerns-large-fix"
_KEY_T8 = "cm-release.trigger-8.last-3-rcs-non-functional"
# provenance-allowlist: synthetic fixture task ID (test DATA the release
# engine's task-scan logic reads), not a reference to a real task.
_TESTER_TASK = "TESTER-20260601-001-verify-release"


def _auto_ship_line(trigger: int, error_key: str) -> str:
    return f"Auto-shipped under ship_posture=always (would have halted: trigger {trigger} \u2014 {error_key})"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class Lane:
    """One fixture project: its repo, kanban root and project directory."""

    def __init__(self, tmp_path: pathlib.Path, posture: Optional[str], version: str):
        self.version = version
        self.repo = tmp_path / "dev_repo"
        _init_git_repo_with_remote(self.repo, tmp_path / "bare_remote.git")
        self.root = _build_kanban_root(tmp_path, _PROJECT, self.repo, push_to_remote="false")
        self.proj = self.root / "projects" / _PROJECT
        (self.proj / "bugs").mkdir(parents=True, exist_ok=True)
        if posture is not None:
            cfg = self.proj / "project.cfg"
            cfg.write_text(
                cfg.read_text(encoding="utf-8") + f"\n[release]\nship_posture = {posture}\n",
                encoding="utf-8",
            )

    @property
    def rc_branch(self) -> str:
        return f"rc/{self.version}"

    def open_rc(self) -> None:
        result = _run_cm_script(_OPEN_RC_SCRIPT, [self.version], self.root, _PROJECT, repo_root=self.repo)
        assert result.returncode == 0, f"cm-open-rc.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"

    def rc_work(self, filename: str = "feature.txt") -> None:
        _commit_on_branch(self.repo, self.rc_branch, filename, "RC work\n", "feat: RC work", return_branch="main")

    def release(self, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
        return _run_cm_script(_RELEASE_SCRIPT, [], self.root, _PROJECT, repo_root=self.repo, extra_env=extra_env)

    def tester_task(self, *, state: str = "DONE", recommendation: str = "PASS",
                    systemic_risk: str = "low", findings: str = "") -> None:
        task_dir = self.proj / "tasks" / _TESTER_TASK
        (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (task_dir / "README.md").write_text(
            f"# Task: {_TESTER_TASK}\n\n## Role\nTESTER\n\n## Release Version\n{self.version}\n",
            encoding="utf-8",
        )
        (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
        (task_dir / "artifacts" / "report.md").write_text(
            f"# Verification Report: {self.version}\n\n"
            f"## Recommendation\n{recommendation}\n\n"
            f"## Systemic Risk\n{systemic_risk}\n\n"
            f"## Findings\n\n{findings or 'No findings.'}\n\n"
            "## Lint Findings (non-blocking)\n\nNone.\n",
            encoding="utf-8",
        )

    def bug(self, bug_id: str, slug: str, symptom: str) -> pathlib.Path:
        path = self.proj / "bugs" / f"{bug_id}-{slug}.md"
        path.write_text(
            f"# {bug_id}: {slug}\n\n**Bug ID:** {bug_id}-{slug}\n**Filed By:** TESTER\n"
            f"**Severity:** high\n\n## Status\nopen\n\n## Category\nmisc\n\n---\n\n"
            f"## Symptom\n{symptom}\n\n## Expected\nIt works.\n\n## Actual\n{symptom}\n",
            encoding="utf-8",
        )
        return path

    def halt_key(self) -> Optional[str]:
        halt = self.proj / "HALT"
        if not halt.is_file():
            return None
        match = re.search(r"error:\s+(\S+)", halt.read_text(encoding="utf-8"))
        return match.group(1) if match else ""

    def release_state(self) -> str:
        return (self.proj / "release-state.md").read_text(encoding="utf-8")

    def auto_ship_events(self) -> list:
        """Each '## Auto-Ship Event' block in release-state.md, as a field dict."""
        events = []
        for block in self.release_state().split("## Auto-Ship Event\n")[1:]:
            fields = {}
            for line in block.split("\n## ", 1)[0].splitlines():
                label, sep, value = line.partition(": ")
                if sep:
                    fields[label] = value
            events.append(fields)
        return events

    def notes_on_main(self) -> str:
        return _git_local(self.repo, "show", f"main:release-notes/{self.version}.md")


def _section(notes: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", notes, re.MULTILINE | re.DOTALL)
    assert match, f"no '## {heading}' section in:\n{notes}"
    return match.group(1).strip()


def _diag(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# The real report shape behind a trigger-2 stop: the high-risk finding is
# this RC's own regression with integrity breakage; a second, low-risk
# finding carries a large fix.
_TRIGGER_2_FINDINGS = """### Finding 1: Gated unit runner aborts at collection

**Category:** bug
**Fix Effort:** small (test-only change)
**Systemic Risk:** high (the one authoritative unit signal is dead on every run)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0058 (Path C-A)

### Finding 2: Inline Python reduction short of target

**Category:** gap
**Fix Effort:** large (architectural)
**Systemic Risk:** low
**Filed As:** BUG-0063 (Path C-A)
"""

_UNIT_LANE_SYMPTOM = "The parallel unit lane collects different tests in each worker and runs none of them."
_INLINE_SYMPTOM = "Inline Python in shell scripts remains above the reduction target."


def _trigger_2_lane(tmp_path: pathlib.Path, posture: str) -> Lane:
    lane = Lane(tmp_path, posture, "v0.5.0")
    lane.open_rc()
    lane.rc_work()
    lane.bug("BUG-0058", "unit-lane-collection", _UNIT_LANE_SYMPTOM)
    lane.bug("BUG-0063", "inline-python-target", _INLINE_SYMPTOM)
    lane.tester_task(recommendation="SHIP-WITH-SERIOUS-CONCERNS", systemic_risk="high", findings=_TRIGGER_2_FINDINGS)
    return lane


def test_trigger_two_ships_non_functional_under_always_and_records_the_event(tmp_path):
    matrix = _trigger_2_lane(tmp_path / "matrix", "matrix")
    matrix_result = matrix.release()
    assert matrix_result.returncode != 0, _diag(matrix_result)
    assert matrix.halt_key() == _KEY_T2, _diag(matrix_result)

    lane = _trigger_2_lane(tmp_path / "always", "always")
    result = lane.release()

    assert result.returncode == 0, _diag(result)
    assert lane.halt_key() is None, _diag(result)
    assert _git_tag_exists(lane.repo, lane.version), _diag(result)
    notes = lane.notes_on_main()
    assert _section(notes, "Status") == "NON-FUNCTIONAL", notes
    known = _section(notes, "Known Issues")
    first, _, rest = known.partition("\n\n")
    assert first == _auto_ship_line(2, _KEY_T2), known
    assert rest.startswith("This release is shipped non-functional."), known
    assert _UNIT_LANE_SYMPTOM.rstrip(".") in known, known
    assert _INLINE_SYMPTOM.rstrip(".") not in known, known
    events = lane.auto_ship_events()
    assert len(events) == 1, lane.release_state()
    assert events[0]["Trigger"] == "2"
    assert events[0]["Error Key"] == _KEY_T2
    assert events[0]["Ship Posture"] == "always"
    assert events[0]["TESTER Task"] == _TESTER_TASK
    assert events[0]["Version"] == lane.version
    assert "Filed Bug" not in events[0]
    validate = subprocess.run(
        ["python3", "-m", "pgai_agent_kanban.cm.read_state_field", "--validate", str(lane.proj / "release-state.md")],
        capture_output=True, text=True, cwd=str(_TEAM_DIR), env={**os.environ, "PYTHONPATH": str(_TEAM_DIR)},
    )
    assert validate.returncode == 0, f"{validate.stdout}\n{validate.stderr}"


_TRIGGER_3_FINDINGS = """### Finding 1: Convergence target unmet

**Category:** gap
**Fix Effort:** large (spans several RCs)
**Systemic Risk:** medium
**Filed As:** BUG-0071

### Finding 2: Stale assertion in one test

**Category:** stale-assertion
**Fix Effort:** small
**Systemic Risk:** low
**Filed As:** BUG-0072
"""

_CONVERGENCE_SYMPTOM = "Two shell verbs still have no module behind them."
_STALE_SYMPTOM = "One test still asserts the old route list."


def _trigger_3_lane(tmp_path: pathlib.Path, posture: str) -> Lane:
    lane = Lane(tmp_path, posture, "v0.6.0")
    lane.open_rc()
    lane.rc_work()
    # WRITER-authored notes carrying the placeholder Step 4e stamps.
    _git_local(lane.repo, "checkout", lane.rc_branch)
    (lane.repo / "release-notes" / f"{lane.version}.md").write_text(
        f"# Release Notes: {_PROJECT} {lane.version}\n\n## Status\nPENDING-RELEASE\n\n"
        "## Features\n- RC work\n\n## Known Issues\nNone.\n",
        encoding="utf-8",
    )
    _git_local(lane.repo, "add", f"release-notes/{lane.version}.md")
    _git_local(lane.repo, "commit", "-m", "docs: release notes")
    _git_local(lane.repo, "checkout", "main")
    lane.bug("BUG-0071", "convergence-target", _CONVERGENCE_SYMPTOM)
    lane.bug("BUG-0072", "stale-route-assertion", _STALE_SYMPTOM)
    lane.tester_task(recommendation="SHIP-WITH-SERIOUS-CONCERNS", systemic_risk="medium", findings=_TRIGGER_3_FINDINGS)
    return lane


def test_trigger_three_ships_non_functional_under_always_and_stamps_known_issues(tmp_path):
    matrix = _trigger_3_lane(tmp_path / "matrix", "matrix")
    matrix_result = matrix.release()
    assert matrix_result.returncode != 0, _diag(matrix_result)
    assert matrix.halt_key() == _KEY_T3, _diag(matrix_result)

    lane = _trigger_3_lane(tmp_path / "always", "always")
    result = lane.release()

    assert result.returncode == 0, _diag(result)
    assert lane.halt_key() is None, _diag(result)
    assert _git_tag_exists(lane.repo, lane.version), _diag(result)
    notes = lane.notes_on_main()
    assert _section(notes, "Status") == "NON-FUNCTIONAL", notes
    known = _section(notes, "Known Issues")
    first, _, rest = known.partition("\n\n")
    assert first == _auto_ship_line(3, _KEY_T3), known
    assert rest.startswith("This release is shipped non-functional."), known
    assert _CONVERGENCE_SYMPTOM.rstrip(".") in known, known
    assert _STALE_SYMPTOM.rstrip(".") not in known, known
    events = lane.auto_ship_events()
    assert len(events) == 1, lane.release_state()
    assert (events[0]["Trigger"], events[0]["Error Key"], events[0]["Ship Posture"]) == ("3", _KEY_T3, "always")
    assert events[0]["TESTER Task"] == _TESTER_TASK


def _trigger_8_lane(tmp_path: pathlib.Path, posture: str) -> Lane:
    lane = Lane(tmp_path, posture, "v0.4.0")
    for version in ("v0.1.0", "v0.2.0", "v0.3.0"):
        (lane.repo / "release-notes" / f"{version}.md").write_text(
            f"# Release Notes: {_PROJECT} {version}\n\n## Status\nNON-FUNCTIONAL\n", encoding="utf-8",
        )
    _git_local(lane.repo, "add", "release-notes")
    _git_local(lane.repo, "commit", "-m", "docs: three earlier release notes")
    lane.open_rc()
    lane.rc_work()
    lane.tester_task()
    return lane


def test_trigger_eight_files_one_pattern_bug_and_ships_under_always(tmp_path):
    matrix = _trigger_8_lane(tmp_path / "matrix", "matrix")
    matrix_result = matrix.release()
    assert matrix_result.returncode != 0, _diag(matrix_result)
    assert matrix.halt_key() == _KEY_T8, _diag(matrix_result)
    assert list((matrix.proj / "bugs").glob("BUG-*.md")) == []

    lane = _trigger_8_lane(tmp_path / "always", "always")
    result = lane.release()

    assert result.returncode == 0, _diag(result)
    assert lane.halt_key() is None, _diag(result)
    assert _git_tag_exists(lane.repo, lane.version), _diag(result)
    bugs = sorted((lane.proj / "bugs").glob("BUG-*.md"))
    assert len(bugs) == 1, _diag(result)
    assert bugs[0].name.endswith("-last-three-releases-non-functional.md")
    bug_text = bugs[0].read_text(encoding="utf-8")
    assert "v0.1.0.md=NON-FUNCTIONAL, v0.2.0.md=NON-FUNCTIONAL, v0.3.0.md=NON-FUNCTIONAL" in bug_text
    bug_id = re.match(r"(BUG-\d+)", bugs[0].name).group(1)
    events = lane.auto_ship_events()
    assert len(events) == 1, lane.release_state()
    assert (events[0]["Trigger"], events[0]["Error Key"], events[0]["Filed Bug"]) == ("8", _KEY_T8, bug_id)
    assert events[0]["Ship Posture"] == "always"
    # Step 4d decided on the TESTER report alone: a PASS report ships FUNCTIONAL.
    notes = lane.notes_on_main()
    assert _section(notes, "Status") == "FUNCTIONAL"
    assert _section(notes, "Known Issues") == _auto_ship_line(8, _KEY_T8), notes


# ---------------------------------------------------------------------------
# Every other HALT halts identically under always
# ---------------------------------------------------------------------------


def _blocked_tester(lane: Lane) -> Optional[dict]:
    lane.open_rc()
    lane.rc_work()
    lane.tester_task(state="BLOCKED", systemic_risk="high")
    return None


def _working_tester(lane: Lane) -> Optional[dict]:
    lane.open_rc()
    lane.rc_work()
    lane.tester_task(state="WORKING")
    return None


def _failing_pre_squash_hook(lane: Lane) -> Optional[dict]:
    lane.open_rc()
    lane.rc_work()
    _commit_in_repo_hook(
        lane.repo, lane.rc_branch, "pre-squash", "#!/usr/bin/env bash\nexit 1\n",
        "chore: pre-squash hook that fails", return_branch="main",
    )
    return None


def _squash_conflict(lane: Lane) -> Optional[dict]:
    _commit_on_branch(lane.repo, "main", "shared.txt", "line A\nline B\n", "chore: shared file")
    lane.open_rc()
    _commit_on_branch(lane.repo, lane.rc_branch, "shared.txt", "line A\nline B (RC)\n", "feat: RC edit", return_branch="main")
    _commit_on_branch(lane.repo, "main", "shared.txt", "line A\nline B (main)\n", "fix: conflicting main edit")
    return None


def _notes_without_status(lane: Lane) -> Optional[dict]:
    lane.open_rc()
    lane.rc_work()
    _git_local(lane.repo, "checkout", lane.rc_branch)
    (lane.repo / "release-notes" / f"{lane.version}.md").write_text("# Release Notes\n\nNo status heading.\n", encoding="utf-8")
    _git_local(lane.repo, "add", f"release-notes/{lane.version}.md")
    _git_local(lane.repo, "commit", "-m", "docs: notes without a status heading")
    _git_local(lane.repo, "checkout", "main")
    return None


def _strict_pollution(lane: Lane) -> Optional[dict]:
    lane.open_rc()
    _git_local(lane.repo, "checkout", lane.rc_branch)
    (lane.repo / "artifacts").mkdir(exist_ok=True)
    (lane.repo / "artifacts" / "scratch.txt").write_text("stray\n", encoding="utf-8")
    _git_local(lane.repo, "add", "artifacts/scratch.txt")
    _git_local(lane.repo, "commit", "-m", "chore: stray artifact")
    _git_local(lane.repo, "checkout", "main")
    return {"CM_SQUASH_STRICT_POLLUTION_GUARD": "true"}


def _fidelity_divergence(lane: Lane) -> Optional[dict]:
    lane.open_rc()
    lane.rc_work()
    _commit_on_branch(lane.repo, "main", "hotfix.txt", "hotfix\n", "fix: non-conflicting main commit")
    return None


_HALT_SCENARIOS: dict = {
    "trigger_1_tester_blocked": (_blocked_tester, "cm-release.trigger-1.tester-blocked"),
    "unrecognized_tester_state": (_working_tester, "cm-release.ship-policy.tester-state-unrecognized"),
    "trigger_4_pre_squash_hook_failed": (_failing_pre_squash_hook, "cm-release.trigger-4.pre-squash-hook-failed"),
    "trigger_5_squash_conflict": (_squash_conflict, "cm-release.trigger-5.squash-conflict"),
    "trigger_9_notes_missing_status": (_notes_without_status, "cm-release.trigger-9.notes-missing-status-heading"),
    "trigger_10_strict_pollution_guard": (_strict_pollution, "cm-release.trigger-10.pollution-guard-strict"),
    "trigger_11_fidelity_gate": (_fidelity_divergence, "cm-release.trigger-11.fidelity-gate-divergence"),
}


def _run_halt_scenario(tmp_path: pathlib.Path, posture: Optional[str], build: Callable) -> Lane:
    lane = Lane(tmp_path, posture, "v0.7.0")
    extra_env = build(lane)
    lane.result = lane.release(extra_env=extra_env)
    return lane


@pytest.mark.parametrize("scenario", sorted(_HALT_SCENARIOS))
def test_every_other_halt_writes_the_same_halt_file_under_always(tmp_path, scenario):
    build, expected_key = _HALT_SCENARIOS[scenario]
    reference = _run_halt_scenario(tmp_path / "absent", None, build)
    lane = _run_halt_scenario(tmp_path / "always", "always", build)

    for run in (reference, lane):
        assert run.result.returncode != 0, _diag(run.result)
        assert run.halt_key() == expected_key, _diag(run.result)
        assert not _git_tag_exists(run.repo, run.version), _diag(run.result)
        assert run.auto_ship_events() == [], run.release_state()
    assert lane.halt_key() == reference.halt_key()
    assert not (lane.root / "HALT").exists(), "a release halt must never write the fleet-scoped HALT"

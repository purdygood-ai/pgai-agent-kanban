"""
test_cm_release_decide_only.py
==============================
End-to-end tests for ``cm/release.sh --decide-only``: the run that resolves
the project, validates the active RC, applies the ship-policy matrix with the
project's ship posture and prints the decision a full release would reach,
without writing anything.

Every fixture drives the real cm-open-rc.sh and cm-release.sh against a
synthetic git repository and kanban root under tmp_path (push_to_remote is
false on every fixture project; the operator's live install is never
referenced).

Coverage:
  - On each ship-policy fixture -- a clean PASS report (SHIP-FUNCTIONAL),
    trigger 1 (TESTER BLOCKED), trigger 2 (systemic risk high with a
    regression), trigger 3 (serious concerns plus a large fix), trigger 8
    (the last three releases NON-FUNCTIONAL), the unrecognized-TESTER-state
    row and the no-row-matched row -- under ship_posture=matrix, with the
    key absent and under always:
      * stdout is exactly one 'ship-policy: decision=… posture=… trigger=…
        notes_status=…' line, followed on HALT by the would-be HALT's
        error/message/remedy lines, and the exit status is 0 for a ship and
        3 for HALT;
      * the dev tree's HEAD (symbolic ref and tree hash), every branch and
        tag ref, the working-tree status, release-state.md, the bugs/
        directory, both HALT files and every other file under the kanban
        root are byte-identical before and after -- including trigger 8
        under always, where the full run files a bug;
      * a full release on an identical copy of the sandbox reaches the same
        decision: the same ship decision and notes status when the decider
        exits 0, and a HALT file carrying the same error key when it
        exits 3.
  - The trigger-8 pattern check reads the RC branch's release-notes/ whether
    or not the dev tree has the RC branch checked out, and agrees with the
    full run either way.
  - The decider runs under an agent wake (PGAI_AGENT_WAKE_ACTIVE=1) without
    the operator-only --force refusal, and refuses --force/--reason with a
    dual-channel message, writing nothing.
  - --help documents --decide-only and the help-presence lint passes.

The no-row-matched row cannot be reached by a report the shipped readers
normalize (every systemic-risk token they print is lower case), so that
fixture reaches it the one way the unit tests do -- by handing the matrix an
upper-case "HIGH" -- through a BASH_ENV file both the decide-only and the
full run load: it sources the real report reader and wraps
read_ship_policy_report_values. Nothing else in the release is altered.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.integration.test_cm_release_ship_posture import (
    _PROJECT,
    _RELEASE_SCRIPT,
    Lane,
    _blocked_tester,
    _trigger_2_lane,
    _trigger_3_lane,
    _trigger_8_lane,
    _working_tester,
)
from tests.integration.test_cm_release_synthetic import (
    _git_local,
    _git_tag_exists,
    _run_cm_script,
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH."""
    require_binary("git", label="cm-release-decide-only")


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/

_KEY_T1 = "cm-release.trigger-1.tester-blocked"
_KEY_T2 = "cm-release.trigger-2.systemic-risk-high"
_KEY_T3 = "cm-release.trigger-3.serious-concerns-large-fix"
_KEY_T8 = "cm-release.trigger-8.last-3-rcs-non-functional"
_KEY_UNRECOGNIZED = "cm-release.ship-policy.tester-state-unrecognized"
_KEY_UNMATCHED = "cm-release.ship-policy.halt"

_LINE_RE = re.compile(
    r"^ship-policy: decision=(?P<decision>\S+)\s+posture=(?P<posture>\S+)\s+"
    r"trigger=(?P<trigger>\S+)\s+notes_status=(?P<notes_status>\S+)\s*$"
)
_PROCEEDING_RE = re.compile(r"Proceeding with release: (\S+) \(release notes Status: ([^)]*)\)")

# The postures each fixture runs under: the key set to matrix, absent, and
# set to always.
_POSTURES = {"matrix": "matrix", "absent": None, "always": "always"}


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _clean_pass(lane: Lane) -> Optional[dict]:
    lane.open_rc()
    lane.rc_work()
    lane.tester_task()
    return None


def _unmatched_row(lane: Lane) -> Optional[dict]:
    """A DONE report with high systemic risk handed to the matrix as "HIGH"."""
    lane.open_rc()
    lane.rc_work()
    lane.tester_task(systemic_risk="high")
    bash_env = lane.repo.parent.parent / "unmatched_row_bash_env.sh"
    bash_env.write_text(
        "# Test-only: the matrix sees the report's Systemic Risk as an upper-case\n"
        "# token, which no named HALT row matches.\n"
        f"source {_TEAM_DIR / 'scripts' / 'lib' / 'cm' / 'release_ship_policy_report.sh'}\n"
        "read_ship_policy_report_values() {\n"
        "  pp_run_ops pgai_agent_kanban.cm.ship_policy_report_inputs values \"$1\" "
        "| sed 's/^systemic_risk=high$/systemic_risk=HIGH/'\n"
        "}\n",
        encoding="utf-8",
    )
    return {"BASH_ENV": str(bash_env)}


def _lane_builder(build: Callable[[Lane], Optional[dict]]) -> Callable:
    def make(tmp_path: pathlib.Path, posture: Optional[str]):
        lane = Lane(tmp_path, posture, "v0.7.0")
        return lane, build(lane)
    return make


def _posture_builder(build: Callable[[pathlib.Path, str], Lane]) -> Callable:
    def make(tmp_path: pathlib.Path, posture: Optional[str]):
        return build(tmp_path, posture), None
    return make


@dataclass(frozen=True)
class Expected:
    """What the decider prints (and the full run reaches) for one fixture."""

    decision: str
    trigger: str
    notes_status: str
    halt_key: str = ""


_SHIP_FUNCTIONAL = Expected("SHIP-FUNCTIONAL", "none", "FUNCTIONAL")


@dataclass(frozen=True)
class Scenario:
    make: Callable
    matrix: Expected
    always: Expected


_SCENARIOS = {
    "clean_ship_functional": Scenario(_lane_builder(_clean_pass), _SHIP_FUNCTIONAL, _SHIP_FUNCTIONAL),
    "trigger_1_tester_blocked": Scenario(
        _lane_builder(_blocked_tester),
        Expected("HALT", "1", "none", _KEY_T1),
        Expected("HALT", "1", "none", _KEY_T1),
    ),
    "trigger_2_systemic_risk_high": Scenario(
        _posture_builder(_trigger_2_lane),
        Expected("HALT", "2", "none", _KEY_T2),
        Expected("SHIP-NON-FUNCTIONAL", "2", "NON-FUNCTIONAL"),
    ),
    "trigger_3_serious_concerns_large_fix": Scenario(
        _posture_builder(_trigger_3_lane),
        Expected("HALT", "3", "none", _KEY_T3),
        Expected("SHIP-NON-FUNCTIONAL", "3", "NON-FUNCTIONAL"),
    ),
    "trigger_8_last_three_non_functional": Scenario(
        _posture_builder(_trigger_8_lane),
        Expected("HALT", "8", "none", _KEY_T8),
        Expected("SHIP-FUNCTIONAL", "8", "FUNCTIONAL"),
    ),
    "unrecognized_tester_state": Scenario(
        _lane_builder(_working_tester),
        Expected("HALT", "none", "none", _KEY_UNRECOGNIZED),
        Expected("HALT", "none", "none", _KEY_UNRECOGNIZED),
    ),
    "unmatched_row": Scenario(
        _lane_builder(_unmatched_row),
        Expected("HALT", "none", "none", _KEY_UNMATCHED),
        Expected("HALT", "none", "none", _KEY_UNMATCHED),
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _diag(result: subprocess.CompletedProcess) -> str:
    return f"--- exit {result.returncode} ---\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _decide(lane: Lane, extra_env: Optional[dict] = None, args: tuple = ()) -> subprocess.CompletedProcess:
    return _run_cm_script(
        _RELEASE_SCRIPT, ["--decide-only", *args], lane.root, _PROJECT, repo_root=lane.repo, extra_env=extra_env,
    )


def _files(root: pathlib.Path) -> dict:
    """Every file under *root*, relative path -> sha256 of its bytes."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _snapshot(lane: Lane) -> dict:
    """Everything a decide-only run must leave untouched."""
    repo = lane.repo
    bugs = lane.proj / "bugs"
    return {
        "head_ref": _git_local(repo, "rev-parse", "--symbolic-full-name", "HEAD"),
        "head_tree": _git_local(repo, "rev-parse", "HEAD^{tree}"),
        "refs": _git_local(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
        "status": _git_local(repo, "status", "--porcelain", "--untracked-files=all"),
        "release_state": (lane.proj / "release-state.md").read_bytes(),
        "bugs": _files(bugs) if bugs.is_dir() else None,
        "project_halt": (lane.proj / "HALT").exists(),
        "root_halt": (lane.root / "HALT").exists(),
        "kanban_root_files": _files(lane.root),
    }


def _assert_unchanged(before: dict, after: dict, result: subprocess.CompletedProcess) -> None:
    for key in before:
        assert after[key] == before[key], f"--decide-only changed {key}\n{_diag(result)}"


def _parse(result: subprocess.CompletedProcess) -> tuple:
    """(decision-line fields, the HALT contract lines) from decider stdout."""
    lines = result.stdout.splitlines()
    assert lines, _diag(result)
    match = _LINE_RE.match(lines[0])
    assert match, f"first stdout line is not the decision line\n{_diag(result)}"
    assert sum(1 for line in lines if line.startswith("ship-policy:")) == 1, _diag(result)
    return match.groupdict(), lines[1:]


def _assert_decision(result: subprocess.CompletedProcess, expected: Expected, posture: str) -> None:
    fields, rest = _parse(result)
    assert fields == {
        "decision": expected.decision,
        "posture": posture,
        "trigger": expected.trigger,
        "notes_status": expected.notes_status,
    }, _diag(result)
    if expected.decision == "HALT":
        assert result.returncode == 3, _diag(result)
        assert len(rest) == 3, _diag(result)
        assert re.match(rf"^error:\s+{re.escape(expected.halt_key)}$", rest[0]), _diag(result)
        assert rest[1].startswith("message: ") and rest[2].startswith("remedy: "), _diag(result)
    else:
        assert result.returncode == 0, _diag(result)
        assert rest == [], _diag(result)


class _Copy:
    """A full copy of one fixture's sandbox (repo, bare remote, kanban root)."""

    def __init__(self, lane: Lane, dest: pathlib.Path):
        src = lane.repo.parent
        shutil.copytree(src, dest, symlinks=True)
        self.repo = dest / lane.repo.name
        self.root = dest / lane.root.name
        self.proj = self.root / "projects" / _PROJECT
        self.version = lane.version
        cfg = self.proj / "project.cfg"
        cfg.write_text(
            cfg.read_text(encoding="utf-8").replace(f"dev_tree_path = {lane.repo}", f"dev_tree_path = {self.repo}"),
            encoding="utf-8",
        )

    def release(self, extra_env: Optional[dict]) -> subprocess.CompletedProcess:
        return _run_cm_script(_RELEASE_SCRIPT, [], self.root, _PROJECT, repo_root=self.repo, extra_env=extra_env)

    def halt_key(self) -> Optional[str]:
        halt = self.proj / "HALT"
        if not halt.is_file():
            return None
        match = re.search(r"error:\s+(\S+)", halt.read_text(encoding="utf-8"))
        return match.group(1) if match else ""


def _assert_full_run_agrees(decided: subprocess.CompletedProcess, full: subprocess.CompletedProcess,
                            copy: _Copy) -> None:
    fields, rest = _parse(decided)
    if decided.returncode == 0:
        assert full.returncode == 0, f"decider shipped, full run did not\n{_diag(full)}"
        assert copy.halt_key() is None, _diag(full)
        assert _git_tag_exists(copy.repo, copy.version), _diag(full)
        proceeding = _PROCEEDING_RE.search(full.stdout)
        assert proceeding, _diag(full)
        assert proceeding.groups() == (fields["decision"], fields["notes_status"]), _diag(full)
    else:
        assert decided.returncode == 3, _diag(decided)
        assert full.returncode != 0, f"decider halted, full run shipped\n{_diag(full)}"
        assert not _git_tag_exists(copy.repo, copy.version), _diag(full)
        assert copy.halt_key() == rest[0].split()[1], _diag(full)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("posture_id", sorted(_POSTURES))
@pytest.mark.parametrize("scenario_id", sorted(_SCENARIOS))
def test_decide_only_writes_nothing_and_agrees_with_the_full_run(tmp_path, scenario_id, posture_id):
    scenario = _SCENARIOS[scenario_id]
    posture = _POSTURES[posture_id]
    lane, extra_env = scenario.make(tmp_path / "decide", posture)
    expected = scenario.always if posture == "always" else scenario.matrix

    before = _snapshot(lane)
    decided = _decide(lane, extra_env)
    _assert_unchanged(before, _snapshot(lane), decided)
    _assert_decision(decided, expected, "always" if posture == "always" else "matrix")

    copy = _Copy(lane, tmp_path / "full")
    full = copy.release(extra_env)
    _assert_full_run_agrees(decided, full, copy)
    if scenario_id == "trigger_8_last_three_non_functional" and posture == "always":
        # The full run files the pattern bug the decider only narrated.
        assert len(list((copy.proj / "bugs").glob("BUG-*.md"))) == 1, _diag(full)
        assert list((lane.proj / "bugs").glob("BUG-*.md")) == [], _diag(decided)


def _pattern_on_rc_branch_only(tmp_path: pathlib.Path) -> Lane:
    """Three NON-FUNCTIONAL release notes committed on the RC branch alone."""
    lane = Lane(tmp_path, "matrix", "v0.4.0")
    lane.open_rc()
    _git_local(lane.repo, "checkout", lane.rc_branch)
    for version in ("v0.1.0", "v0.2.0", "v0.3.0"):
        (lane.repo / "release-notes" / f"{version}.md").write_text(
            f"# Release Notes: {_PROJECT} {version}\n\n## Status\nNON-FUNCTIONAL\n", encoding="utf-8",
        )
    _git_local(lane.repo, "add", "release-notes")
    _git_local(lane.repo, "commit", "-m", "docs: three earlier release notes")
    _git_local(lane.repo, "checkout", "main")
    lane.tester_task()
    return lane


@pytest.mark.parametrize("checked_out", ["main", "rc"])
def test_pattern_check_reads_the_rc_branch_notes_without_a_checkout(tmp_path, checked_out):
    lane = _pattern_on_rc_branch_only(tmp_path / "decide")
    if checked_out == "rc":
        _git_local(lane.repo, "checkout", lane.rc_branch)

    before = _snapshot(lane)
    decided = _decide(lane)
    _assert_unchanged(before, _snapshot(lane), decided)
    _assert_decision(decided, Expected("HALT", "8", "none", _KEY_T8), "matrix")

    copy = _Copy(lane, tmp_path / "full")
    _assert_full_run_agrees(decided, copy.release(None), copy)


def test_decide_only_runs_under_an_agent_wake(tmp_path):
    lane, _ = _lane_builder(_clean_pass)(tmp_path, None)
    before = _snapshot(lane)
    decided = _decide(lane, {"PGAI_AGENT_WAKE_ACTIVE": "1"})
    _assert_unchanged(before, _snapshot(lane), decided)
    assert "agent-wake-refused" not in decided.stderr, _diag(decided)
    _assert_decision(decided, _SHIP_FUNCTIONAL, "matrix")


@pytest.mark.parametrize(
    "flags",
    [("--force", "--reason", "ship it"), ("--reason", "ship it"), ("--force",)],
    ids=["force_and_reason", "reason_only", "force_only"],
)
@pytest.mark.parametrize("wake", ["0", "1"])
def test_decide_only_refuses_override_flags_and_writes_nothing(tmp_path, flags, wake):
    lane, _ = _lane_builder(_blocked_tester)(tmp_path, None)
    before = _snapshot(lane)
    decided = _decide(lane, {"PGAI_AGENT_WAKE_ACTIVE": wake}, args=flags)
    _assert_unchanged(before, _snapshot(lane), decided)
    assert decided.returncode == 1, _diag(decided)
    assert decided.stdout == "", _diag(decided)
    assert re.search(r"^error:\s+cm-release\.decide-only\.override-flag-refused$", decided.stderr, re.MULTILINE), \
        _diag(decided)
    assert re.search(r"^remedy:\s+\S", decided.stderr, re.MULTILINE), _diag(decided)


@pytest.mark.parametrize(
    "project_args, env_project, expected_project",
    [
        (("--project", "other-proj"), _PROJECT, "other-proj"),
        ((), _PROJECT, _PROJECT),
        ((), "", "PROJECT_NAME"),
    ],
    ids=["project_flag_wins", "env_project_name", "neither_set_placeholder"],
)
def test_decide_only_override_refusal_remedy_names_the_requested_project(
    tmp_path, project_args, env_project, expected_project,
):
    """The refusal's remedy runs the decider for the project the operator asked about.

    ``--project`` wins over ``PGAI_PROJECT_NAME``; with neither, the remedy
    names the upper-case ``PROJECT_NAME`` placeholder (never a real project,
    since project names are lower-case). The refusal still exits 1 and
    writes nothing.
    """
    lane, _ = _lane_builder(_clean_pass)(tmp_path, None)
    before = _snapshot(lane)
    decided = _run_cm_script(
        _RELEASE_SCRIPT,
        [*project_args, "--decide-only", "--force", "--reason", "x"],
        lane.root,
        env_project,
        repo_root=lane.repo,
    )
    _assert_unchanged(before, _snapshot(lane), decided)
    assert decided.returncode == 1, _diag(decided)
    assert re.search(r"^error:\s+cm-release\.decide-only\.override-flag-refused$", decided.stderr, re.MULTILINE), \
        _diag(decided)
    remedy = re.search(r"^remedy:\s+(.*)$", decided.stderr, re.MULTILINE)
    assert remedy, _diag(decided)
    assert re.search(rf"--project\s+{re.escape(expected_project)}\s+--decide-only", remedy.group(1)), _diag(decided)
    assert "--force" not in remedy.group(1) and "--reason" not in remedy.group(1), _diag(decided)
    for other in {"pgai-agent-kanban", _PROJECT, "other-proj"} - {expected_project}:
        assert other not in remedy.group(1), _diag(decided)


def test_help_documents_decide_only_and_the_help_lint_passes():
    help_result = subprocess.run(["bash", str(_RELEASE_SCRIPT), "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0, help_result.stderr
    assert "--decide-only" in help_result.stdout
    assert "ship-policy: decision=" in help_result.stdout
    lint = subprocess.run(
        ["python3", str(_TEAM_DIR / "scripts" / "lint_help_presence.py")], capture_output=True, text=True,
    )
    assert lint.returncode == 0, f"{lint.stdout}\n{lint.stderr}"

"""
test_overwatch_resolution_prefixed_tag.py
=========================================
End to end through pgai_agent_kanban.overwatch.resolution_stage: on a
project with ``branch_prefix = ai_``, a CM ship-policy HALT raised after
the release tag ``ai_vX.Y.Z`` was cut is a hard HALT. The stage reads the
prefixed tag as "shipped or tagged", declines, and touches nothing -- it
never runs the cancel-and-rebundle drill against a tagged release.

The fixture is the golden cancel-and-rebundle fixture of
test_overwatch_resolution_stage.py (every other fact matches the drill's
row, and its project.cfg sets branch_prefix = ai_), with one change: the
release tag exists locally. All scratch lives under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch.resolution_stage import DECLINED, run_resolution_stage
from tests.fixtures.binary_guard import require_binary
from tests.integration.test_overwatch_resolution_stage import _build_golden_fixture


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="overwatch-resolution-prefixed-tag")


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def test_post_tag_ship_policy_halt_on_a_prefixed_project_is_a_hard_halt(tmp_path: pathlib.Path) -> None:
    (
        kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id,
    ) = _build_golden_fixture(tmp_path)
    _git(repo_path, "tag", f"ai_{version}", "ai_main")

    outcome = run_resolution_stage("golden_project", str(kanban_root))

    assert outcome.status == DECLINED, outcome.reason
    assert "shipped_or_tagged=True" in outcome.reason, outcome.reason

    # Nothing of the drill ran: HALT kept, no ledger, RC still active, RC
    # branch and tag intact, task and bug untouched.
    assert halt_path.is_file()
    assert not (proj / "auto-resolutions.md").exists()
    assert f"## Active RC\n{version}" in (proj / "release-state.md").read_text(encoding="utf-8")
    assert _git(repo_path, "branch", "--list", f"ai_rc/{version}").strip()
    assert _git(repo_path, "tag", "--list", f"ai_{version}").strip() == f"ai_{version}"
    assert "## State\nWORKING" in (proj / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    assert "## Status\nassigned" in (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")

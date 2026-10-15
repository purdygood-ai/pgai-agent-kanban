"""
test_overwatch_backlog_self_heal.py
====================================
Regression coverage for the overwatch backlog scaffolding and self-heal
fix, driven through the REAL production entry point (bash
scripts/wake/claude.sh), exactly as
test_overwatch_wake_driven_fixtures.py already does for the resolution
stage's other fixtures — this file reuses that file's fixture builders
rather than duplicating them (per this test tree's established
one-self-contained-fixture-set-per-file convention, the builders
themselves are shared, not the assertions).

The guarded defect: run_project_chain's missing-backlog-file check
(team/scripts/lib/wake_driver.sh) running BEFORE run_overwatch_resolution_stage
and returning early for every agent, overwatch included. A project whose
overwatch_backlog.md was deleted (or never seeded, on a pre-scaffolding
install) then never reaches the resolution stage at all — silently
swallowing a ship-policy HALT the overwatch carve-out in wake_common.sh's
per-project HALT fast-exit had specifically let the chain through to
classify. The queue file's presence, not the HALT, would decide whether the
block ever got a look.

Coverage:
  - HALT present, overwatch_backlog.md deleted: the wake self-heals the
    file (recreates it) and narrates the self-heal, and the resolution
    stage still runs to completion (the same CANCEL-AND-REBUNDLE
    classification test_overwatch_wake_driven_fixtures.py's golden fixture
    exercises when the file is left alone) rather than being skipped past.
  - Empty overwatch_backlog.md, no HALT: the self-heal never engages — a clean
    idle exit, exactly as before.

Design notes:
  - All temp paths use pytest's tmp_path. No bare /tmp paths appear in this
    file.
  - Every wake invocation goes through the real
    `bash .../scripts/wake/claude.sh --agent=overwatch --max-tasks=1`
    subprocess — the exact entry point a cron firing invokes.
"""

from __future__ import annotations

import pathlib
import re

from tests.integration.test_overwatch_wake_driven_fixtures import (
    _build_golden_fixture,
    _build_kanban_root,
    _init_git_repo,
    _resolution_stage_log,
    _run_wake,
    _write_release_state_md,
)


def _backlog_path(fixture_kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    return fixture_kanban_root / "projects" / project_name / "tasks" / "queues" / "overwatch_backlog.md"


class TestMissingBacklogSelfHealsRatherThanSkipping:
    def test_halt_present_and_backlog_deleted_self_heals_and_still_classifies_the_block(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_golden_fixture(tmp_path, "wd_self_heal")

        backlog = _backlog_path(fx.kanban_root, fx.proj.name)
        assert backlog.is_file(), "Fixture builder must seed the queue file before we delete it."
        backlog.unlink()
        assert not backlog.exists(), "Precondition: the queue file must be gone before waking."

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, (
            f"wake/claude.sh --agent=overwatch exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        # Self-healed: the file exists again, and the wake's own log
        # narrates the self-heal rather than silently recreating it.
        assert backlog.is_file(), (
            "Expected overwatch_backlog.md to be recreated (self-healed) by the wake.\n"
            f"stdout:\n{result.stdout}"
        )
        # Narration lives on the log channel (stderr), never on stdout --
        # stdout is reserved for data a caller might capture via $(...).
        assert "self-healed" in result.stderr, (
            f"Expected the wake log to narrate the self-heal.\nstderr:\n{result.stderr}"
        )

        # Not skipped past: the resolution stage still ran to completion and
        # classified/resolved the block, exactly as it does when the queue
        # file was never disturbed (test_overwatch_wake_driven_fixtures.py's
        # golden fixture asserts the identical outcome).
        stage_log = _resolution_stage_log(fx.kanban_root)
        assert "status=executed" in stage_log, f"resolution-stage log:\n{stage_log}"
        ledger_text = fx.ledger_path.read_text(encoding="utf-8")
        assert ledger_text.count("## Entry:") == 1
        assert "CANCEL-AND-REBUNDLE" in ledger_text
        assert not fx.halt_path.exists(), "The ship-policy HALT this row resolves should be cleared."

    def test_self_heal_produces_no_bracket_marker_entries(self, tmp_path: pathlib.Path) -> None:
        """The recreated queue file is empty (header/legend only) -- a
        self-heal must never invent a queued task."""
        fx = _build_golden_fixture(tmp_path, "wd_self_heal_empty")
        backlog = _backlog_path(fx.kanban_root, fx.proj.name)
        backlog.unlink()

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, (
            f"wake/claude.sh --agent=overwatch exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        recreated_text = backlog.read_text(encoding="utf-8")
        assert not re.search(r"^\s*-\s*\[[ WABx]\]\s+\S", recreated_text, re.MULTILINE), (
            f"Expected zero bracket-marker entries in the self-healed file.\n{recreated_text}"
        )


class TestEmptyBacklogNoHaltStaysAnIdleExit:
    def test_empty_backlog_and_no_halt_is_a_clean_idle_exit(self, tmp_path: pathlib.Path) -> None:
        """Outside the self-heal: a project with no HALT and its
        (already-present) empty overwatch_backlog.md untouched exits clean,
        exactly as before -- this fixture never enters the self-heal branch
        at all because the file was never missing."""
        project_name = "wd_idle_no_halt"
        repo_path = tmp_path / f"dev_repo_{project_name}"
        _init_git_repo(repo_path, "ai_main", "ai_rc/v2.0.1")
        kanban_root = _build_kanban_root(tmp_path, project_name, repo_path)
        proj = kanban_root / "projects" / project_name
        _write_release_state_md(proj, "none")

        backlog = _backlog_path(kanban_root, project_name)
        assert backlog.is_file(), "Fixture builder must seed the queue file."
        before_text = backlog.read_text(encoding="utf-8")

        result = _run_wake(kanban_root, "overwatch")
        assert result.returncode == 0, (
            f"wake/claude.sh --agent=overwatch exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        # Narration lives on the log channel (stderr); check both channels
        # so this negative assertion still catches a false self-heal even
        # if narration ever leaked back onto stdout.
        assert "self-healed" not in result.stdout and "self-healed" not in result.stderr, (
            "No self-heal should ever fire when the file was never missing.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert backlog.read_text(encoding="utf-8") == before_text, (
            "The queue file must be left byte-for-byte unchanged on an idle firing."
        )

        stage_log = _resolution_stage_log(kanban_root)
        assert "no HALT file present" in stage_log or stage_log == "", (
            f"Expected a quiet no-op with no HALT present.\nresolution-stage log:\n{stage_log}"
        )

        action_log_path = proj / "overwatch" / "actions.log"
        action_log_text = action_log_path.read_text(encoding="utf-8") if action_log_path.is_file() else ""
        assert action_log_text == "", (
            f"A no-HALT firing must write nothing to the action log.\n{action_log_text}"
        )

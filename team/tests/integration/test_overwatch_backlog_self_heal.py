"""
test_overwatch_backlog_self_heal.py
====================================
The overwatch wake keeps no queue file: driven through the REAL production
entry point (bash scripts/wake/claude.sh --agent=overwatch), a project with
no tasks/queues/overwatch_backlog.md is neither skipped nor given one. The
fixture builders are shared with test_overwatch_wake_driven_fixtures.py,
which builds no OVERWATCH queue file at all.

Coverage:
  - HALT present, no overwatch_backlog.md: the resolution stage still runs
    to completion (the same CANCEL-AND-REBUNDLE classification
    test_overwatch_wake_driven_fixtures.py's golden fixture exercises), and
    the wake creates no queue file.
  - No HALT, no overwatch_backlog.md: a clean idle exit that creates no
    queue file and writes nothing to the action log.

Design notes:
  - All temp paths use pytest's tmp_path. No bare /tmp paths appear in this
    file.
  - Every wake invocation goes through the real
    `bash .../scripts/wake/claude.sh --agent=overwatch --max-tasks=1`
    subprocess.
"""

from __future__ import annotations

import pathlib

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


class TestOverwatchWakeNeedsNoQueueFile:
    def test_halt_present_and_no_queue_file_still_classifies_the_block(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_golden_fixture(tmp_path, "wd-no-queue")
        backlog = _backlog_path(fx.kanban_root, fx.proj.name)
        assert not backlog.exists(), "Precondition: the fixture builds no OVERWATCH queue file."

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, (
            f"wake/claude.sh --agent=overwatch exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        assert not backlog.exists(), (
            "The overwatch wake must not create a queue file.\n"
            f"stderr:\n{result.stderr}"
        )
        assert "overwatch backlog not found" not in result.stderr, (
            f"A missing OVERWATCH queue file must not skip the project.\nstderr:\n{result.stderr}"
        )

        # Not skipped past: the resolution stage ran to completion and
        # classified/resolved the block, exactly as
        # test_overwatch_wake_driven_fixtures.py's golden fixture asserts.
        stage_log = _resolution_stage_log(fx.kanban_root)
        assert "status=executed" in stage_log, f"resolution-stage log:\n{stage_log}"
        ledger_text = fx.ledger_path.read_text(encoding="utf-8")
        assert ledger_text.count("## Entry:") == 1
        assert "CANCEL-AND-REBUNDLE" in ledger_text
        assert not fx.halt_path.exists(), "The ship-policy HALT this row resolves should be cleared."

    def test_no_halt_and_no_queue_file_is_a_clean_idle_exit(self, tmp_path: pathlib.Path) -> None:
        project_name = "wd-idle-no-halt"
        repo_path = tmp_path / f"dev_repo_{project_name}"
        _init_git_repo(repo_path, "ai_main", "ai_rc/v2.0.1")
        kanban_root = _build_kanban_root(tmp_path, project_name, repo_path)
        proj = kanban_root / "projects" / project_name
        _write_release_state_md(proj, "none")
        backlog = _backlog_path(kanban_root, project_name)

        result = _run_wake(kanban_root, "overwatch")
        assert result.returncode == 0, (
            f"wake/claude.sh --agent=overwatch exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        assert not backlog.exists(), "An idle overwatch wake must not create a queue file."

        stage_log = _resolution_stage_log(kanban_root)
        assert "no HALT file present" in stage_log or stage_log == "", (
            f"Expected a quiet no-op with no HALT present.\nresolution-stage log:\n{stage_log}"
        )

        action_log_path = proj / "overwatch" / "actions.log"
        action_log_text = action_log_path.read_text(encoding="utf-8") if action_log_path.is_file() else ""
        assert action_log_text == "", (
            f"A no-HALT firing must write nothing to the action log.\n{action_log_text}"
        )

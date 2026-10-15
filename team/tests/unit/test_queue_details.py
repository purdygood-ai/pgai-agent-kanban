"""
test_queue_details.py
======================
Behavioral unit tests for team/pgai_agent_kanban/dashboard/queue_details.py
-- the shared resolver behind show-queues.sh's ``--details`` renderer and
GET /show-queues?details=true.

All filesystem operations use tmp_path; no live kanban root, no
subprocess calls.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.dashboard.queue_details import (
    AGENT_ORDER,
    STATE_ORDER,
    resolve_queue_details,
)


def _write_task(tasks_root: pathlib.Path, task_id: str, state: str, role: "str | None") -> None:
    task_dir = tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(f"## State\n{state}\n", encoding="utf-8")
    if role is not None:
        (task_dir / "README.md").write_text(f"## Role\n{role}\n", encoding="utf-8")


def _write_queue_line(queues_dir: pathlib.Path, agent_file: str, marker: str, task_id: str) -> None:
    queues_dir.mkdir(parents=True, exist_ok=True)
    path = queues_dir / f"{agent_file}_backlog.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(existing + f"- [{marker}] {task_id}\n", encoding="utf-8")


class TestResolveQueueDetails:
    def test_every_agent_and_state_key_is_always_present(self, tmp_path: pathlib.Path) -> None:
        """An empty tasks_root still returns the full AGENT_ORDER x STATE_ORDER
        shape (all empty lists) -- callers never need to guess a missing key
        means "not scanned"."""
        result = resolve_queue_details(tmp_path / "tasks", None)
        assert set(result.keys()) == set(AGENT_ORDER)
        for agent in AGENT_ORDER:
            assert set(result[agent].keys()) == set(STATE_ORDER)
            for state in STATE_ORDER:
                assert result[agent][state] == []

    def test_done_and_wontdo_tasks_are_excluded(self, tmp_path: pathlib.Path) -> None:
        tasks_root = tmp_path / "tasks"
        _write_task(tasks_root, "CODER-20260101-001-done-task", "DONE", "CODER")
        _write_task(tasks_root, "CODER-20260101-002-wontdo-task", "WONT-DO", "CODER")
        _write_task(tasks_root, "CODER-20260101-003-working-task", "WORKING", "CODER")

        result = resolve_queue_details(tasks_root, None)

        working_ids = {e["task_id"] for e in result["coder"]["WORKING"]}
        assert working_ids == {"CODER-20260101-003-working-task"}
        all_ids = {
            e["task_id"]
            for agent in AGENT_ORDER
            for state in STATE_ORDER
            for e in result[agent][state]
        }
        assert "CODER-20260101-001-done-task" not in all_ids
        assert "CODER-20260101-002-wontdo-task" not in all_ids

    def test_agent_is_resolved_from_readme_role_field(self, tmp_path: pathlib.Path) -> None:
        tasks_root = tmp_path / "tasks"
        _write_task(tasks_root, "WRITER-20260101-001-a-doc", "BLOCKED", "WRITER")
        _write_task(tasks_root, "CODER-20260101-002-no-readme", "BACKLOG", None)

        result = resolve_queue_details(tasks_root, None)

        assert result["writer"]["BLOCKED"][0]["task_id"] == "WRITER-20260101-001-a-doc"
        # No README.md at all -- agent falls back to "unknown", never guessed
        # from the task ID prefix.
        assert result["unknown"]["BACKLOG"][0]["task_id"] == "CODER-20260101-002-no-readme"

    def test_active_task_with_no_queue_entry_warns_no_queue_entry(
        self, tmp_path: pathlib.Path
    ) -> None:
        tasks_root = tmp_path / "tasks"
        _write_task(tasks_root, "CODER-20260101-001-orphan", "WORKING", "CODER")

        result = resolve_queue_details(tasks_root, tmp_path / "queues")

        entry = result["coder"]["WORKING"][0]
        assert entry == {"task_id": "CODER-20260101-001-orphan", "warning": "no_queue_entry"}

    def test_queue_marker_and_status_state_agreeing_carries_no_warning(
        self, tmp_path: pathlib.Path
    ) -> None:
        tasks_root = tmp_path / "tasks"
        queues_dir = tmp_path / "queues"
        _write_task(tasks_root, "CODER-20260101-001-clean", "WORKING", "CODER")
        _write_queue_line(queues_dir, "coder", "A", "CODER-20260101-001-clean")

        result = resolve_queue_details(tasks_root, queues_dir)

        entry = result["coder"]["WORKING"][0]
        assert entry == {"task_id": "CODER-20260101-001-clean", "warning": None}

    def test_queue_marker_status_disagreement_warns_marker_mismatch(
        self, tmp_path: pathlib.Path
    ) -> None:
        tasks_root = tmp_path / "tasks"
        queues_dir = tmp_path / "queues"
        # Queue says BLOCKED ("B"), status.md says WORKING -- a real disagreement.
        _write_task(tasks_root, "CODER-20260101-001-mismatch", "WORKING", "CODER")
        _write_queue_line(queues_dir, "coder", "B", "CODER-20260101-001-mismatch")

        result = resolve_queue_details(tasks_root, queues_dir)

        entry = result["coder"]["WORKING"][0]
        assert entry == {"task_id": "CODER-20260101-001-mismatch", "warning": "marker_mismatch"}

    def test_done_queue_marker_with_still_active_status_warns_marker_mismatch(
        self, tmp_path: pathlib.Path
    ) -> None:
        tasks_root = tmp_path / "tasks"
        queues_dir = tmp_path / "queues"
        _write_task(tasks_root, "CODER-20260101-001-stale-done", "WORKING", "CODER")
        _write_queue_line(queues_dir, "coder", "x", "CODER-20260101-001-stale-done")

        result = resolve_queue_details(tasks_root, queues_dir)

        entry = result["coder"]["WORKING"][0]
        assert entry["warning"] == "marker_mismatch"

    def test_queue_entry_with_no_task_folder_warns_folder_missing(
        self, tmp_path: pathlib.Path
    ) -> None:
        tasks_root = tmp_path / "tasks"
        queues_dir = tmp_path / "queues"
        tasks_root.mkdir(parents=True, exist_ok=True)
        _write_queue_line(queues_dir, "coder", " ", "CODER-20260101-001-ghost")

        result = resolve_queue_details(tasks_root, queues_dir)

        entry = result["coder"]["BACKLOG"][0]
        assert entry == {"task_id": "CODER-20260101-001-ghost", "warning": "folder_missing"}

    def test_unrecognized_state_is_never_guessed_into_a_canonical_bucket(
        self, tmp_path: pathlib.Path
    ) -> None:
        tasks_root = tmp_path / "tasks"
        _write_task(tasks_root, "CODER-20260101-001-typo", "WROKING", "CODER")

        result = resolve_queue_details(tasks_root, None)

        entry = result["coder"]["BACKLOG"][0]
        assert entry == {"task_id": "CODER-20260101-001-typo", "warning": "unknown_state:WROKING"}

    def test_none_queues_dir_treats_every_active_task_as_unreconciled(
        self, tmp_path: pathlib.Path
    ) -> None:
        """queues_dir=None (no reconciliation data available) means every
        active task warns no_queue_entry -- never silently "no warning"."""
        tasks_root = tmp_path / "tasks"
        _write_task(tasks_root, "CODER-20260101-001-solo", "BACKLOG", "CODER")

        result = resolve_queue_details(tasks_root, None)

        entry = result["coder"]["BACKLOG"][0]
        assert entry["warning"] == "no_queue_entry"

    def test_entries_within_a_state_are_sorted_by_task_id(self, tmp_path: pathlib.Path) -> None:
        tasks_root = tmp_path / "tasks"
        _write_task(tasks_root, "CODER-20260101-002-second", "WAITING", "CODER")
        _write_task(tasks_root, "CODER-20260101-001-first", "WAITING", "CODER")

        result = resolve_queue_details(tasks_root, None)

        ids = [e["task_id"] for e in result["coder"]["WAITING"]]
        assert ids == ["CODER-20260101-001-first", "CODER-20260101-002-second"]

"""
test_archive_plan.py
====================
Unit tests for pgai_agent_kanban.archive.plan — choosing which finished
items an archive run would move:

  - Each category keeps its newest ``count`` items live, and tasks keep
    ``count`` per role; only older items are candidates. Bugs and
    priorities order by number, requirements by semver (never as text),
    tasks within a role by release version (semver), then date and
    sequence; a task with no release version sits where it was created. A
    prefixed (``ai_v1.3.0``) or marked-up release version reads as that
    release.
  - An older item is selected only when it is finished, by each category's
    own rule; an unknown or unreadable status is never selected.
  - An older finished item stays live while live work references it: a
    prerequisite of an unfinished task, an item in an open bundle, a
    requirement or task of the Active RC. An unreadable release-state.md
    holds every task and requirement.
  - Queue lines and training logs follow the items they name.
  - ``count`` 0 is an empty plan, and planning changes nothing on disk.

Every test builds its own sandbox kanban root under pytest's tmp_path
(tests/fixtures/archive_sandbox.py) and passes it to plan() explicitly;
nothing here reads or writes the live install.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from pgai_agent_kanban.archive import ArchiveLayoutError, plan
from pgai_agent_kanban.archive.plan import (
    ACTIVE_RC_UNKNOWN,
    HOLD_ACTIVE_RC_UNKNOWN,
    HOLD_IN_ACTIVE_RC,
    HOLD_IN_OPEN_BUNDLE,
    HOLD_NOT_RELEASED,
    HOLD_PREREQUISITE,
    HOLD_STATUS_UNREADABLE,
    HOLD_UNFINISHED,
)
from tests.fixtures.archive_sandbox import ArchiveSandbox, build_acceptance_scenario


def _held_codes(result, category: str) -> dict:
    """item_id -> hold code for the held items of *category*."""
    return {h.item_id: h.code for h in result.held if h.category == category}


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> ArchiveSandbox:
    """An empty sandbox project: no Active RC, v1.0.0 released."""
    return ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")


@pytest.fixture
def scenario(tmp_path: pathlib.Path):
    """The acceptance fixture: 73 bugs, 80 tasks, archive_count = 55."""
    return build_acceptance_scenario(tmp_path)


# ---------------------------------------------------------------------------
# The acceptance scenario
# ---------------------------------------------------------------------------


def test_acceptance_selects_exactly_the_eighteen_oldest_done_bugs(scenario) -> None:
    result = plan(scenario.sandbox.project, kanban_root=scenario.sandbox.root)

    assert result.count == scenario.archive_count, "count should resolve from the sandbox kanban.cfg"
    assert result.ids_in("bugs") == scenario.expected_bug_ids


def test_acceptance_keeps_open_bugs_and_the_highest_numbered_bug_live(scenario) -> None:
    selected = set(plan(scenario.sandbox.project, kanban_root=scenario.sandbox.root).ids_in("bugs"))

    for number in scenario.open_bug_numbers:
        assert f"BUG-{number:04d}" not in selected, f"open bug {number} was selected"
    assert f"BUG-{max(scenario.bug_numbers):04d}" not in selected, "the highest-numbered bug must stay live"


def test_acceptance_never_selects_unfinished_or_active_rc_tasks(scenario) -> None:
    result = plan(scenario.sandbox.project, kanban_root=scenario.sandbox.root)
    selected = set(result.ids_in("tasks"))
    held = _held_codes(result, "tasks")

    assert result.ids_in("tasks") == scenario.expected_task_ids
    for task_id in scenario.unfinished_task_ids:
        assert task_id not in selected
        assert held[task_id] == HOLD_UNFINISHED
    for task_id in scenario.active_rc_task_ids:
        assert task_id not in selected
        assert held[task_id] == HOLD_IN_ACTIVE_RC


def test_acceptance_queue_lines_and_training_logs_follow_selected_items(scenario) -> None:
    sandbox = scenario.sandbox
    result = plan(sandbox.project, kanban_root=sandbox.root)

    followed = {line.item_id for line in result.queue_lines}
    assert followed == set(scenario.expected_bug_ids) | set(scenario.expected_task_ids)
    for line in result.queue_lines:
        lines = line.queue_file.read_text(encoding="utf-8").splitlines()
        assert lines[line.line_number - 1] == line.text, "line_number must point at the planned text"
    assert {log.task_id for log in result.training_logs} == set(scenario.expected_task_ids)
    for task_id in scenario.expected_task_ids:
        logs = result.training_logs_for(task_id)
        assert len(logs) == 1 and task_id in logs[0].path.name
    for task_id in scenario.unfinished_task_ids + scenario.active_rc_task_ids:
        assert not result.queue_lines_for(task_id), f"a line of held task {task_id} was planned to move"


def test_plan_leaves_the_sandbox_byte_for_byte_unchanged(scenario) -> None:
    sandbox = scenario.sandbox
    before = sandbox.snapshot()

    result = plan(sandbox.project, kanban_root=sandbox.root)

    assert not result.is_empty
    assert sandbox.snapshot() == before, "plan() must not create, move or rewrite anything"
    assert not (sandbox.root / "archive").exists()


# ---------------------------------------------------------------------------
# archive_count = 0
# ---------------------------------------------------------------------------


def test_count_zero_argument_is_an_empty_plan(scenario) -> None:
    result = plan(scenario.sandbox.project, 0, kanban_root=scenario.sandbox.root)

    assert result.is_empty
    assert result.items == () and result.held == ()


def test_count_zero_in_kanban_cfg_is_an_empty_plan_and_changes_nothing(scenario) -> None:
    sandbox = scenario.sandbox
    sandbox.set_archive_count(0)
    before = sandbox.snapshot()

    result = plan(sandbox.project, kanban_root=sandbox.root)

    assert result.count == 0 and result.is_empty
    assert sandbox.snapshot() == before


def test_negative_count_is_refused(sandbox) -> None:
    with pytest.raises(ValueError, match="count"):
        plan(sandbox.project, -1, kanban_root=sandbox.root)


def test_unsafe_project_name_is_refused_before_any_read(sandbox) -> None:
    with pytest.raises(ArchiveLayoutError):
        plan("../escape", 1, kanban_root=sandbox.root)


def test_project_with_no_items_is_an_empty_plan(sandbox) -> None:
    assert plan(sandbox.project, 1, kanban_root=sandbox.root).is_empty


# ---------------------------------------------------------------------------
# Ordering: newest count stay live
# ---------------------------------------------------------------------------


def test_bugs_order_by_number_not_by_file_name(sandbox) -> None:
    # As text the five-digit bug file sorts before the four-digit one; by number it is newer.
    sandbox.add_bug(9999, "done")
    sandbox.add_bug(10000, "done")

    assert plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("bugs") == ("BUG-9999",)


def test_priorities_order_by_number_and_newest_stay_live(sandbox) -> None:
    for number in (1, 2, 3):
        sandbox.add_priority(number, "done")

    assert plan(sandbox.project, 2, kanban_root=sandbox.root).ids_in("priority") == ("PRIORITY-0001",)


def test_requirements_order_by_semver_not_by_text(sandbox) -> None:
    # As text v0.9.7 is the newest; by semver v0.17.1 is.
    for version in ("v0.9.7", "v0.17.1", "v0.10.0"):
        sandbox.add_requirement(version, "done")

    selected = plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("requirements")

    assert selected == ("v0.9.7-feature.md", "v0.10.0-feature.md")


def test_tasks_order_by_date_then_sequence_within_a_role(sandbox) -> None:
    newest = sandbox.add_task("PM-20260102-001-newest").name
    second = sandbox.add_task("PM-20260101-002-second").name
    oldest = sandbox.add_task("PM-20260101-001-oldest").name

    selected = plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("tasks")

    assert selected == (oldest, second)
    assert newest not in selected


def test_each_role_keeps_its_own_newest_count_of_tasks(sandbox) -> None:
    # PM numbers its tasks from its own counter; CODER and CM share one that
    # runs far higher on the same day.
    sandbox.set_archive_count(5)
    pm = [sandbox.add_task(ArchiveSandbox.task_id("PM", n, date="20261011")).name for n in range(1, 11)]
    shared = {"CODER": [], "CM": []}
    for n in range(1, 71):
        role = "CODER" if n % 2 else "CM"
        shared[role].append(sandbox.add_task(ArchiveSandbox.task_id(role, n, date="20261011")).name)

    result = plan(sandbox.project, kanban_root=sandbox.root)
    selected = result.ids_in("tasks")

    assert result.count == 5, "count should resolve from the sandbox kanban.cfg"
    assert [t for t in selected if t.startswith("PM-")] == pm[:5]
    for role, ids in shared.items():
        assert [t for t in selected if t.startswith(f"{role}-")] == ids[:-5], f"{role} kept the wrong tasks"
    live = set(pm + shared["CODER"] + shared["CM"]) - set(selected)
    assert {t for t in live if t.startswith("PM-")} == set(pm[5:]), "PM keeps -006 ... -010 live"
    assert len(live) == 15, "five newest of each of the three roles stay live"


def test_tasks_order_by_release_version_before_date_within_a_role(sandbox) -> None:
    # Created oldest first, but the first task belongs to the newest release.
    # As text v1.9.0 would be the newest; by semver v1.10.0 is.
    newest_release = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), release_version="v1.10.0").name
    middle = sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), release_version="v1.9.0").name
    oldest_release = sandbox.add_task(ArchiveSandbox.task_id("CODER", 3), release_version="v1.2.0").name

    selected = plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("tasks")

    assert selected == (oldest_release, middle)
    assert newest_release not in selected


@pytest.mark.parametrize("written", ["ai_v1.3.0", "`v1.3.0`", "**v1.3.0**"])
def test_a_prefixed_or_marked_up_release_version_orders_as_that_release(sandbox, written: str) -> None:
    # Created first, so by date alone it would be the oldest; read as v1.3.0
    # it is the newest release and stays live.
    marked = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), release_version=written).name
    older = sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), release_version="v1.2.0").name

    selected = plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("tasks")

    assert selected == (older,)
    assert marked not in selected


def test_a_prefixed_release_version_of_the_active_rc_stays_live(tmp_path: pathlib.Path) -> None:
    sandbox = ArchiveSandbox(tmp_path, active_rc="v2.0.0", last_released="v1.9.0")
    in_rc = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE", release_version="ai_v2.0.0").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE", release_version="v2.0.0")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert _held_codes(result, "tasks") == {in_rc: HOLD_IN_ACTIVE_RC}


def test_task_without_release_version_sits_where_it_was_created(sandbox) -> None:
    first = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), release_version="none").name
    second = sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), release_version="v1.0.0").name
    third = sandbox.add_task(ArchiveSandbox.task_id("CODER", 3), release_version="v2.0.0").name
    latest = sandbox.add_task(ArchiveSandbox.task_id("CODER", 4), release_version="none").name
    older_line = sandbox.add_task(ArchiveSandbox.task_id("CODER", 5), release_version="v1.5.0").name

    selected = plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("tasks")

    # The first has no versioned task before it, so it is the oldest; the
    # latest unversioned one follows v2.0.0, created before it, and stays live.
    assert selected == (first, second, older_line, third)
    assert latest not in selected


def test_newest_count_stay_live_even_when_finished(sandbox) -> None:
    for number in range(1, 6):
        sandbox.add_bug(number, "done")

    result = plan(sandbox.project, 5, kanban_root=sandbox.root)

    assert result.is_empty and result.held == ()


def test_unfinished_items_still_count_toward_the_newest(sandbox) -> None:
    sandbox.add_bug(1, "done")
    sandbox.add_bug(2, "done")
    sandbox.add_bug(3, "open")

    # Bug 3 is the newest; it fills the one live slot although it is open.
    assert plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("bugs") == ("BUG-0001", "BUG-0002")


# ---------------------------------------------------------------------------
# What "finished" means, per category
# ---------------------------------------------------------------------------


def test_bug_finished_statuses(sandbox) -> None:
    statuses = ["done", "wont-do", "duplicate", "rejected", "superseded", "Done", "open", "running"]
    for number, status in enumerate(statuses, start=1):
        sandbox.add_bug(number, status)
    sandbox.add_bug(len(statuses) + 1, "done")  # the one live slot

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("bugs") == tuple(f"BUG-{n:04d}" for n in range(1, 7))
    assert _held_codes(result, "bugs") == {"BUG-0007": HOLD_UNFINISHED, "BUG-0008": HOLD_UNFINISHED}


def test_priority_finished_statuses(sandbox) -> None:
    for number, status in enumerate(["done", "wont-do", "duplicate", "open"], start=1):
        sandbox.add_priority(number, status)
    sandbox.add_priority(5, "done")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("priority") == ("PRIORITY-0001", "PRIORITY-0002")
    assert _held_codes(result, "priority") == {
        "PRIORITY-0003": HOLD_UNFINISHED,
        "PRIORITY-0004": HOLD_UNFINISHED,
    }


def test_requirement_selected_only_when_done_or_shipped_and_released(tmp_path: pathlib.Path) -> None:
    sandbox = ArchiveSandbox(tmp_path, active_rc="v1.4.0", last_released="v1.3.0")
    sandbox.add_requirement("v1.1.0", "done", slug="done-released")
    sandbox.add_requirement("v1.2.0", "shipped", slug="shipped-released")
    sandbox.add_requirement("v1.3.0", "done", slug="done-at-last-released")
    sandbox.add_requirement("v1.0.0", "running", slug="running")
    sandbox.add_requirement("v1.0.1", "wont-do", slug="wont-do")
    sandbox.add_requirement("v1.0.2", "done", slug="target-unreleased", target_version="v1.5.0")
    sandbox.add_requirement("v1.0.3", "done", slug="target-is-active-rc", target_version="v1.4.0")
    sandbox.add_requirement("v9.9.9", "running", slug="newest")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("requirements") == (
        "v1.1.0-done-released.md",
        "v1.2.0-shipped-released.md",
        "v1.3.0-done-at-last-released.md",
    )
    assert _held_codes(result, "requirements") == {
        "v1.0.0-running.md": HOLD_UNFINISHED,
        "v1.0.1-wont-do.md": HOLD_UNFINISHED,
        "v1.0.2-target-unreleased.md": HOLD_NOT_RELEASED,
        "v1.0.3-target-is-active-rc.md": HOLD_IN_ACTIVE_RC,
    }


def test_requirement_is_not_selected_when_nothing_was_released(tmp_path: pathlib.Path) -> None:
    sandbox = ArchiveSandbox(tmp_path, active_rc="none", last_released="none")
    sandbox.add_requirement("v0.1.0", "done", slug="old")
    sandbox.add_requirement("v0.2.0", "done", slug="new")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("requirements") == ()
    assert _held_codes(result, "requirements") == {"v0.1.0-old.md": HOLD_NOT_RELEASED}


def test_task_finished_states(sandbox) -> None:
    states = ["DONE", "WONT-DO", "done", "WORKING", "BACKLOG", "WAITING", "BLOCKED"]
    for sequence, state in enumerate(states, start=1):
        sandbox.add_task(ArchiveSandbox.task_id("CODER", sequence), state)
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 99), "DONE")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("tasks") == tuple(ArchiveSandbox.task_id("CODER", s) for s in (1, 2, 3))
    assert set(_held_codes(result, "tasks").values()) == {HOLD_UNFINISHED}


# ---------------------------------------------------------------------------
# Fail closed: unknown or unreadable status stays live
# ---------------------------------------------------------------------------


def test_unknown_or_unreadable_intake_status_is_never_selected(sandbox) -> None:
    sandbox.add_bug(1, None, slug="no-status-field")
    sandbox.add_bug(2, "", slug="empty-status")
    sandbox.add_bug(3, "pending-triage", slug="unknown-status")
    undecodable = sandbox.add_bug(4, "done", slug="undecodable")
    undecodable.write_bytes(b"## Status\ndone\n\xff\xfe\n")
    sandbox.add_bug(5, "done")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("bugs") == ()
    assert _held_codes(result, "bugs") == {
        "BUG-0001": HOLD_STATUS_UNREADABLE,
        "BUG-0002": HOLD_STATUS_UNREADABLE,
        "BUG-0003": HOLD_UNFINISHED,
        "BUG-0004": HOLD_STATUS_UNREADABLE,
    }


def test_task_with_unreadable_state_is_never_selected(sandbox) -> None:
    no_state = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), None).name
    no_status_file = sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")
    (no_status_file / "status.md").unlink()
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 3), "DONE")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("tasks") == ()
    assert _held_codes(result, "tasks") == {
        no_state: HOLD_STATUS_UNREADABLE,
        no_status_file.name: HOLD_STATUS_UNREADABLE,
    }


def test_requirement_without_a_readable_target_version_is_never_selected(sandbox) -> None:
    sandbox.add_requirement("v0.1.0", "done", slug="bad-target", target_version="next")
    sandbox.add_requirement("v0.2.0", "done", slug="newest")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert _held_codes(result, "requirements") == {"v0.1.0-bad-target.md": HOLD_STATUS_UNREADABLE}


def test_invalid_release_state_holds_tasks_and_requirements_but_not_bugs(sandbox) -> None:
    sandbox.set_release_state(text="# Release State\n\n## Active RC\nNONE\n\n## RC Opened At\nnone\n\n"
                                   "## RC Opened By Task\nnone\n\n## Last Released\nv1.0.0\n")
    old_task = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")
    sandbox.add_requirement("v0.1.0", "done", slug="old")
    sandbox.add_requirement("v0.2.0", "done", slug="new")
    sandbox.add_bug(1, "done")
    sandbox.add_bug(2, "done")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.active_rc == ACTIVE_RC_UNKNOWN
    assert result.ids_in("tasks") == () and result.ids_in("requirements") == ()
    assert _held_codes(result, "tasks") == {old_task: HOLD_ACTIVE_RC_UNKNOWN}
    assert _held_codes(result, "requirements") == {"v0.1.0-old.md": HOLD_ACTIVE_RC_UNKNOWN}
    assert result.ids_in("bugs") == ("BUG-0001",)
    assert any("release-state.md" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Live references keep finished items live
# ---------------------------------------------------------------------------


def test_prerequisite_of_an_unfinished_task_stays_live(sandbox) -> None:
    needed = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE").name
    free = sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 3), "WORKING", prerequisites=[needed])

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("tasks") == (free,)
    assert _held_codes(result, "tasks") == {needed: HOLD_PREREQUISITE}


def test_prerequisite_of_a_finished_task_does_not_hold(sandbox) -> None:
    needed = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE", prerequisites=[needed])

    assert plan(sandbox.project, 1, kanban_root=sandbox.root).ids_in("tasks") == (needed,)


def test_prerequisite_written_with_a_trailing_note_still_holds(sandbox) -> None:
    needed = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE").name
    sandbox.add_task(
        ArchiveSandbox.task_id("CODER", 2), "BLOCKED", prerequisites=[f"{needed} (opens the RC),"]
    )

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert _held_codes(result, "tasks") == {needed: HOLD_PREREQUISITE}


def test_items_in_an_open_bundle_stay_live(sandbox) -> None:
    in_running = sandbox.add_bug(1, "done")
    in_unset = sandbox.add_bug(2, "done")
    in_shipped = sandbox.add_bug(3, "done")
    in_done = sandbox.add_bug(4, "done")
    priority = sandbox.add_priority(1, "done")
    sandbox.add_priority(2, "done")
    sandbox.add_bug(5, "done")
    sandbox.add_requirement("v2.0.0", "running", slug="bundle-a", bundled=[in_running, priority])
    sandbox.add_requirement("v2.0.1", None, slug="bundle-b", bundled=[in_unset])
    sandbox.add_requirement("v0.5.0", "shipped", slug="bundle-c", bundled=[in_shipped])
    sandbox.add_requirement("v0.6.0", "done", slug="bundle-d", bundled=[in_done])

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("bugs") == ("BUG-0003", "BUG-0004")
    assert _held_codes(result, "bugs") == {"BUG-0001": HOLD_IN_OPEN_BUNDLE, "BUG-0002": HOLD_IN_OPEN_BUNDLE}
    assert _held_codes(result, "priority") == {"PRIORITY-0001": HOLD_IN_OPEN_BUNDLE}


def test_finished_task_of_the_active_rc_stays_live(tmp_path: pathlib.Path) -> None:
    sandbox = ArchiveSandbox(tmp_path, active_rc="v2.0.0", last_released="v1.9.0")
    in_rc = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE", release_version="v2.0.0").name
    shipped = sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE", release_version="v1.9.0").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 3), "DONE", release_version="v2.0.0")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("tasks") == (shipped,)
    assert _held_codes(result, "tasks") == {in_rc: HOLD_IN_ACTIVE_RC}


def test_task_without_readme_stays_live_while_an_rc_is_active(tmp_path: pathlib.Path) -> None:
    sandbox = ArchiveSandbox(tmp_path, active_rc="v2.0.0", last_released="v1.9.0")
    unknown = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE", readme=False).name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("tasks") == ()
    assert unknown in _held_codes(result, "tasks")


# ---------------------------------------------------------------------------
# Followers
# ---------------------------------------------------------------------------


def test_queue_lines_follow_by_id_whatever_the_marker(sandbox) -> None:
    task = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")
    bug = sandbox.add_bug(1, "done").stem
    sandbox.add_bug(2, "done")
    expected = {
        sandbox.add_queue_line("coder", task, marker="x", description="first"),
        sandbox.add_queue_line("tester", task, marker="", description="the [] alias"),
        sandbox.add_queue_line("bug", bug, marker="x"),
    }
    # A line in a file that is not an agent backlog never follows.
    (sandbox.project_root / "tasks" / "queues" / "notes.md").write_text(f"- [x] {task}\n", encoding="utf-8")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert {line.text for line in result.queue_lines} == expected
    assert {line.item_id for line in result.queue_lines} == {task, "BUG-0001"}
    assert all(line.queue_file.name.endswith("_backlog.md") for line in result.queue_lines)


def test_queue_lines_follow_whatever_the_brackets_hold(sandbox) -> None:
    task = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")
    expected = {
        sandbox.add_queue_line("coder", task, marker=" x "),
        sandbox.add_queue_line("writer", task, marker="X"),
    }

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert {line.text for line in result.queue_lines} == expected


def test_queue_lines_of_held_items_do_not_follow(sandbox) -> None:
    held = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "WORKING").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")
    sandbox.add_queue_line("coder", held, marker="A")

    assert plan(sandbox.project, 1, kanban_root=sandbox.root).queue_lines == ()


def test_training_log_follows_only_the_task_whose_id_it_names_whole(sandbox) -> None:
    selected = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1, slug="archive"), "DONE").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")
    own_log = sandbox.add_training_log("coder", selected)
    # Same head, longer slug: a different task's log.
    sandbox.add_training_log("coder", f"{selected}-plan")
    other_role_log = sandbox.add_training_log("tester", selected, stamp="20260302T000000")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert {log.path for log in result.training_logs} == {own_log, other_role_log}
    assert {log.role for log in result.training_logs} == {"coder", "tester"}
    assert {log.relpath for log in result.training_logs} == {
        own_log.relative_to(sandbox.project_root).as_posix(),
        other_role_log.relative_to(sandbox.project_root).as_posix(),
    }


def test_planned_items_carry_their_live_relative_path(sandbox) -> None:
    sandbox.add_bug(1, "done")
    sandbox.add_bug(2, "done")
    task = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE").name
    sandbox.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert {item.relpath for item in result.items} == {"bugs/BUG-0001-item-1.md", f"tasks/{task}"}
    assert all(item.path == sandbox.project_root / item.relpath for item in result.items)


def test_plan_renders_as_json(scenario) -> None:
    rendered = plan(scenario.sandbox.project, kanban_root=scenario.sandbox.root).to_dict()

    decoded = json.loads(json.dumps(rendered))
    assert [row["item_id"] for row in decoded["items"] if row["category"] == "bugs"] == list(
        scenario.expected_bug_ids
    )
    assert decoded["active_rc"] == "v1.4.0" and decoded["last_released"] == "v1.3.0"

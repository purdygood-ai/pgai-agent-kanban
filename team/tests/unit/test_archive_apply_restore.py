"""
test_archive_apply_restore.py
=============================
Unit tests for pgai_agent_kanban.archive.apply — moving a plan's items into
the archive, and moving one item back:

  - On the acceptance fixture, exactly the planned items move to the same
    relative paths under archive/<project>/, their queue lines (markers
    unchanged) and training logs follow, and the ledger holds one row per
    move; unfinished and Active-RC work stays live.
  - A second apply — of a fresh plan or of the same plan — moves nothing
    and writes no ledger rows.
  - apply takes the project's repo lock with full provenance; when another
    process holds it nothing moves and a 'skipped' result names the holder.
    With accept_chain_lock, a lock whose recorded holder is this process
    or an ancestor (the wake chain the caller runs inside) is worked under
    and left as it was; a lock held by any other live process still skips.
  - The cross-filesystem branch (copy, fsync, rename, remove) gives the
    same result as a rename.
  - A failure stops the run at the failing item; items already moved keep
    matching their ledger rows, and a re-run finishes the job.
  - A task named in two queue files leaves both, each line reaching the
    archive queue file of the same name; a line whose brackets hold more
    than one character (a hand edit) follows its task as well.
  - restore brings an item back with its queue lines and training logs and
    writes a 'restore' row; an ID that is not archived, or whose live path
    is taken, is refused dual-channel with nothing moved.

Every test builds its own sandbox kanban root under pytest's tmp_path
(tests/fixtures/archive_sandbox.py) and passes it explicitly; nothing here
reads or writes the live install.
"""

from __future__ import annotations

import errno
import importlib
import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.archive import (
    CATEGORY_RESTORE,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_SKIPPED,
    ArchiveRestoreError,
    apply,
    plan,
    read_rows,
    restore,
)
from pgai_agent_kanban.archive import move as move_module
from pgai_agent_kanban.archive.ledger import LedgerRow, append_row
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.locks import acquire_lock, read_provenance, repo_lock_path
from tests.fixtures.archive_sandbox import ArchiveSandbox, build_acceptance_scenario

# The package exports the function apply(), which hides the submodule of the
# same name as a package attribute; the module itself is reached by name.
apply_module = importlib.import_module("pgai_agent_kanban.archive.apply")


def _quiet(_line: str) -> None:
    """A log sink that keeps run narration out of the test output."""


@pytest.fixture
def scenario(tmp_path: pathlib.Path):
    """The acceptance fixture: 73 bugs, 80 tasks, archive_count = 55."""
    return build_acceptance_scenario(tmp_path)


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> ArchiveSandbox:
    """A small project: archive_count 1, two done bugs, two done tasks with followers."""
    box = ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")
    box.set_archive_count(1)
    for number in (1, 2):
        bug = box.add_bug(number, "done")
        box.add_queue_line("bug", bug.stem, marker="x")
    for sequence in (1, 2):
        task_id = ArchiveSandbox.task_id("CODER", sequence)
        box.add_task(task_id, "DONE")
        box.add_queue_line("coder", task_id, marker="x", description=f"task {sequence}")
        box.add_training_log("coder", task_id)
    return box


def _archive_root(box: ArchiveSandbox) -> pathlib.Path:
    return box.root / "archive" / box.project


def _ledger(box: ArchiveSandbox) -> tuple[LedgerRow, ...]:
    return read_rows(_archive_root(box) / "ARCHIVE-LEDGER.tsv")


def _plan(box: ArchiveSandbox):
    return plan(box.project, kanban_root=box.root)


# ---------------------------------------------------------------------------
# The acceptance scenario
# ---------------------------------------------------------------------------


def test_acceptance_moves_exactly_the_planned_items_to_the_same_relative_paths(scenario) -> None:
    box = scenario.sandbox
    planned = _plan(box)

    result = apply(planned, log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert set(result.moved_ids) == {item.item_id for item in planned.items}
    for item in planned.items:
        assert not item.path.exists(), f"{item.item_id} is still live at {item.path}"
        assert (_archive_root(box) / item.relpath).exists(), f"{item.item_id} missing from the archive"
    assert tuple(r for r in result.moved_ids if r.startswith("BUG-")) == scenario.expected_bug_ids


def test_acceptance_ledger_rows_match_the_moves_one_to_one(scenario) -> None:
    box = scenario.sandbox
    planned = _plan(box)

    result = apply(planned, log=_quiet)
    rows = _ledger(box)

    assert rows == result.rows, "the ledger on disk should hold exactly the rows the run reports"
    assert sorted((r.category, r.item_id) for r in rows) == sorted(
        (item.category, item.item_id) for item in planned.items
    ), "one ledger row per moved item, no more and no fewer"
    for row in rows:
        assert row.release == "v1.3.0", row
        assert not (box.root / row.source).exists(), f"ledger 'from' still exists: {row.source}"
        assert (box.root / row.destination).exists(), f"ledger 'to' missing: {row.destination}"
        assert row.source == f"projects/{box.project}/{row.destination.split('/', 2)[2]}", row
    assert len({(r.category, r.item_id) for r in rows}) == len(rows), "an item was recorded twice"


def test_acceptance_unfinished_open_and_active_rc_work_stays_live(scenario) -> None:
    box = scenario.sandbox
    apply(_plan(box), log=_quiet)

    for number in scenario.open_bug_numbers:
        assert list((box.project_root / "bugs").glob(f"BUG-{number:04d}-*.md")), f"open bug {number} moved"
    for task_id in (*scenario.unfinished_task_ids, *scenario.active_rc_task_ids):
        assert (box.project_root / "tasks" / task_id).is_dir(), f"{task_id} left the live tree"
        assert not (_archive_root(box) / "tasks" / task_id).exists(), f"{task_id} reached the archive"


def test_acceptance_queue_lines_follow_their_items_with_markers_unchanged(scenario) -> None:
    box = scenario.sandbox
    planned = _plan(box)

    apply(planned, log=_quiet)

    for line in planned.queue_lines:
        live_text = line.queue_file.read_text(encoding="utf-8")
        archived = _archive_root(box) / "tasks" / "queues" / line.queue_file.name
        assert line.text not in live_text.splitlines(), f"line still live: {line.text}"
        assert line.text in archived.read_text(encoding="utf-8").splitlines(), f"line not archived: {line.text}"
    live_queues = "".join(
        box.queue_file(agent).read_text(encoding="utf-8") for agent in ("coder", "writer", "tester", "pm")
    )
    for task_id in (*scenario.unfinished_task_ids, *scenario.active_rc_task_ids):
        assert f"] {task_id} " in live_queues, f"queue line of live task {task_id} left the live queues"


def test_a_task_named_in_two_queue_files_leaves_both_and_reaches_both_archive_queues(tmp_path) -> None:
    box = ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")
    box.set_archive_count(1)
    archived_id = ArchiveSandbox.task_id("CODER", 1)
    live_id = ArchiveSandbox.task_id("CODER", 2)
    box.add_task(archived_id, "DONE")
    box.add_task(live_id, "DONE")
    coder_line = box.add_queue_line("coder", archived_id, marker="x", description="implement")
    tester_line = box.add_queue_line("tester", archived_id, marker="x", description="verify")
    live_line = box.add_queue_line("coder", live_id, marker="x")

    result = apply(_plan(box), log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert result.moved_ids == (archived_id,)
    assert sorted(result.queue_lines) == sorted((coder_line, tester_line))
    archive_queues = _archive_root(box) / "tasks" / "queues"
    for agent, line in (("coder", coder_line), ("tester", tester_line)):
        assert line not in box.queue_file(agent).read_text(encoding="utf-8").splitlines(), (
            f"{agent}_backlog.md still names the archived task"
        )
        assert (archive_queues / f"{agent}_backlog.md").read_text(encoding="utf-8").splitlines() == [line]
    assert live_line in box.queue_file("coder").read_text(encoding="utf-8").splitlines()


def test_a_hand_edited_marker_line_follows_its_task_into_the_archive(tmp_path) -> None:
    box = ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")
    box.set_archive_count(1)
    archived_id = ArchiveSandbox.task_id("CODER", 1)
    box.add_task(archived_id, "DONE")
    box.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE")
    # Spaces inside the brackets: the shared queue-marker writer matches
    # this line for the ID, so the archive must carry it along too.
    spaced = box.add_queue_line("writer", archived_id, marker=" x ")

    result = apply(_plan(box), log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert spaced not in box.queue_file("writer").read_text(encoding="utf-8").splitlines()
    archived_queue = _archive_root(box) / "tasks" / "queues" / "writer_backlog.md"
    assert archived_queue.read_text(encoding="utf-8").splitlines() == [spaced]


def test_acceptance_training_logs_follow_their_tasks(scenario) -> None:
    box = scenario.sandbox
    planned = _plan(box)

    result = apply(planned, log=_quiet)

    assert set(result.training_logs) == {log.relpath for log in planned.training_logs}
    for log in planned.training_logs:
        assert not log.path.exists(), f"training log still live: {log.path}"
        assert (_archive_root(box) / log.relpath).is_file(), f"training log not archived: {log.relpath}"
        assert log.relpath.startswith(f"logs/training/{log.role}/"), log.relpath


def test_second_apply_of_a_fresh_plan_moves_nothing_and_writes_no_rows(scenario) -> None:
    box = scenario.sandbox
    apply(_plan(box), log=_quiet)
    rows_before = _ledger(box)
    before = box.snapshot()

    result = apply(_plan(box), log=_quiet)

    assert result.status == RESULT_OK
    assert result.rows == () and result.queue_lines == () and result.training_logs == ()
    assert _ledger(box) == rows_before
    assert box.snapshot() == before, "a second run changed the tree"


def test_second_apply_of_the_same_plan_moves_nothing_and_writes_no_rows(scenario) -> None:
    box = scenario.sandbox
    planned = _plan(box)
    apply(planned, log=_quiet)
    rows_before = _ledger(box)
    before = box.snapshot()

    result = apply(planned, log=_quiet)

    assert result.status == RESULT_OK
    assert result.rows == ()
    assert set(result.dropped) == {item.item_id for item in planned.items}
    assert _ledger(box) == rows_before
    assert box.snapshot() == before, "re-applying a used plan changed the tree"


def test_archive_count_zero_moves_nothing(scenario) -> None:
    box = scenario.sandbox
    before = box.snapshot()

    result = apply(plan(box.project, 0, kanban_root=box.root), log=_quiet)

    assert result.status == RESULT_OK and result.rows == ()
    assert box.snapshot() == before


# ---------------------------------------------------------------------------
# Re-checking under the lock
# ---------------------------------------------------------------------------


def test_an_item_reopened_after_planning_stays_live(sandbox) -> None:
    planned = _plan(sandbox)
    reopened = next(item for item in planned.items if item.category == "tasks")
    (reopened.path / "status.md").write_text("# Status\n\n## State\nWORKING\n", encoding="utf-8")

    result = apply(planned, log=_quiet)

    assert result.status == RESULT_OK
    assert reopened.item_id in result.dropped
    assert reopened.item_id not in result.moved_ids
    assert reopened.path.is_dir(), "a task reopened after planning was archived"


# ---------------------------------------------------------------------------
# The repo lock
# ---------------------------------------------------------------------------


def test_apply_holds_the_repo_lock_with_full_provenance_and_releases_it(sandbox) -> None:
    lock_file = repo_lock_path(sandbox.root, sandbox.project)
    seen = []

    def watch(_line: str) -> None:
        seen.append(read_provenance(lock_file))

    result = apply(_plan(sandbox), task="CM-20260301-001-ship", log=watch)

    assert result.status == RESULT_OK
    record = seen[-1]
    assert f"holder=archive\npid={os.getpid()}\nop=archive-apply\ntask=CM-20260301-001-ship\n" in record + "\n"
    assert "acquired_at=" in record
    assert not lock_file.exists(), "the repo lock was not released"


def test_apply_without_a_task_records_task_none(sandbox) -> None:
    lock_file = repo_lock_path(sandbox.root, sandbox.project)
    seen = []

    apply(_plan(sandbox), log=lambda _l: seen.append(read_provenance(lock_file)))

    assert "\ntask=none\n" in seen[-1] + "\n"


def test_a_held_repo_lock_skips_the_run_and_names_the_holder(sandbox) -> None:
    planned = _plan(sandbox)
    before = sandbox.snapshot()
    lines = []
    holder = acquire_lock(
        repo_lock_path(sandbox.root, sandbox.project), holder="wake-coder", pid="424242", op="wake", task="none"
    )
    try:
        result = apply(planned, log=lines.append)
    finally:
        holder.release()

    assert result.status == RESULT_SKIPPED
    assert result.lock_holder == ("wake-coder", "424242", "wake", "none")
    assert result.rows == ()
    contract = parse_error_contract(result.error)
    assert contract and contract["error"] == "archive.repo_lock_busy"
    for field in ("holder=wake-coder", "pid=424242", "op=wake", "task=none"):
        assert field in contract["message"], field
    assert lines == [result.error], "the skip should be logged exactly once"
    after = sandbox.snapshot()
    after.pop("locks", None)
    before.pop("locks", None)
    assert after == before, "a skipped run moved something"


@pytest.mark.parametrize("chain_pid", [os.getpid, os.getppid], ids=["this-process", "parent-process"])
def test_accept_chain_lock_runs_under_a_lock_its_own_chain_holds(sandbox, chain_pid) -> None:
    planned = _plan(sandbox)
    lock_file = repo_lock_path(sandbox.root, sandbox.project)
    lines = []
    with acquire_lock(lock_file, holder="wake-chain", pid=chain_pid(), op="wake-chain:demo", task="none"):
        record_before = read_provenance(lock_file)
        result = apply(planned, log=lines.append, accept_chain_lock=True)
        record_after = read_provenance(lock_file)

    assert result.status == RESULT_OK, lines
    assert planned.items, "the sandbox should plan something to move"
    assert sorted(result.moved_ids) == sorted(item.item_id for item in planned.items)
    assert any("is held by the wake chain this run belongs to" in line for line in lines), lines
    assert record_after == record_before, "the chain's own lock record was replaced"


def test_accept_chain_lock_still_skips_a_lock_held_by_an_unrelated_process(sandbox) -> None:
    planned = _plan(sandbox)
    before = sandbox.snapshot()
    # A live process that is this test's child, never its ancestor.
    other = subprocess.Popen(["sleep", "60"])
    try:
        holder = acquire_lock(
            repo_lock_path(sandbox.root, sandbox.project),
            holder="wake-chain", pid=other.pid, op="wake-chain:demo", task="none",
        )
        try:
            result = apply(planned, log=_quiet, accept_chain_lock=True)
        finally:
            holder.release()
    finally:
        other.kill()
        other.wait(timeout=30)

    assert result.status == RESULT_SKIPPED
    assert result.lock_holder == ("wake-chain", str(other.pid), "wake-chain:demo", "none")
    contract = parse_error_contract(result.error)
    assert contract and contract["error"] == "archive.repo_lock_busy"
    assert contract["remedy"]
    after = sandbox.snapshot()
    after.pop("locks", None)
    before.pop("locks", None)
    assert after == before, "a skipped run moved something"


def test_without_accept_chain_lock_even_this_processes_own_lock_skips(sandbox) -> None:
    planned = _plan(sandbox)
    with acquire_lock(
        repo_lock_path(sandbox.root, sandbox.project), holder="wake-chain", pid=os.getpid(), op="hold", task="none"
    ):
        result = apply(planned, log=_quiet)

    assert result.status == RESULT_SKIPPED
    assert result.rows == ()


# ---------------------------------------------------------------------------
# The cross-filesystem branch
# ---------------------------------------------------------------------------


def _cross_device(monkeypatch) -> list:
    """Make every first-try rename fail with EXDEV; return the paths it refused."""
    refused = []

    def no_rename(src, dst):
        refused.append(pathlib.Path(src))
        raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))

    monkeypatch.setattr(move_module, "_rename", no_rename)
    return refused


def test_cross_filesystem_apply_copies_then_removes_with_the_same_result(scenario, monkeypatch) -> None:
    box = scenario.sandbox
    planned = _plan(box)
    contents = {item.relpath: item.path.read_bytes() for item in planned.items if item.path.is_file()}
    task = next(item for item in planned.items if item.category == "tasks")
    task_files = {p.relative_to(task.path).as_posix(): p.read_bytes() for p in task.path.rglob("*") if p.is_file()}
    refused = _cross_device(monkeypatch)

    result = apply(planned, log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert len(refused) == len(planned.items) + len(planned.training_logs), "every move should take the copy branch"
    assert len(_ledger(box)) == len(planned.items)
    for relpath, data in contents.items():
        assert (_archive_root(box) / relpath).read_bytes() == data, relpath
        assert not (box.project_root / relpath).exists(), relpath
    archived_task = _archive_root(box) / task.relpath
    assert {p.relative_to(archived_task).as_posix(): p.read_bytes() for p in archived_task.rglob("*") if p.is_file()} == task_files
    assert (archived_task / "artifacts").is_dir() and (archived_task / "logs").is_dir(), "empty sub-directories lost"
    assert not list(_archive_root(box).rglob("*.archive-staging")), "a staging copy was left behind"


def test_cross_filesystem_move_clears_a_staging_copy_left_by_an_interrupted_move(tmp_path, monkeypatch) -> None:
    source = tmp_path / "live" / "item.md"
    source.parent.mkdir()
    source.write_text("complete\n", encoding="utf-8")
    destination = tmp_path / "archive" / "item.md"
    destination.parent.mkdir()
    (destination.parent / ".item.md.archive-staging").write_text("partial", encoding="utf-8")
    _cross_device(monkeypatch)

    assert move_module.move_path(source, destination) == move_module.MOVED_BY_COPY

    assert destination.read_text(encoding="utf-8") == "complete\n"
    assert not source.exists()
    assert not (destination.parent / ".item.md.archive-staging").exists()


def test_move_never_overwrites_an_existing_destination(tmp_path) -> None:
    source = tmp_path / "a.md"
    source.write_text("live\n", encoding="utf-8")
    destination = tmp_path / "archive" / "a.md"
    destination.parent.mkdir()
    destination.write_text("archived\n", encoding="utf-8")

    with pytest.raises(move_module.ArchiveMoveError) as caught:
        move_module.move_path(source, destination)

    assert caught.value.error == "archive.destination_exists"
    assert caught.value.remedy.startswith("diff -r ")
    assert source.read_text(encoding="utf-8") == "live\n"
    assert destination.read_text(encoding="utf-8") == "archived\n"


# ---------------------------------------------------------------------------
# Failure stops the run at the failing item
# ---------------------------------------------------------------------------


def test_a_failed_move_stops_the_run_and_earlier_moves_keep_their_rows(scenario, monkeypatch) -> None:
    box = scenario.sandbox
    planned = _plan(box)
    failing = planned.items[5]
    real_rename = os.replace

    def rename(src, dst):
        if pathlib.Path(src) == failing.path:
            raise OSError(errno.EACCES, "Permission denied", str(src))
        return real_rename(src, dst)

    monkeypatch.setattr(move_module, "_rename", rename)
    lines = []

    result = apply(planned, log=lines.append)

    assert result.status == RESULT_FAILED
    assert result.failed_item == failing.item_id
    assert result.moved_ids == tuple(item.item_id for item in planned.items[:5])
    assert _ledger(box) == result.rows, "the ledger should hold exactly the moves made before the failure"
    for row in result.rows:
        assert (box.root / row.destination).exists() and not (box.root / row.source).exists(), row
    assert failing.path.exists(), "the failing item left the live tree"
    for item in planned.items[6:]:
        assert item.path.exists(), f"{item.item_id} moved after the failure"
    contract = parse_error_contract(result.error)
    assert contract and contract["error"] == "archive.run_failed"
    assert failing.item_id in contract["message"]
    assert result.error in lines, "the failure was not logged"


def test_rerun_after_a_failure_finishes_and_the_ledger_matches_every_move(scenario, monkeypatch) -> None:
    box = scenario.sandbox
    planned = _plan(box)
    failing = next(item for item in planned.items if item.category == "tasks")
    real_rename = os.replace

    def rename(src, dst):
        if pathlib.Path(src) == failing.path:
            raise OSError(errno.EACCES, "Permission denied", str(src))
        return real_rename(src, dst)

    monkeypatch.setattr(move_module, "_rename", rename)
    assert apply(planned, log=_quiet).status == RESULT_FAILED
    monkeypatch.setattr(move_module, "_rename", real_rename)

    result = apply(_plan(box), log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert failing.item_id in result.moved_ids
    rows = _ledger(box)
    assert sorted((r.category, r.item_id) for r in rows) == sorted((i.category, i.item_id) for i in planned.items)
    for log in planned.training_logs_for(failing.item_id):
        assert (_archive_root(box) / log.relpath).is_file(), "the failing task's training log did not end archived"


def test_a_ledger_write_failure_moves_the_item_back(sandbox, monkeypatch) -> None:
    planned = _plan(sandbox)
    first = planned.items[0]

    def broken(_ledger_path, _row):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(apply_module, "append_row", broken)

    result = apply(planned, log=_quiet)

    assert result.status == RESULT_FAILED and result.failed_item == first.item_id
    assert result.rows == ()
    assert first.path.exists(), "an item with no ledger row was left in the archive"
    assert not (_archive_root(sandbox) / first.relpath).exists()
    assert "moved back" in parse_error_contract(result.error)["message"]


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def test_restore_brings_a_task_back_with_its_queue_line_and_training_log(sandbox) -> None:
    planned = _plan(sandbox)
    task = next(item for item in planned.items if item.category == "tasks")
    queue_line = planned.queue_lines_for(task.item_id)[0].text
    training = planned.training_logs_for(task.item_id)[0]
    apply(planned, log=_quiet)
    archived_queue = _archive_root(sandbox) / "tasks" / "queues" / "coder_backlog.md"
    assert queue_line in archived_queue.read_text(encoding="utf-8")

    result = restore(sandbox.project, task.item_id, kanban_root=sandbox.root, log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert task.path.is_dir() and (task.path / "status.md").is_file()
    assert not (_archive_root(sandbox) / task.relpath).exists()
    assert queue_line in sandbox.queue_file("coder").read_text(encoding="utf-8").splitlines()
    assert queue_line not in archived_queue.read_text(encoding="utf-8").splitlines()
    assert training.path.is_file() and not (_archive_root(sandbox) / training.relpath).exists()
    assert result.queue_lines == (queue_line,)
    assert result.training_logs == (training.relpath,)


def test_restore_appends_one_restore_row(sandbox) -> None:
    planned = _plan(sandbox)
    bug = next(item for item in planned.items if item.category == "bugs")
    apply(planned, log=_quiet)
    rows_before = _ledger(sandbox)

    result = restore(sandbox.project, "bug-1", kanban_root=sandbox.root, log=_quiet)

    rows = _ledger(sandbox)
    assert rows[: len(rows_before)] == rows_before
    assert rows[len(rows_before):] == result.rows and len(result.rows) == 1
    row = result.rows[0]
    assert (row.category, row.item_id, row.release) == (CATEGORY_RESTORE, bug.item_id, "v1.0.0")
    assert row.source == f"archive/{sandbox.project}/{bug.relpath}"
    assert row.destination == f"projects/{sandbox.project}/{bug.relpath}"
    assert bug.path.is_file()


def test_restore_of_an_id_that_is_not_archived_is_refused_and_moves_nothing(sandbox) -> None:
    apply(_plan(sandbox), log=_quiet)
    before = sandbox.snapshot()

    with pytest.raises(ArchiveRestoreError) as caught:
        restore(sandbox.project, "BUG-0099", kanban_root=sandbox.root, log=_quiet)

    contract = parse_error_contract(str(caught.value))
    assert contract and contract["error"] == "archive.restore_not_archived"
    assert "BUG-0099" in contract["message"]
    assert contract["remedy"] == f"ls -1 {_archive_root(sandbox) / 'bugs'}"
    assert sandbox.snapshot() == before


def test_restore_of_an_unrecognised_id_is_refused(sandbox) -> None:
    with pytest.raises(ArchiveRestoreError) as caught:
        restore(sandbox.project, "../../etc", kanban_root=sandbox.root, log=_quiet)

    assert caught.value.error == "archive.restore_not_archived"


def test_restore_over_a_live_item_is_refused_and_moves_nothing(sandbox) -> None:
    planned = _plan(sandbox)
    bug = next(item for item in planned.items if item.category == "bugs")
    apply(planned, log=_quiet)
    sandbox.add_bug(1, "open", slug="reopened")
    before = sandbox.snapshot()

    with pytest.raises(ArchiveRestoreError) as caught:
        restore(sandbox.project, bug.item_id, kanban_root=sandbox.root, log=_quiet)

    assert caught.value.error == "archive.restore_live_exists"
    assert caught.value.remedy.startswith("diff -r ")
    assert sandbox.snapshot() == before


def test_restore_skips_when_the_repo_lock_is_held(sandbox) -> None:
    planned = _plan(sandbox)
    task = next(item for item in planned.items if item.category == "tasks")
    apply(planned, log=_quiet)
    holder = acquire_lock(
        repo_lock_path(sandbox.root, sandbox.project), holder="wake-pm", pid="5150", op="wake", task="PM-1"
    )
    try:
        result = restore(sandbox.project, task.item_id, kanban_root=sandbox.root, log=_quiet)
    finally:
        holder.release()

    assert result.status == RESULT_SKIPPED
    assert result.lock_holder == ("wake-pm", "5150", "wake", "PM-1")
    assert not task.path.exists()


def test_restore_with_accept_chain_lock_runs_under_its_own_chains_lock(sandbox) -> None:
    planned = _plan(sandbox)
    task = next(item for item in planned.items if item.category == "tasks")
    apply(planned, log=_quiet)
    lock_file = repo_lock_path(sandbox.root, sandbox.project)
    with acquire_lock(lock_file, holder="wake-chain", pid=os.getppid(), op="wake-chain:demo", task="none"):
        result = restore(sandbox.project, task.item_id, kanban_root=sandbox.root, log=_quiet, accept_chain_lock=True)

    assert result.status == RESULT_OK, result.messages
    assert task.path.is_dir()


def test_restore_then_apply_archives_the_item_again(sandbox) -> None:
    planned = _plan(sandbox)
    task = next(item for item in planned.items if item.category == "tasks")
    apply(planned, log=_quiet)
    restore(sandbox.project, task.item_id, kanban_root=sandbox.root, log=_quiet)

    again = apply(_plan(sandbox), log=_quiet)

    assert again.moved_ids == (task.item_id,)
    categories = [row.category for row in _ledger(sandbox) if row.item_id == task.item_id]
    assert categories == ["tasks", CATEGORY_RESTORE, "tasks"]


# ---------------------------------------------------------------------------
# The ledger format
# ---------------------------------------------------------------------------


def test_ledger_rows_are_six_tab_separated_fields_with_no_header(sandbox) -> None:
    apply(_plan(sandbox), log=_quiet)

    lines = (_archive_root(sandbox) / "ARCHIVE-LEDGER.tsv").read_text(encoding="utf-8").splitlines()

    assert len(lines) == len(_ledger(sandbox)) == 2
    for line in lines:
        fields = line.split("\t")
        assert len(fields) == 6, line
        assert fields[0].endswith("+00:00") and fields[1] == "v1.0.0"


def test_ledger_fields_never_carry_a_tab_or_line_break(tmp_path) -> None:
    ledger = tmp_path / "ARCHIVE-LEDGER.tsv"

    append_row(ledger, LedgerRow("t", "v1.0.0", "bugs", "BUG-0001", "a\tb", "c\nd"))

    assert ledger.read_text(encoding="utf-8") == "t\tv1.0.0\tbugs\tBUG-0001\ta b\tc d\n"

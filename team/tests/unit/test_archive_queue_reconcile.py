"""
test_archive_queue_reconcile.py
===============================
Unit tests for pgai_agent_kanban.archive.reconcile — moving live queue lines
that name an item no longer in the live project into the archive's queue
files — and its operator surface, ``archive.sh --reconcile-queues``:

  - The plan (the dry run) lists every live line naming an item that is
    archived and not live, and every [x] line naming a task whose folder is
    in neither tree; it lists the lines it leaves (a task with no folder and
    a line that is not [x]; a bug in neither tree) with the reason, and it
    changes nothing on disk.
  - Apply moves exactly the planned lines to archive/<project>/tasks/queues/
    <same file>, marker and text unchanged; afterwards no live line names a
    task folder that is absent from live tasks/ and present in the archive.
    A second apply moves nothing and changes nothing.
  - A line whose item is live is never touched, byte for byte.
  - A line already copied into the archive by an interrupted run ends up
    once in the archive and gone from the live queue.
  - A held repo lock skips the run and moves nothing.
  - archive.sh: --reconcile-queues --dry-run lists and changes nothing,
    --reconcile-queues moves, a second run moves nothing; combined with
    --count or --restore it is refused dual-channel with nothing moved.
  - POST /operations/archive with ``reconcile_queues``: its dry run prints
    the same text as the CLI's and changes nothing; without ``dry_run`` it
    moves the lines.

Every test builds its own sandbox kanban root under pytest's tmp_path
(tests/fixtures/archive_sandbox.py) and passes it explicitly; nothing here
reads or writes the live install.
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.archive import (
    RESULT_OK,
    RESULT_SKIPPED,
    apply,
    apply_queue_reconcile,
    plan,
    plan_queue_reconcile,
)
from pgai_agent_kanban.archive.reconcile import (
    LEFT_ITEM_NOT_FOUND,
    LEFT_NOT_FINISHED,
    OP_RECONCILE_QUEUES,
    REASON_ARCHIVED,
    REASON_NO_TASK_FOLDER,
)
from pgai_agent_kanban.archive.run import EXIT_OK, EXIT_REFUSED, run_archive
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.locks import acquire_lock, repo_lock_path
from tests.fixtures.archive_sandbox import ArchiveSandbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPT = _TEAM_DIR / "scripts" / "archive.sh"


def _quiet(_line: str) -> None:
    """A log sink that keeps run narration out of the test output."""


@dataclasses.dataclass
class Board:
    """A sandbox whose archive already holds tasks, with dangling live lines added afterwards."""

    box: ArchiveSandbox
    archived_task: str
    archived_bug: str
    folderless_done: str
    folderless_mixed: str
    live_task: str
    moving: dict  # line text -> queue file name it sits in
    left: dict  # line text -> queue file name it sits in
    untouched: dict  # line text -> queue file name it sits in


@pytest.fixture
def board(tmp_path: pathlib.Path) -> Board:
    """Archive two tasks and a bug, then add the dangling lines the pass must handle.

    After a normal archive run (archive_count 1: tasks 1-2 and bug 1 move,
    task 3 and bug 2 stay live), lines are appended to the live queues:

    - task 1 (archived) in coder and tester, [x] -- moves;
    - bug 1 (archived) in bug_backlog -- moves;
    - task 9, no folder anywhere, [x] in coder and writer -- moves;
    - task 8, no folder anywhere, [x] in coder and [ ] in tester -- left;
    - bug 7, in neither tree -- left;
    - task 3 (live), a second line in tester -- never touched.
    """
    box = ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")
    box.set_archive_count(1)
    tasks = [ArchiveSandbox.task_id("CODER", n) for n in (1, 2, 3)]
    for task_id in tasks:
        box.add_task(task_id, "DONE")
    bugs = [box.add_bug(n, "done").stem for n in (1, 2)]
    applied = apply(plan(box.project, kanban_root=box.root), log=_quiet)
    assert applied.status == RESULT_OK and set(applied.moved_ids) == {tasks[0], tasks[1], "BUG-0001"}

    folderless_done = ArchiveSandbox.task_id("CODER", 9, date="20260201")
    folderless_mixed = ArchiveSandbox.task_id("WRITER", 8, date="20260201")
    moving = {
        box.add_queue_line("coder", tasks[0], marker="x", description="implement"): "coder_backlog.md",
        box.add_queue_line("tester", tasks[0], marker="x", description="verify"): "tester_backlog.md",
        box.add_queue_line("bug", bugs[0], marker="x"): "bug_backlog.md",
        box.add_queue_line("coder", folderless_done, marker="x"): "coder_backlog.md",
        box.add_queue_line("writer", folderless_done, marker="x", description="docs"): "writer_backlog.md",
    }
    left = {
        box.add_queue_line("coder", folderless_mixed, marker="x"): "coder_backlog.md",
        box.add_queue_line("tester", folderless_mixed, marker=" "): "tester_backlog.md",
        box.add_queue_line("bug", "BUG-0007-never-filed", marker="x"): "bug_backlog.md",
    }
    untouched = {box.add_queue_line("tester", tasks[2], marker="x", description="live"): "tester_backlog.md"}
    return Board(box, tasks[0], "BUG-0001", folderless_done, folderless_mixed, tasks[2], moving, left, untouched)


def _archive_queues(box: ArchiveSandbox) -> pathlib.Path:
    return box.root / "archive" / box.project / "tasks" / "queues"


def _live_lines(box: ArchiveSandbox, file_name: str) -> list[str]:
    return (box.project_root / "tasks" / "queues" / file_name).read_text(encoding="utf-8").splitlines()


def _archived_lines(box: ArchiveSandbox, file_name: str) -> list[str]:
    path = _archive_queues(box) / file_name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _without_locks(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if not key.startswith("locks")}


def _live_lines_naming_archived_tasks(box: ArchiveSandbox) -> list[str]:
    """Live queue lines naming a task folder absent from live tasks/ and present in the archive."""
    archived = {p.name for p in (box.root / "archive" / box.project / "tasks").iterdir() if p.is_dir()}
    found = []
    for queue in sorted((box.project_root / "tasks" / "queues").glob("*_backlog.md")):
        for line in queue.read_text(encoding="utf-8").splitlines():
            words = line.split()
            if len(words) >= 3 and words[0] == "-" and words[2] in archived:
                if not (box.project_root / "tasks" / words[2]).exists():
                    found.append(f"{queue.name}: {line}")
    return found


# ---------------------------------------------------------------------------
# The plan (dry run)
# ---------------------------------------------------------------------------


def test_plan_lists_the_dangling_lines_with_their_reasons(board) -> None:
    planned = plan_queue_reconcile(board.box.project, kanban_root=board.box.root)

    assert {line.text: line.queue_file.name for line in planned.lines} == board.moving
    reasons = {line.item_id: line.reason for line in planned.lines}
    assert reasons == {
        board.archived_task: REASON_ARCHIVED,
        board.archived_bug: REASON_ARCHIVED,
        board.folderless_done: REASON_NO_TASK_FOLDER,
    }
    assert {line.text: line.queue_file.name for line in planned.left} == board.left
    assert {line.item_id: line.reason for line in planned.left} == {
        board.folderless_mixed: LEFT_NOT_FINISHED,
        "BUG-0007": LEFT_ITEM_NOT_FOUND,
    }
    for line in (*planned.lines, *planned.left):
        lines = line.queue_file.read_text(encoding="utf-8").splitlines()
        assert lines[line.line_number - 1] == line.text, "line_number must point at the listed text"


def test_dry_run_lists_the_lines_and_changes_nothing(board) -> None:
    before = board.box.snapshot()
    out: list[str] = []

    run = run_archive(
        OpsContext(kanban_root=board.box.root), board.box.project, dry_run=True, reconcile_queues=True, out=out.append
    )

    assert run.exit_code == EXIT_OK and run.mode == "reconcile-queues-dry-run"
    assert board.box.snapshot() == before, "a reconciliation dry run changed the sandbox"
    text = "\n".join(out)
    assert f"queue lines: {len(board.moving)} would move" in text, text
    assert f"left: {len(board.left)} line(s)" in text, text
    for line in (*board.moving, *board.left):
        assert line in text, f"{line!r} missing from the dry run:\n{text}"
    for line in board.untouched:
        assert line not in text, f"a line of a live task was listed:\n{text}"


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_apply_moves_the_planned_lines_with_markers_unchanged(board) -> None:
    box = board.box

    result = apply_queue_reconcile(box.project, kanban_root=box.root, log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert result.op == OP_RECONCILE_QUEUES and result.rows == ()
    assert sorted(result.queue_lines) == sorted(board.moving)
    for line, file_name in board.moving.items():
        assert line not in _live_lines(box, file_name), f"still live in {file_name}: {line}"
        assert _archived_lines(box, file_name).count(line) == 1, f"not archived once in {file_name}: {line}"
    for line, file_name in board.left.items():
        assert line in _live_lines(box, file_name), f"a left line moved out of {file_name}: {line}"
        assert line not in _archived_lines(box, file_name)


def test_after_apply_no_live_line_names_a_task_that_is_only_archived(board) -> None:
    box = board.box
    assert _live_lines_naming_archived_tasks(box), "the fixture should start with such a line"

    apply_queue_reconcile(box.project, kanban_root=box.root, log=_quiet)

    assert _live_lines_naming_archived_tasks(box) == []


def test_a_line_whose_task_is_live_is_never_touched(board) -> None:
    box = board.box
    tester_before = (box.project_root / "tasks" / "queues" / "tester_backlog.md").read_text(encoding="utf-8")
    live_lines = [line for line in tester_before.splitlines() if board.live_task in line]

    apply_queue_reconcile(box.project, kanban_root=box.root, log=_quiet)

    tester_after = _live_lines(box, "tester_backlog.md")
    assert [line for line in tester_after if board.live_task in line] == live_lines
    assert all(board.live_task not in line for line in _archived_lines(box, "tester_backlog.md"))


def test_a_second_apply_moves_nothing_and_changes_nothing(board) -> None:
    box = board.box
    apply_queue_reconcile(box.project, kanban_root=box.root, log=_quiet)
    before = box.snapshot()

    second = apply_queue_reconcile(box.project, kanban_root=box.root, log=_quiet)

    assert second.status == RESULT_OK
    assert second.queue_lines == ()
    assert plan_queue_reconcile(box.project, kanban_root=box.root).lines == ()
    assert box.snapshot() == before, "a second reconciliation changed the tree"


def test_a_line_already_copied_by_an_interrupted_run_ends_once_in_the_archive(board) -> None:
    box = board.box
    line = next(text for text, name in board.moving.items() if name == "tester_backlog.md")
    _archive_queues(box).mkdir(parents=True, exist_ok=True)
    with (_archive_queues(box) / "tester_backlog.md").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")

    result = apply_queue_reconcile(box.project, kanban_root=box.root, log=_quiet)

    assert result.status == RESULT_OK, result.error
    assert line not in _live_lines(box, "tester_backlog.md")
    assert _archived_lines(box, "tester_backlog.md").count(line) == 1


def test_a_held_repo_lock_skips_the_reconciliation_and_moves_nothing(board) -> None:
    box = board.box
    before = _without_locks(box.snapshot())
    holder = acquire_lock(
        repo_lock_path(box.root, box.project), holder="wake-coder", pid="424242", op="wake", task="none"
    )
    try:
        result = apply_queue_reconcile(box.project, kanban_root=box.root, log=_quiet)
    finally:
        holder.release()

    assert result.status == RESULT_SKIPPED
    assert result.lock_holder == ("wake-coder", "424242", "wake", "none")
    contract = parse_error_contract(result.error)
    assert contract and contract["error"] == "archive.repo_lock_busy"
    assert _without_locks(box.snapshot()) == before, "a skipped reconciliation moved something"


# ---------------------------------------------------------------------------
# archive.sh --reconcile-queues
# ---------------------------------------------------------------------------


def _run_cli(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run the real archive.sh against the sandbox *root*, from outside the repository."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp_dir = tmp_path / "framework-tmp"
    temp_dir.mkdir(exist_ok=True)
    foreign_cwd = tmp_path / "cwd"
    foreign_cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        },
    )
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(foreign_cwd),
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def test_cli_dry_run_then_apply_then_a_second_apply_that_moves_nothing(board, tmp_path) -> None:
    box = board.box
    before = _without_locks(box.snapshot())

    dry = _run_cli(box.root, tmp_path, "--project", box.project, "--reconcile-queues", "--dry-run")

    assert dry.returncode == 0, _diagnostic(dry)
    assert _without_locks(box.snapshot()) == before, "the CLI dry run changed the sandbox"
    for line in board.moving:
        assert line in dry.stdout, _diagnostic(dry)

    applied = _run_cli(box.root, tmp_path, "--project", box.project, "--reconcile-queues")

    assert applied.returncode == 0, _diagnostic(applied)
    for line, file_name in board.moving.items():
        assert line not in _live_lines(box, file_name)
        assert line in _archived_lines(box, file_name)
    after_first = _without_locks(box.snapshot())

    again = _run_cli(box.root, tmp_path, "--project", box.project, "--reconcile-queues")

    assert again.returncode == 0, _diagnostic(again)
    assert "no live queue line to reconcile" in again.stdout + again.stderr, _diagnostic(again)
    assert _without_locks(box.snapshot()) == after_first, "a second CLI run changed the tree"


@pytest.mark.parametrize(
    "extra",
    [("--count", "3"), ("--restore", "BUG-0001")],
    ids=["with-count", "with-restore"],
)
def test_cli_refuses_reconcile_combined_with_count_or_restore(board, tmp_path, extra) -> None:
    box = board.box
    before = _without_locks(box.snapshot())

    result = _run_cli(box.root, tmp_path, "--project", box.project, "--reconcile-queues", *extra)

    assert result.returncode == EXIT_REFUSED, _diagnostic(result)
    contract = parse_error_contract(result.stderr)
    assert contract is not None and contract["error"] == "archive.conflicting_options", _diagnostic(result)
    assert "--reconcile-queues" in contract["message"]
    assert "<" not in contract["remedy"], f"remedy carries a placeholder: {contract['remedy']!r}"
    assert _without_locks(box.snapshot()) == before, "a refused run changed the sandbox"


# ---------------------------------------------------------------------------
# POST /operations/archive {"reconcile_queues": true}
# ---------------------------------------------------------------------------


def _client(root: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """A TestClient for the real app whose kanban root is the sandbox."""
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    return TestClient(create_app(cfg=ApiConfig(host="127.0.0.1", port=0, kanban_root=root)))


def test_rest_reconcile_dry_run_matches_the_cli_and_apply_moves_the_lines(board, tmp_path, monkeypatch) -> None:
    box = board.box
    before = _without_locks(box.snapshot())

    cli = _run_cli(box.root, tmp_path, "--project", box.project, "--reconcile-queues", "--dry-run")
    with _client(box.root, monkeypatch) as client:
        dry = client.post(
            "/operations/archive", json={"project": box.project, "reconcile_queues": True, "dry_run": True}
        )
        assert dry.status_code == 200, dry.text
        assert _without_locks(box.snapshot()) == before, "the REST dry run changed the sandbox"
        applied = client.post("/operations/archive", json={"project": box.project, "reconcile_queues": True})

    assert cli.returncode == 0, _diagnostic(cli)
    assert dry.json()["stdout"] == cli.stdout, (
        f"REST dry run differs from the CLI\n--- REST ---\n{dry.json()['stdout']}\n--- CLI ---\n{cli.stdout}"
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["exit_code"] == 0, applied.json()
    for line, file_name in board.moving.items():
        assert line not in _live_lines(box, file_name)
        assert line in _archived_lines(box, file_name)

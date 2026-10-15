"""
test_archive_cli_rest.py
========================
Tests for the archive's two operator surfaces and the function they share:

  - ``team/scripts/archive.sh`` (run as a real subprocess, from a working
    directory outside the repository, under a rebuilt environment) plans
    then applies; ``--dry-run`` prints the planned moves per category and
    changes nothing; ``--count N`` overrides ``archive_count``; ``--count 0``
    moves nothing; ``--restore ID`` brings one item back with its queue
    lines; a second run moves nothing; ``--help`` exits 0; an unknown
    argument, a missing ``--project`` (with ``PGAI_PROJECT_NAME`` set, which
    never stands in for it), a missing option value, an invalid ``--count``
    and ``--restore`` combined with ``--dry-run`` are refused dual-channel
    with nothing moved; a held repo lock skips the run (exit 3), even when
    the holder is the script's own ancestor -- the script never asks to
    work under a wake chain's hold.
  - ``POST /operations/archive`` reports, for its ``dry_run``, exactly the
    planned moves the CLI's ``--dry-run`` prints on the same sandbox board;
    it refuses the same malformed values with the same machine key, and a
    body with no ``project`` at 422 before anything runs.
  - ``pgai_agent_kanban.archive.run.run_archive`` itself: refusal order and
    exit codes, a project with no directory, and ``accept_chain_lock``
    working under a repo lock this process's own chain holds.

Every test builds its own sandbox kanban root under pytest's tmp_path
(``tests/fixtures/archive_sandbox.py``) and hands it to the code under test
explicitly -- as ``--kanban-root`` through the script's environment, or as
the API's own kanban root. Nothing here points the archive at the live
install.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.archive import read_rows
from pgai_agent_kanban.archive.run import (
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_SKIPPED,
    run_archive,
)
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.locks import acquire_lock, repo_lock_path
from tests.fixtures.archive_sandbox import ArchiveSandbox, build_acceptance_scenario
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPT = _TEAM_DIR / "scripts" / "archive.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run_cli(
    root: pathlib.Path, tmp_path: pathlib.Path, *args: str, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run the real archive.sh against the sandbox *root*.

    The environment is rebuilt, not inherited: both kanban-root names point
    at the sandbox, the framework temp dir is the test's own, HOME is a
    scratch directory, and this interpreter's directory leads PATH so the
    script's python3 is the one running the suite. The working directory
    is outside the repository.
    """
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
            **(extra_env or {}),
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


def _contract(stderr: str) -> dict:
    fields = parse_error_contract(stderr)
    assert fields is not None, f"no error/message/remedy block in:\n{stderr}"
    assert "<" not in fields["remedy"], f"remedy carries a placeholder: {fields['remedy']!r}"
    return fields


# Diagnostics a refusal or a lock may legitimately leave behind: the
# refusal ledger the shared emitter records into when a project is in scope,
# and lock files. Neither is project state an archive run may change.
_DIAGNOSTIC_NAMES = ("refusal-ledger.md",)
_DIAGNOSTIC_DIRS = ("locks/",)


def _content_snapshot(root: pathlib.Path) -> dict[str, bytes]:
    """Every project-state file under *root* (relative path -> bytes)."""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or path.name in _DIAGNOSTIC_NAMES or rel.startswith(_DIAGNOSTIC_DIRS):
            continue
        snapshot[rel] = path.read_bytes()
    return snapshot


@pytest.fixture
def scenario(tmp_path: pathlib.Path):
    """The acceptance fixture: 73 bugs (3 open), 80 tasks, archive_count = 55."""
    return build_acceptance_scenario(tmp_path)


@pytest.fixture
def small(tmp_path: pathlib.Path) -> ArchiveSandbox:
    """Two done bugs and three done tasks with queue lines; archive_count 1."""
    box = ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")
    box.set_archive_count(1)
    for number in (1, 2):
        bug = box.add_bug(number, "done")
        box.add_queue_line("bug", bug.stem, marker="x")
    for sequence in (1, 2, 3):
        task_id = ArchiveSandbox.task_id("CODER", sequence)
        box.add_task(task_id, "DONE")
        box.add_queue_line("coder", task_id, marker="x", description=f"task {sequence}")
    return box


# ---------------------------------------------------------------------------
# archive.sh: plan, apply, preview, count, restore
# ---------------------------------------------------------------------------


def test_dry_run_prints_planned_moves_per_category_and_changes_nothing(scenario, tmp_path):
    box = scenario.sandbox
    before = box.snapshot()

    result = _run_cli(box.root, tmp_path, "--project", box.project, "--dry-run")

    assert result.returncode == 0, _diagnostic(result)
    assert box.snapshot() == before, "a dry run changed the sandbox"
    out = result.stdout
    assert f"bugs: {len(scenario.expected_bug_ids)} item(s) would move" in out, _diagnostic(result)
    assert f"tasks: {len(scenario.expected_task_ids)} item(s) would move" in out, _diagnostic(result)
    assert "priority: 0 item(s) would move" in out
    assert "requirements: 0 item(s) would move" in out
    for bug_id in scenario.expected_bug_ids:
        assert f"  {bug_id}  " in out, f"{bug_id} missing from the dry run:\n{out}"
    for task_id in scenario.expected_task_ids:
        assert f"  {task_id}  projects/{box.project}/tasks/{task_id} -> archive/{box.project}/tasks/{task_id}" in out
    for kept in (*scenario.unfinished_task_ids, *scenario.active_rc_task_ids):
        assert f"tasks {kept}: " in out, f"held task {kept} not reported as staying live:\n{out}"


def test_default_run_moves_the_planned_items_and_a_second_run_moves_nothing(scenario, tmp_path):
    box = scenario.sandbox

    first = _run_cli(box.root, tmp_path, "--project", box.project)

    assert first.returncode == 0, _diagnostic(first)
    rows = read_rows(box.root / "archive" / box.project / "ARCHIVE-LEDGER.tsv")
    assert sorted(row.item_id for row in rows) == sorted(
        (*scenario.expected_bug_ids, *scenario.expected_task_ids)
    )
    for task_id in scenario.expected_task_ids:
        assert (box.root / "archive" / box.project / "tasks" / task_id).is_dir()
        assert not (box.project_root / "tasks" / task_id).exists()
    for kept in (*scenario.unfinished_task_ids, *scenario.active_rc_task_ids):
        assert (box.project_root / "tasks" / kept).is_dir(), f"{kept} left the live project"

    after_first = _content_snapshot(box.root)
    second = _run_cli(box.root, tmp_path, "--project", box.project)

    assert second.returncode == 0, _diagnostic(second)
    assert "nothing to archive" in second.stdout, _diagnostic(second)
    assert _content_snapshot(box.root) == after_first, "a second run changed the sandbox"


def test_count_overrides_archive_count(scenario, tmp_path):
    box = scenario.sandbox

    # 73 bugs, newest 70 kept: bugs 1-3 are the older ones, all done.
    result = _run_cli(box.root, tmp_path, "--project", box.project, "--dry-run", "--count", "70")

    assert result.returncode == 0, _diagnostic(result)
    assert "(archive_count 70," in result.stdout
    assert "bugs: 3 item(s) would move" in result.stdout, _diagnostic(result)


def test_count_zero_moves_nothing(small, tmp_path):
    before = _content_snapshot(small.root)

    result = _run_cli(small.root, tmp_path, "--project", small.project, "--count", "0")

    assert result.returncode == 0, _diagnostic(result)
    assert "archiving is disabled" in result.stdout, _diagnostic(result)
    assert _content_snapshot(small.root) == before


def test_restore_brings_one_item_back_with_its_queue_lines(small, tmp_path):
    task_id = ArchiveSandbox.task_id("CODER", 1)
    queue = small.queue_file("coder")
    archived = _run_cli(small.root, tmp_path, "--project", small.project)
    assert archived.returncode == 0, _diagnostic(archived)
    assert task_id not in queue.read_text(encoding="utf-8")

    result = _run_cli(small.root, tmp_path, "--project", small.project, "--restore", task_id)

    assert result.returncode == 0, _diagnostic(result)
    assert (small.project_root / "tasks" / task_id / "status.md").is_file()
    assert not (small.root / "archive" / small.project / "tasks" / task_id).exists()
    assert f"- [x] {task_id} " in queue.read_text(encoding="utf-8")
    rows = read_rows(small.root / "archive" / small.project / "ARCHIVE-LEDGER.tsv")
    assert rows[-1].category == "restore" and rows[-1].item_id == task_id


def test_help_exits_zero_and_lists_every_flag(tmp_path):
    root = tmp_path / "kanban"
    root.mkdir()

    result = _run_cli(root, tmp_path, "--help")

    assert result.returncode == 0, _diagnostic(result)
    for flag in ("--project", "--dry-run", "--count", "--restore", "--help"):
        assert flag in result.stdout, _diagnostic(result)


# ---------------------------------------------------------------------------
# archive.sh: refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, error_key",
    [
        (("--project", "archive-demo", "--bogus"), "archive.unknown_argument"),
        (("--project", "archive-demo", "stray-word"), "archive.unknown_argument"),
        (("--project",), "archive.option_value_required"),
        (("--project", "archive-demo", "--count"), "archive.option_value_required"),
        (("--dry-run",), "archive.project_required"),
        (("--project", "archive-demo", "--count", "ten"), "archive.invalid_count"),
        (("--project", "archive-demo", "--count", "-1"), "archive.invalid_count"),
        (("--project", "archive-demo", "--restore", "BUG-0001", "--dry-run"), "archive.conflicting_options"),
        (("--project", "archive-demo", "--restore", "BUG-0099"), "archive.restore_not_archived"),
    ],
)
def test_malformed_invocation_is_refused_dual_channel_with_nothing_moved(small, tmp_path, args, error_key):
    before = _content_snapshot(small.root)

    # PGAI_PROJECT_NAME is set on purpose: it never stands in for --project.
    result = _run_cli(small.root, tmp_path, *args, extra_env={"PGAI_PROJECT_NAME": small.project})

    assert result.returncode == EXIT_REFUSED, _diagnostic(result)
    assert _contract(result.stderr)["error"] == error_key, _diagnostic(result)
    assert _content_snapshot(small.root) == before, "a refused run changed the sandbox"


def test_unknown_flag_message_carries_the_uniform_unknown_argument_text(small, tmp_path):
    result = _run_cli(small.root, tmp_path, "--project", small.project, "--bogus")

    assert "archive.sh: unknown argument: --bogus" in _contract(result.stderr)["message"], _diagnostic(result)


def test_held_repo_lock_skips_the_run_and_moves_nothing(small, tmp_path):
    before = _content_snapshot(small.root)
    lock = acquire_lock(
        repo_lock_path(small.root, small.project), holder="test", pid=os.getpid(), op="hold", task="none"
    )
    try:
        result = _run_cli(small.root, tmp_path, "--project", small.project)
    finally:
        lock.release()

    assert result.returncode == EXIT_SKIPPED, _diagnostic(result)
    assert _contract(result.stderr)["error"] == "archive.repo_lock_busy", _diagnostic(result)
    assert _content_snapshot(small.root) == before, "a skipped run changed the sandbox"


def test_run_archive_with_accept_chain_lock_works_under_its_own_chains_lock(small):
    lines: list[str] = []
    errors: list[str] = []
    with acquire_lock(
        repo_lock_path(small.root, small.project), holder="wake-chain", pid=os.getpid(), op="hold", task="none"
    ):
        run = run_archive(
            OpsContext(kanban_root=small.root), small.project,
            out=lines.append, err=errors.append, accept_chain_lock=True,
        )

    assert run.exit_code == EXIT_OK, "\n".join(lines + errors)
    assert run.result is not None and run.result.rows, "\n".join(lines + errors)
    assert errors == [], errors


# ---------------------------------------------------------------------------
# POST /operations/archive
# ---------------------------------------------------------------------------


def test_rest_dry_run_reports_the_same_planned_moves_as_the_cli_dry_run(scenario, tmp_path, monkeypatch):
    box = scenario.sandbox
    before = box.snapshot()

    cli = _run_cli(box.root, tmp_path, "--project", box.project, "--dry-run")
    with _client(box.root, monkeypatch) as client:
        response = client.post("/operations/archive", json={"project": box.project, "dry_run": True})

    assert cli.returncode == 0, _diagnostic(cli)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exit_code"] == 0, body
    assert f"tasks: {len(scenario.expected_task_ids)} item(s) would move" in body["stdout"]
    assert body["stdout"] == cli.stdout, (
        f"REST dry run differs from the CLI dry run\n--- REST ---\n{body['stdout']}\n--- CLI ---\n{cli.stdout}"
    )
    assert box.snapshot() == before, "a dry run changed the sandbox"


def test_rest_archive_moves_the_planned_items(small, monkeypatch):
    with _client(small.root, monkeypatch) as client:
        response = client.post("/operations/archive", json={"project": small.project})

    assert response.status_code == 200, response.text
    rows = read_rows(small.root / "archive" / small.project / "ARCHIVE-LEDGER.tsv")
    assert sorted(row.item_id for row in rows) == [
        "BUG-0001",
        ArchiveSandbox.task_id("CODER", 1),
        ArchiveSandbox.task_id("CODER", 2),
    ]


@pytest.mark.parametrize(
    "cli_args, body, error_key, http_status",
    [
        (("--count", "ten"), {"count": "ten"}, "archive.invalid_count", 422),
        (("--count", "-1"), {"count": -1}, "archive.invalid_count", 422),
        (("--restore", "BUG-0001", "--count", "2"), {"restore": "BUG-0001", "count": 2}, "archive.conflicting_options", 422),
        (("--restore", "BUG-0099"), {"restore": "BUG-0099"}, "archive.restore_not_archived", 500),
    ],
)
def test_rest_refuses_what_the_cli_refuses_with_the_same_triple(small, tmp_path, monkeypatch, cli_args, body, error_key, http_status):
    cli = _run_cli(small.root, tmp_path, "--project", small.project, *cli_args)
    with _client(small.root, monkeypatch) as client:
        response = client.post("/operations/archive", json={"project": small.project, **body})

    cli_fields = _contract(cli.stderr)
    payload = response.json()
    assert cli_fields["error"] == error_key, _diagnostic(cli)
    assert response.status_code == http_status, response.text
    assert payload["exit_code"] == cli.returncode == EXIT_REFUSED
    assert {key: payload[key] for key in ("error", "message", "remedy")} == cli_fields


def test_rest_body_without_project_is_refused_422_before_anything_runs(small, monkeypatch):
    before = _content_snapshot(small.root)
    with _client(small.root, monkeypatch) as client:
        response = client.post("/operations/archive", json={"dry_run": True})

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "archive.project_required"
    assert _content_snapshot(small.root) == before


# ---------------------------------------------------------------------------
# run_archive
# ---------------------------------------------------------------------------


def test_run_archive_refuses_a_registered_project_with_no_directory(tmp_path):
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "projects.cfg").write_text("[project:ghost-dir]\npriority = 1\n", encoding="utf-8")
    errors: list[str] = []

    run = run_archive(OpsContext(kanban_root=root), "ghost-dir", dry_run=True, err=errors.append)

    assert run.exit_code == EXIT_REFUSED
    assert parse_error_contract("\n".join(errors))["error"] == "archive.project_missing"
    assert not (root / "archive").exists()


def test_run_archive_dry_run_returns_the_plan_it_rendered(small):
    lines: list[str] = []

    run = run_archive(OpsContext(kanban_root=small.root), small.project, dry_run=True, out=lines.append)

    assert run.exit_code == EXIT_OK and run.mode == "dry-run" and run.result is None
    assert run.plan is not None
    assert [item.item_id for item in run.plan.items_in("tasks")] == [
        ArchiveSandbox.task_id("CODER", 1),
        ArchiveSandbox.task_id("CODER", 2),
    ]
    assert lines[0].startswith(f"archive: dry run for project '{small.project}'")

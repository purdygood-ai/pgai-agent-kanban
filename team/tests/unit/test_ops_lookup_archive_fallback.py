"""
test_ops_lookup_archive_fallback.py
===================================
The ops-layer lookups by ID behind show.sh, why.sh, the verdict queue tails
and GET /show fall back to the project's archive when an ID is not live, and
mark what they read from it as archived. Live-only scanners do not.

Covered, every fixture in a sandbox kanban root under pytest's tmp_path:

  * ``ops.resolve.resolve_item``
      - default (``include_archive=False``, what close/wontdo/delete/reset
        call): an archived task or bug is NOT found, so no write verb can
        reach an archived item;
      - ``include_archive=True``: an archived task and an archived bug (by
        its number prefix) resolve with ``archived=True`` and their state;
      - the live item wins when the same ID is live and archived;
      - a live task folder without status.md is answered from the live
        project (OpsError), never from the archive;
      - an ambiguous archive prefix raises Ambiguous whose result is
        archived;
      - a key carrying a path separator never reaches the archive.
  * ``ops.read.get_task_status``: archived documents (task status.md,
    task README.md, bug file) are returned unchanged with ``archived=True``
    and an ``archived:`` notice; a live document carries no notice.
  * ``show.sh`` (real script, subprocess): finds an archived bug and an
    archived task, stdout is the document, stderr marks it archived; an ID
    neither live nor archived still exits 3.
  * ``why.sh --details`` (real script, subprocess) and
    ``ops.verdict.resolve_verdicts``: a queue entry whose task or bug is
    archived shows the archived state marked ``(archived)``; a live entry
    and an unknown entry read as before; an archived task is never counted
    by the verdict's live task scan.
  * ``GET /show`` returns an archived document by ID with the archived
    notice in ``stderr``.
  * ``GET /board``: an archived task and an archived bug appear in no board
    column, while their live siblings do.

Nothing here reads or writes the live install: subprocesses get an
environment built from an explicit set (``build_scrubbed_env``) with every
kanban root pointing at the sandbox, and the API is rooted at the sandbox.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.ops import read as ops_read
from pgai_agent_kanban.ops import verdict as ops_verdict
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, NotFound, OpsError
from pgai_agent_kanban.ops.resolve import resolve_item
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SHOW_SH = _TEAM_DIR / "scripts" / "show.sh"
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"

_PROJECT = "demo-project"
_LIVE_TASK = "CODER-20260301-001-live-work"
_ARCHIVED_TASK = "CODER-20260101-002-shipped-work"
_ARCHIVED_TWIN_A = "CODER-20260101-003-twin-a"
_ARCHIVED_TWIN_B = "CODER-20260101-003-twin-b"
_BOTH_TASK = "CODER-20260101-004-restored-copy"
_HOLLOW_TASK = "CODER-20260101-005-hollow-live"
_UNKNOWN_TASK = "CODER-20260101-009-never-existed"
_LIVE_BUG = "BUG-0003-live-crash"
_ARCHIVED_BUG = "BUG-0001-old-crash"


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _task(tree: pathlib.Path, task_id: str, state: str, readme: bool = True) -> pathlib.Path:
    task_dir = tree / "tasks" / task_id
    _write(task_dir / "status.md", f"# Status\n\n## State\n{state}\n\n## Summary\n{task_id} fixture\n")
    if readme:
        _write(task_dir / "README.md", f"# Task: {task_id}\n\n## Role\nCODER\n")
    return task_dir


def _bug(tree: pathlib.Path, stem: str, status: str) -> pathlib.Path:
    return _write(tree / "bugs" / f"{stem}.md", f"# {stem}\n\n**Severity:** low\n\n## Status\n{status}\n")


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root with one project, a live tree and an archive tree."""
    kanban = tmp_path / "kanban"
    _write(kanban / "projects.cfg", f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n")
    _write(kanban / "kanban.cfg", "[providers]\nactive = claude\n")
    (kanban / "locks").mkdir(parents=True)
    (kanban / "tmp").mkdir()

    live = kanban / "projects" / _PROJECT
    archive = kanban / "archive" / _PROJECT

    _task(live, _LIVE_TASK, "BACKLOG")
    _task(live, _BOTH_TASK, "WORKING")
    (live / "tasks" / _HOLLOW_TASK).mkdir(parents=True)  # live folder, no status.md
    _bug(live, _LIVE_BUG, "open")
    _write(live / "tasks" / "queues" / "coder_backlog.md",
           f"# Coder Backlog\n\n- [ ] {_LIVE_TASK}\n- [x] {_ARCHIVED_TASK}\n- [x] {_UNKNOWN_TASK}\n")
    _write(live / "tasks" / "queues" / "bug_backlog.md",
           f"# Bug Backlog\n\n- [x] {_ARCHIVED_BUG}\n- [ ] {_LIVE_BUG}\n")

    # The archive holds finished items only -- except _BOTH_TASK, a stale
    # copy that must never shadow its live twin, and a BLOCKED task the
    # verdict's live scan must never see.
    _task(archive, _ARCHIVED_TASK, "DONE")
    _task(archive, _ARCHIVED_TWIN_A, "DONE")
    _task(archive, _ARCHIVED_TWIN_B, "WONT-DO")
    _task(archive, _BOTH_TASK, "DONE")
    _task(archive, _HOLLOW_TASK, "DONE")
    _task(archive, "CODER-20260101-006-archived-blocked", "BLOCKED")
    _bug(archive, _ARCHIVED_BUG, "done")
    _write(archive / "tasks" / "queues" / "coder_backlog.md", f"- [x] {_ARCHIVED_TWIN_A}\n")
    return kanban


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _archive(root: pathlib.Path) -> pathlib.Path:
    return root / "archive" / _PROJECT


def _live(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _run_script(script: pathlib.Path, args: list[str], root: pathlib.Path,
                tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True,
                          env=env, cwd=str(tmp_path), timeout=60, stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# ops.resolve.resolve_item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", [_ARCHIVED_TASK, "BUG-0001"])
def test_resolve_item_without_archive_opt_in_does_not_find_archived_items(root: pathlib.Path, key: str) -> None:
    """The default resolution -- the one every write verb uses -- stays live-only."""
    with pytest.raises(NotFound):
        resolve_item(_ctx(root), _PROJECT, key)


def test_resolve_item_with_archive_finds_an_archived_task(root: pathlib.Path) -> None:
    result = resolve_item(_ctx(root), _PROJECT, _ARCHIVED_TASK, include_archive=True)
    assert result.item_type == "task"
    assert result.path == _archive(root) / "tasks" / _ARCHIVED_TASK
    assert result.state == "DONE"
    assert result.archived is True


def test_resolve_item_with_archive_finds_an_archived_bug_by_its_number_prefix(root: pathlib.Path) -> None:
    result = resolve_item(_ctx(root), _PROJECT, "BUG-0001", include_archive=True)
    assert result.item_type == "bug"
    assert result.path == _archive(root) / "bugs" / f"{_ARCHIVED_BUG}.md"
    assert result.state == "done"
    assert result.archived is True


def test_resolve_item_prefers_the_live_item_over_an_archived_copy(root: pathlib.Path) -> None:
    result = resolve_item(_ctx(root), _PROJECT, _BOTH_TASK, include_archive=True)
    assert result.path == _live(root) / "tasks" / _BOTH_TASK
    assert result.state == "WORKING"
    assert result.archived is False


def test_resolve_item_live_bug_resolves_live_with_archive_opt_in(root: pathlib.Path) -> None:
    result = resolve_item(_ctx(root), _PROJECT, "BUG-0003", include_archive=True)
    assert result.path == _live(root) / "bugs" / f"{_LIVE_BUG}.md"
    assert result.archived is False


def test_resolve_item_live_folder_without_status_is_not_answered_from_the_archive(root: pathlib.Path) -> None:
    """A live folder is the live answer even when it is broken; the archive copy is not consulted."""
    with pytest.raises(OpsError, match="status.md is missing"):
        resolve_item(_ctx(root), _PROJECT, _HOLLOW_TASK, include_archive=True)


def test_resolve_item_ambiguous_archive_prefix_reports_an_archived_first_match(root: pathlib.Path) -> None:
    with pytest.raises(Ambiguous) as caught:
        resolve_item(_ctx(root), _PROJECT, "CODER-20260101-003", include_archive=True)
    assert caught.value.result.archived is True
    assert caught.value.result.path == _archive(root) / "tasks" / _ARCHIVED_TWIN_A
    assert [p.name for p in caught.value.candidates] == [_ARCHIVED_TWIN_A, _ARCHIVED_TWIN_B]
    assert "archive tasks/" in str(caught.value)


def test_resolve_item_key_with_a_path_separator_never_reaches_the_archive(root: pathlib.Path) -> None:
    """``../bugs/<stem>`` would name the archived bug from inside archive/tasks/; it is not followed.

    A key that is not one plain name is refused outright
    (``resolve_item.key_invalid``) before the live tree or the archive is
    searched.
    """
    with pytest.raises(OpsError, match="resolve_item.key_invalid") as caught:
        resolve_item(_ctx(root), _PROJECT, f"../bugs/{_ARCHIVED_BUG}", include_archive=True)
    assert not isinstance(caught.value, NotFound)


def test_resolve_item_with_archive_still_reports_an_id_neither_live_nor_archived(root: pathlib.Path) -> None:
    with pytest.raises(NotFound, match="item not found"):
        resolve_item(_ctx(root), _PROJECT, _UNKNOWN_TASK, include_archive=True)


def test_resolve_item_project_without_an_archive_behaves_as_before(root: pathlib.Path) -> None:
    import shutil

    shutil.rmtree(root / "archive")
    with pytest.raises(NotFound):
        resolve_item(_ctx(root), _PROJECT, _ARCHIVED_TASK, include_archive=True)
    assert resolve_item(_ctx(root), _PROJECT, _LIVE_TASK, include_archive=True).archived is False


# ---------------------------------------------------------------------------
# ops.read.get_task_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, file, relative",
    [
        (_ARCHIVED_TASK, "status", f"tasks/{_ARCHIVED_TASK}/status.md"),
        (_ARCHIVED_TASK, "readme", f"tasks/{_ARCHIVED_TASK}/README.md"),
        ("BUG-0001", "status", f"bugs/{_ARCHIVED_BUG}.md"),
    ],
)
def test_get_task_status_reads_an_archived_document_and_marks_it(
    root: pathlib.Path, key: str, file: str, relative: str
) -> None:
    document = ops_read.get_task_status(_ctx(root), _PROJECT, key, file)
    expected = _archive(root) / relative
    assert document.archived is True
    assert document.path == expected
    assert document.text == expected.read_text(encoding="utf-8")
    notice = document.archived_notice()
    assert notice.startswith(f"archived: {key} ")
    assert str(_archive(root)) in notice


def test_get_task_status_live_document_carries_no_archived_notice(root: pathlib.Path) -> None:
    document = ops_read.get_task_status(_ctx(root), _PROJECT, _LIVE_TASK)
    assert document.archived is False
    assert document.archived_notice() == ""
    assert document.path == _live(root) / "tasks" / _LIVE_TASK / "status.md"


# ---------------------------------------------------------------------------
# show.sh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, relative",
    [
        (_ARCHIVED_TASK, f"tasks/{_ARCHIVED_TASK}/status.md"),
        ("BUG-0001", f"bugs/{_ARCHIVED_BUG}.md"),
    ],
)
def test_show_sh_finds_an_archived_item_and_marks_it_archived(
    root: pathlib.Path, tmp_path: pathlib.Path, key: str, relative: str
) -> None:
    result = _run_script(_SHOW_SH, ["--project", _PROJECT, "--key", key], root, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (_archive(root) / relative).read_text(encoding="utf-8")
    assert f"archived: {key} is not in the live project" in result.stderr


def test_show_sh_live_item_has_no_archived_marker(root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    result = _run_script(_SHOW_SH, ["--project", _PROJECT, "--key", _LIVE_TASK], root, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "archived" not in result.stderr
    assert "## State\nBACKLOG" in result.stdout


def test_show_sh_id_neither_live_nor_archived_exits_three(root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    result = _run_script(_SHOW_SH, ["--project", _PROJECT, "--key", _UNKNOWN_TASK], root, tmp_path)
    assert result.returncode == 3
    assert f"show.sh: key not found: {_UNKNOWN_TASK}" in result.stderr


# ---------------------------------------------------------------------------
# Verdict queue tails (ops.verdict) and why.sh --details
# ---------------------------------------------------------------------------


def _queue_states(root: pathlib.Path) -> dict[str, str]:
    report = ops_verdict.resolve_verdicts(root, [_PROJECT], details=True)
    assert report.details is not None and report.details.queue_tails is not None
    return {entry.task_id: entry.status_state for tail in report.details.queue_tails for entry in tail.entries}


def test_verdict_queue_tail_reads_archived_task_and_bug_states_marked_archived(root: pathlib.Path) -> None:
    states = _queue_states(root)
    assert states[_ARCHIVED_TASK] == "DONE (archived)"
    assert states[_ARCHIVED_BUG] == "done (archived)"


def test_verdict_queue_tail_live_and_unknown_entries_read_as_before(root: pathlib.Path) -> None:
    states = _queue_states(root)
    assert states[_LIVE_TASK] == "BACKLOG"
    assert states[_UNKNOWN_TASK] == "(no status.md)"
    # A live bug has no tasks/<ID>/status.md and is not archived: unchanged.
    assert states[_LIVE_BUG] == "(no status.md)"


def test_verdict_json_queue_tail_carries_the_archived_state(root: pathlib.Path) -> None:
    body = ops_verdict.verdicts_json(ops_verdict.resolve_verdicts(root, [_PROJECT], details=True))
    entries = {e["task_id"]: e["status_state"] for tail in body["queue_tails"] for e in tail["entries"]}
    assert entries[_ARCHIVED_TASK] == "DONE (archived)"


def test_verdict_live_task_scan_never_reads_the_archive(root: pathlib.Path) -> None:
    """The archive holds a BLOCKED task; the lane verdict comes from live tasks only."""
    verdict = ops_verdict.resolve_project_verdict(root, _PROJECT)
    assert verdict.verdict == "RUNNING"
    assert verdict.task_id == _BOTH_TASK


def test_why_details_shows_archived_task_and_bug_by_id(root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    result = _run_script(_WHY_SH, ["--project", _PROJECT, "--details"], root, tmp_path)
    assert "status.md: DONE (archived)" in result.stdout, result.stdout + result.stderr
    assert f"[x] {_ARCHIVED_TASK}  status.md: DONE (archived)" in result.stdout
    assert f"[x] {_ARCHIVED_BUG}  status.md: done (archived)" in result.stdout
    assert f"[ ] {_LIVE_TASK}  status.md: BACKLOG" in result.stdout


# ---------------------------------------------------------------------------
# REST: GET /show and GET /board
# ---------------------------------------------------------------------------


def _client(root: pathlib.Path):
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    return TestClient(create_app(cfg=ApiConfig(host="127.0.0.1", port=0, kanban_root=root)))


@pytest.fixture
def api_env(root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root / "tmp"))
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.mark.parametrize(
    "key, relative",
    [
        (_ARCHIVED_TASK, f"tasks/{_ARCHIVED_TASK}/status.md"),
        ("BUG-0001", f"bugs/{_ARCHIVED_BUG}.md"),
    ],
)
def test_get_show_returns_an_archived_document_by_id(api_env: pathlib.Path, key: str, relative: str) -> None:
    with _client(api_env) as client:
        response = client.get("/show", params={"project": _PROJECT, "key": key})
    body = response.json()
    assert response.status_code == 200, body
    assert body["exit_code"] == 0
    assert body["stdout"] == (_archive(api_env) / relative).read_text(encoding="utf-8")
    assert body["stderr"].startswith(f"archived: {key} is not in the live project")


def test_get_show_live_document_has_no_archived_notice(api_env: pathlib.Path) -> None:
    with _client(api_env) as client:
        body = client.get("/show", params={"project": _PROJECT, "key": _LIVE_TASK}).json()
    assert body["exit_code"] == 0
    assert body["stderr"] == ""


def test_board_columns_never_show_archived_items(api_env: pathlib.Path) -> None:
    """GET /board is a live-only scanner: archived IDs appear in no column.

    The queue lines of archived items are moved to the archive's queue files
    first, the way an archive run leaves them, so every archived ID exists
    only under archive/<project>/.
    """
    live_queues = _live(api_env) / "tasks" / "queues"
    archive_queues = _archive(api_env) / "tasks" / "queues"
    _write(live_queues / "coder_backlog.md", f"# Coder Backlog\n\n- [ ] {_LIVE_TASK}\n- [A] {_BOTH_TASK}\n")
    _write(live_queues / "bug_backlog.md", f"# Bug Backlog\n\n- [ ] {_LIVE_BUG}\n")
    _write(archive_queues / "coder_backlog.md",
           f"- [x] {_ARCHIVED_TWIN_A}\n- [x] {_ARCHIVED_TASK}\n- [x] {_ARCHIVED_TWIN_B}\n")
    _write(archive_queues / "bug_backlog.md", f"- [x] {_ARCHIVED_BUG}\n")
    with _client(api_env) as client:
        response = client.get("/board")
    assert response.status_code == 200, response.text
    keys = {item["key"] for column in response.json()["columns"] for item in column["items"]}
    # Positive control: the live siblings are on the board.
    assert any(_LIVE_TASK in key for key in keys), keys
    assert any("BUG-0003" in key for key in keys), keys
    for archived_id in (_ARCHIVED_TASK, _ARCHIVED_TWIN_A, _ARCHIVED_TWIN_B, "BUG-0001"):
        assert not any(archived_id in key for key in keys), (archived_id, keys)

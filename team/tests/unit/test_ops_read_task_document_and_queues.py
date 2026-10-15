"""
test_ops_read_task_document_and_queues.py — ``ops.read.get_task_status`` and
``ops.read.get_queues``, the two reads ``show.sh`` / ``GET /show`` and
``show-queues.sh`` / ``GET /show-queues`` share, and the read CLI
(``python3 -m pgai_agent_kanban.ops get_task_status|get_queues``) the two
scripts reach through ``pp_run_ops``.

What is proven here:

  1. ``get_task_status`` resolves a key the way show.sh always has (task
     folder, then bugs/, priority/, requirements/; exact name, then
     unambiguous prefix), reads status.md or README.md for a task and the
     intake file itself otherwise, keeps the file's bytes (CRLF included),
     reports an ambiguous prefix without failing, and raises the error the
     CLI turns into show.sh's exit codes.
  2. ``get_queues`` returns the queue pane's data -- counters, working task,
     halt flag/text, the per-agent listing, the raw queue lines -- and
     refuses a missing project directory with the
     ``show_queues.project_directory_missing`` dual-channel block and an
     empty project name with the ``show_queues.no_project`` block, whose
     remedy names a registered project or, on a registry-less root
     (queues at ``<root>/tasks/queues/``), how to register one.
  3. The read CLI prints exactly what the two scripts print, routes through
     the ``pgai_agent_kanban.ops`` package entry point, and exits with the
     shared ops exit codes; compact mode with no data project prints
     nothing to stdout.
  4. A task document that is a symlink out of its task folder -- status.md
     or README.md pointing at a sentinel outside the kanban root -- is
     refused (``resolve_item.path_outside_tree``) by show
     (``get_task_status``, ``GET /show``) and the README route
     (``get_task_readme``, ``GET /projects/{p}/tasks/{k}/readme``), and the
     sentinel's text is returned by none of them. The verification-report
     read (``get_test_report``, ``GET /test-report``) applies the same rule
     to a TESTER task's ``artifacts/report.md`` on both its version-key and
     task-key paths, and a normal report reads as before.

Every board is a throwaway directory under ``tmp_path``.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.dashboard.queue_details import resolve_queue_details
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops import read as ops_read
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import NotFound, OpsError, Refused

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_PROJECT = "alpha-proj"
_WORKING = "CODER-20260101-001-one"
_BLOCKED = "CODER-20260101-002-two"
_AMBIG_A = "CODER-20260101-010-a"
_AMBIG_B = "CODER-20260101-010-b"
_NO_README = "CODER-20260101-003-bare"
_CRLF = "CODER-20260101-004-crlf"
_WORKING_MTIME = 1767268800


def _write(path: pathlib.Path, text: str = "", raw: bytes | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(text, encoding="utf-8")


def _task(project: pathlib.Path, task_id: str, state: str, role: str | None = "CODER") -> pathlib.Path:
    task_dir = project / "tasks" / task_id
    _write(task_dir / "status.md", f"# Status\n\n## State\n{state}\n\n## Summary\nfixture {task_id}\n")
    if role is not None:
        _write(task_dir / "README.md", f"# Task {task_id}\n\n## Role\n{role}\n")
    return task_dir


@pytest.fixture
def board(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root with one registered project carrying tasks, queues and intake files."""
    root = tmp_path / "board"
    _write(root / "projects.cfg", f"[project:{_PROJECT}]\npriority = 1\n")
    project = root / "projects" / _PROJECT
    working = _task(project, _WORKING, "WORKING")
    os.utime(working / "status.md", (_WORKING_MTIME, _WORKING_MTIME))
    _task(project, _BLOCKED, "BLOCKED", role="TESTER")
    _task(project, _AMBIG_A, "BACKLOG")
    _task(project, _AMBIG_B, "BACKLOG")
    _task(project, _NO_README, "DONE", role=None)
    _write(project / "tasks" / _CRLF / "status.md", raw=b"## State\r\nDONE\r\n\r\n## Summary\r\ncrlf\r\n")
    _write(project / "tasks" / "queues" / "coder_backlog.md",
           f"# Coder\n\n- [A] {_WORKING}\n- [ ] {_AMBIG_A}\n")
    _write(project / "bugs" / "BUG-0001-crash.md", "# BUG-0001\n\n## Status\nopen\n")
    _write(project / "bugs" / "BUG-0002-a.md", "# BUG-0002-a\n\n## Status\nopen\n")
    _write(project / "bugs" / "BUG-0002-b.md", "# BUG-0002-b\n\n## Status\ndone\n")
    _write(project / "priority" / "PRIORITY-001-urgent.md", "# P1\n\n## Status\nopen\n")
    _write(project / "requirements" / "v1.0.0-feature.md", "# v1.0.0\n\n## Status\nrunning\n")
    return root


@pytest.fixture
def ctx(board: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=board)


def _project_dir(board: pathlib.Path) -> pathlib.Path:
    return board / "projects" / _PROJECT


# ---------------------------------------------------------------------------
# 1. get_task_status
# ---------------------------------------------------------------------------


def test_task_key_returns_status_md_text_state_and_fields(ctx: OpsContext, board: pathlib.Path) -> None:
    doc = ops_read.get_task_status(ctx, _PROJECT, _WORKING)
    status = _project_dir(board) / "tasks" / _WORKING / "status.md"
    assert doc.item_type == "task"
    assert doc.state == "WORKING"
    assert doc.path == status
    assert doc.text == status.read_text(encoding="utf-8")
    assert doc.fields["State"] == "WORKING"
    assert doc.fields["Summary"] == f"fixture {_WORKING}"
    assert doc.candidates == () and doc.ambiguity == "" and doc.ambiguity_notice() == ""


def test_readme_file_selects_the_task_readme(ctx: OpsContext, board: pathlib.Path) -> None:
    doc = ops_read.get_task_status(ctx, _PROJECT, _WORKING, "readme")
    readme = _project_dir(board) / "tasks" / _WORKING / "README.md"
    assert doc.path == readme
    assert doc.text == readme.read_text(encoding="utf-8")


def test_unambiguous_prefix_resolves_to_the_one_task(ctx: OpsContext) -> None:
    doc = ops_read.get_task_status(ctx, _PROJECT, "CODER-20260101-002")
    assert doc.path.parent.name == _BLOCKED
    assert doc.key == "CODER-20260101-002"


def test_ambiguous_task_prefix_reads_the_first_match_and_carries_the_notice(
    ctx: OpsContext, board: pathlib.Path
) -> None:
    doc = ops_read.get_task_status(ctx, _PROJECT, "CODER-20260101-010")
    tasks = _project_dir(board) / "tasks"
    assert doc.path == tasks / _AMBIG_A / "status.md"
    assert doc.candidates == (tasks / _AMBIG_A, tasks / _AMBIG_B)
    notice = doc.ambiguity_notice().splitlines()
    assert notice[0] == doc.ambiguity
    assert "ambiguous key 'CODER-20260101-010'" in notice[0]
    assert notice[1:] == [
        f"resolve_item:   {tasks / _AMBIG_A}",
        f"resolve_item:   {tasks / _AMBIG_B}",
        f"resolve_item: resolving to first match: {tasks / _AMBIG_A}",
    ]


def test_ambiguous_intake_prefix_names_the_file_as_first_match(ctx: OpsContext, board: pathlib.Path) -> None:
    doc = ops_read.get_task_status(ctx, _PROJECT, "BUG-0002")
    first = _project_dir(board) / "bugs" / "BUG-0002-a.md"
    assert doc.path == first
    assert doc.ambiguity_notice().splitlines()[-1] == f"resolve_item: resolving to first match: {first}"


@pytest.mark.parametrize(
    ("key", "item_type", "relative"),
    [
        ("BUG-0001", "bug", "bugs/BUG-0001-crash.md"),
        ("PRIORITY-001", "priority", "priority/PRIORITY-001-urgent.md"),
        ("v1.0.0-feature", "requirement", "requirements/v1.0.0-feature.md"),
    ],
)
def test_intake_key_returns_the_intake_file_whatever_the_file_argument(
    ctx: OpsContext, board: pathlib.Path, key: str, item_type: str, relative: str
) -> None:
    expected = _project_dir(board) / relative
    for file in ("status", "readme"):
        doc = ops_read.get_task_status(ctx, _PROJECT, key, file)
        assert doc.item_type == item_type
        assert doc.path == expected
        assert doc.text == expected.read_text(encoding="utf-8")


def test_crlf_line_endings_are_kept_as_written(ctx: OpsContext) -> None:
    doc = ops_read.get_task_status(ctx, _PROJECT, _CRLF)
    assert doc.text == "## State\r\nDONE\r\n\r\n## Summary\r\ncrlf\r\n"


def test_unknown_key_raises_not_found(ctx: OpsContext) -> None:
    with pytest.raises(NotFound, match="item not found for key 'NOPE-1'"):
        ops_read.get_task_status(ctx, _PROJECT, "NOPE-1")


def test_task_without_readme_raises_naming_the_missing_file(ctx: OpsContext, board: pathlib.Path) -> None:
    missing = _project_dir(board) / "tasks" / _NO_README / "README.md"
    with pytest.raises(OpsError) as excinfo:
        ops_read.get_task_status(ctx, _PROJECT, _NO_README, "readme")
    assert not isinstance(excinfo.value, NotFound)
    assert str(excinfo.value) == f"readme.md not found for task {_NO_README}: {missing}"


def test_file_other_than_status_or_readme_is_refused(ctx: OpsContext) -> None:
    with pytest.raises(OpsError, match="file must be one of status, readme"):
        ops_read.get_task_status(ctx, _PROJECT, _WORKING, "bogus")


def test_missing_project_root_raises_ops_error(ctx: OpsContext) -> None:
    with pytest.raises(OpsError, match="project root does not exist"):
        ops_read.get_task_status(ctx, "no-such-project", _WORKING)


# ---------------------------------------------------------------------------
# 2. get_queues
# ---------------------------------------------------------------------------


def test_get_queues_returns_counters_working_task_listing_and_queue_lines(
    ctx: OpsContext, board: pathlib.Path
) -> None:
    queues = ops_read.get_queues(ctx, _PROJECT)
    tasks = _project_dir(board) / "tasks"

    assert set(queues) == {"project", "counts", "working", "halt", "details", "entries"}
    assert queues["project"] == _PROJECT
    # CODER: the working task, two backlog tasks, the README-less done task
    # and the README-less CRLF task (counted under CODER, its state is DONE).
    assert queues["counts"]["CODER"] == {"TOTAL": 5, "DONE": 2, "WORKING": 1, "BLOCKED": 0, "WAITING": 0}
    assert queues["counts"]["TESTER"] == {"TOTAL": 1, "DONE": 0, "WORKING": 0, "BLOCKED": 1, "WAITING": 0}
    assert queues["working"] == {"task_id": _WORKING, "mtime": _WORKING_MTIME, "role": "CODER"}
    assert queues["halt"] == {"flag": "no", "text": ""}
    assert queues["details"] == resolve_queue_details(tasks, tasks / "queues")
    assert queues["entries"] == [
        {"queue": "coder_backlog", "task_line": f"- [A] {_WORKING}"},
        {"queue": "coder_backlog", "task_line": f"- [ ] {_AMBIG_A}"},
    ]


@pytest.mark.parametrize(
    ("project_file", "body", "root_halt", "expected"),
    [
        ("HALT", "", False, {"flag": "yes", "text": "HALT"}),
        ("HALT-AFTER", "rc\n", False, {"flag": "no", "text": "HALT-AFTER rc"}),
        ("HALT-AFTER", "rc\n", True, {"flag": "no", "text": "HALT-AFTER rc"}),
        (None, "", True, {"flag": "yes", "text": "HALT"}),
    ],
    ids=["project-halted", "draining", "draining-wins-over-global", "global-halt"],
)
def test_get_queues_halt_pair_follows_the_dashboard_rules(
    ctx: OpsContext, board: pathlib.Path, project_file, body: str, root_halt: bool, expected: dict
) -> None:
    if project_file:
        _write(_project_dir(board) / project_file, body)
    if root_halt:
        _write(board / "HALT")
    assert ops_read.get_queues(ctx, _PROJECT)["halt"] == expected


def test_get_queues_with_no_working_task_reports_none(ctx: OpsContext, board: pathlib.Path) -> None:
    _write(_project_dir(board) / "tasks" / _WORKING / "status.md", "## State\nDONE\n")
    assert ops_read.get_queues(ctx, _PROJECT)["working"] is None


def test_get_queues_refuses_a_missing_project_directory_dual_channel(ctx: OpsContext, board: pathlib.Path) -> None:
    with pytest.raises(NotFound) as excinfo:
        ops_read.get_queues(ctx, "ghost")
    contract = parse_error_contract(str(excinfo.value))
    assert contract is not None
    assert contract["error"] == "show_queues.project_directory_missing"
    assert str(board / "projects" / "ghost") in contract["message"]
    assert contract["remedy"] == (
        "Check the project name against projects.cfg, or create it first with "
        "team/scripts/create-project.sh --project ghost."
    )


@pytest.mark.parametrize("project", ["", "  "], ids=["empty", "blank"])
def test_get_queues_refuses_an_empty_project_naming_a_registered_one(
    ctx: OpsContext, project: str
) -> None:
    with pytest.raises(Refused) as excinfo:
        ops_read.get_queues(ctx, project)
    contract = parse_error_contract(str(excinfo.value))
    assert contract is not None
    assert contract["error"] == "show_queues.no_project"
    assert "no project specified; pass --project <name> or set PGAI_PROJECT_NAME" in contract["message"]
    assert contract["remedy"] == (
        f"Name a registered project ({_PROJECT}): "
        f"team/scripts/dashboard/show-queues.sh --project {_PROJECT} "
        f"(REST: GET /show-queues?project={_PROJECT}), or export "
        f"PGAI_PROJECT_NAME={_PROJECT} before running it."
    )


def test_get_queues_refuses_an_empty_project_on_a_single_project_root_with_real_queues(
    tmp_path: pathlib.Path,
) -> None:
    # A single-project root: no projects.cfg, queues at <root>/tasks/queues/
    # holding real entries. The board reads registered project directories
    # only, so it refuses rather than report the entries as absent.
    root = tmp_path / "legacy"
    _task(root, _WORKING, "BACKLOG")
    _task(root, _BLOCKED, "DONE")
    _write(root / "tasks" / "queues" / "coder_backlog.md", f"- [ ] {_WORKING}\n- [x] {_BLOCKED}\n")

    with pytest.raises(Refused) as excinfo:
        ops_read.get_queues(OpsContext(kanban_root=root), "")
    contract = parse_error_contract(str(excinfo.value))
    assert contract is not None
    assert contract["error"] == "show_queues.no_project"
    assert str(root.resolve() / "projects.cfg") in contract["message"]
    assert "<" not in contract["remedy"]
    assert contract["remedy"].startswith("Register the project, then name it: team/scripts/create-project.sh")


# ---------------------------------------------------------------------------
# 3. The read CLI
# ---------------------------------------------------------------------------


def test_cli_get_task_status_prints_the_document_bytes(
    board: pathlib.Path, capfdbinary: pytest.CaptureFixture
) -> None:
    rc = ops_read._cli_main(["get_task_status", str(board), _PROJECT, _CRLF])
    out, err = capfdbinary.readouterr()
    assert rc == 0
    assert out == b"## State\r\nDONE\r\n\r\n## Summary\r\ncrlf\r\n"
    assert err == b""


def test_cli_get_task_status_ambiguous_key_prints_notice_and_exits_zero(
    board: pathlib.Path, capfdbinary: pytest.CaptureFixture
) -> None:
    rc = ops_read._cli_main(["get_task_status", str(board), _PROJECT, "CODER-20260101-010", "readme"])
    out, err = capfdbinary.readouterr()
    assert rc == 0
    assert out == (_project_dir(board) / "tasks" / _AMBIG_A / "README.md").read_bytes()
    assert b"resolving to first match" in err


@pytest.mark.parametrize(
    ("args", "expected_rc"),
    [
        (["NOPE-1"], 3),
        ([_NO_README, "readme"], 1),
        ([_WORKING, "bogus"], 1),
    ],
    ids=["not-found", "missing-document", "bad-file"],
)
def test_cli_get_task_status_exit_codes(
    board: pathlib.Path, capfd: pytest.CaptureFixture, args: list[str], expected_rc: int
) -> None:
    rc = ops_read._cli_main(["get_task_status", str(board), _PROJECT, *args])
    out, err = capfd.readouterr()
    assert rc == expected_rc
    assert out == ""
    assert err.strip()


def test_cli_get_queues_prints_the_compact_pane(
    board: pathlib.Path, capfd: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("PGAI_AGENT_PANE_WIDTH", raising=False)
    rc = ops_read._cli_main(["get_queues", str(board), _PROJECT, _PROJECT, "0"])
    out, err = capfd.readouterr()
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == "Active provider: claude"
    assert lines[1] == "pm:     0/0"
    # 36-column row: label (8) + "2/5" + 2 spaces + "1W" + padding + "40%".
    assert lines[2] == "coder:  2/5  1W" + " " * 18 + "40%"
    assert "one" in lines
    assert "kanban.cfg not found" in err


def test_cli_get_queues_refuses_a_missing_project_directory(
    board: pathlib.Path, capfd: pytest.CaptureFixture
) -> None:
    rc = ops_read._cli_main(["get_queues", str(board), "ghost", "ghost", "1"])
    out, err = capfd.readouterr()
    assert rc == 3
    assert out == ""
    assert "error:   show_queues.project_directory_missing" in err


def test_cli_get_queues_compact_with_no_data_project_refuses_and_prints_no_counters(
    board: pathlib.Path, capfd: pytest.CaptureFixture
) -> None:
    rc = ops_read._cli_main(["get_queues", str(board), "", "", "0"])
    out, err = capfd.readouterr()
    assert rc == 2
    assert out == ""
    assert err.startswith("error:   show_queues.no_project\n")


def test_cli_get_queues_details_with_no_project_still_lists_every_registered_project(
    board: pathlib.Path, capfd: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TERM", "dumb")
    rc = ops_read._cli_main(["get_queues", str(board), "", "", "1"])
    out, _err = capfd.readouterr()
    assert rc == 0
    assert f"  {_WORKING}" in out.splitlines()


@pytest.mark.parametrize(
    "argv",
    [[], ["get_task_status", "only-root"], ["get_queues", "root", "p"], ["no_such_verb"]],
    ids=["no-verb", "task-status-short", "queues-short", "unknown-verb"],
)
def test_cli_usage_errors_exit_one(argv: list[str], capfd: pytest.CaptureFixture) -> None:
    assert ops_read._cli_main(argv) == 1
    assert capfd.readouterr().err.strip()


def test_package_entry_point_routes_read_verbs_to_the_read_cli(board: pathlib.Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(_TEAM_DIR)}
    result = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.ops", "get_task_status", str(board), _PROJECT, _WORKING],
        capture_output=True, text=True, env=env, timeout=60, cwd=str(_TEAM_DIR),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (_project_dir(board) / "tasks" / _WORKING / "status.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Task documents are confined to their task folder
# ---------------------------------------------------------------------------

_LINKED_STATUS = "CODER-20260101-020-linked-status"
_LINKED_README = "CODER-20260101-021-linked-readme"
_SENTINEL_TEXT = "SENTINEL-OUTSIDE-THE-KANBAN-ROOT"


@pytest.fixture
def linked_board(board: pathlib.Path, tmp_path: pathlib.Path) -> pathlib.Path:
    """The board plus two tasks whose status.md / README.md link to a file outside the root."""
    sentinel = tmp_path / "outside" / "secret.md"
    _write(sentinel, f"## State\nDONE\n\n{_SENTINEL_TEXT}\n")
    project = _project_dir(board)
    status_task = project / "tasks" / _LINKED_STATUS
    status_task.mkdir(parents=True)
    (status_task / "status.md").symlink_to(sentinel)
    _write(status_task / "README.md", "# Task\n")
    readme_task = _task(project, _LINKED_README, "WORKING", role=None)
    (readme_task / "README.md").symlink_to(sentinel)
    return board


@pytest.mark.parametrize(("key", "file"), [(_LINKED_STATUS, "status"), (_LINKED_STATUS, "readme"), (_LINKED_README, "readme")])
def test_show_refuses_a_task_document_linked_outside_its_folder(
    linked_board: pathlib.Path, key: str, file: str
) -> None:
    with pytest.raises(OpsError) as caught:
        ops_read.get_task_status(OpsContext(kanban_root=linked_board), _PROJECT, key, file)
    assert "resolve_item.path_outside_tree" in str(caught.value)
    assert _SENTINEL_TEXT not in str(caught.value)


@pytest.mark.parametrize("key", [_LINKED_STATUS, _LINKED_README])
def test_readme_route_refuses_a_task_document_linked_outside_its_folder(linked_board: pathlib.Path, key: str) -> None:
    output = ops_read.render_task_readme(OpsContext(kanban_root=linked_board), _PROJECT, key)
    assert output.exit_code == 1, output
    assert output.stdout == ""
    assert "resolve_item.path_outside_tree" in output.stderr, output.stderr
    assert _SENTINEL_TEXT not in output.stdout + output.stderr


def test_rest_show_and_readme_routes_do_not_return_the_sentinel(linked_board: pathlib.Path) -> None:
    from tests.fixtures.route_cli_parity import build_api_client

    client = build_api_client(linked_board)
    responses = [
        client.get("/show", params={"project": _PROJECT, "key": _LINKED_STATUS}),
        client.get("/show", params={"project": _PROJECT, "key": _LINKED_README, "file": "readme"}),
        client.get(f"/projects/{_PROJECT}/tasks/{_LINKED_STATUS}/readme"),
        client.get(f"/projects/{_PROJECT}/tasks/{_LINKED_README}/readme"),
    ]
    for response in responses:
        assert response.status_code != 200, response.text
        assert _SENTINEL_TEXT not in response.text, response.text
        assert "resolve_item.path_outside_tree" in response.text, response.text


def test_normal_task_folders_still_read_beside_linked_ones(linked_board: pathlib.Path) -> None:
    ctx = OpsContext(kanban_root=linked_board)
    status = _project_dir(linked_board) / "tasks" / _WORKING / "status.md"
    assert ops_read.get_task_status(ctx, _PROJECT, _WORKING).text == status.read_text(encoding="utf-8")
    readme = ops_read.render_task_readme(ctx, _PROJECT, _WORKING)
    assert readme.exit_code == 0 and readme.stdout.startswith(f"# Task {_WORKING}")


# ---------------------------------------------------------------------------
# 5. The verification report is confined to its task folder
# ---------------------------------------------------------------------------

_LINKED_REPORT_TASK = "TESTER-20260101-030-verify-9-9-9"
_NORMAL_REPORT_TASK = "TESTER-20260101-031-verify-9-9-8"
_NORMAL_REPORT_TEXT = "# Verification report\n\nall green\n"


@pytest.fixture
def report_board(board: pathlib.Path, tmp_path: pathlib.Path) -> pathlib.Path:
    """The board plus a TESTER task whose report.md links outside the root, and one normal report."""
    sentinel = tmp_path / "outside" / "SECRET.txt"
    _write(sentinel, f"{_SENTINEL_TEXT}\n")
    project = _project_dir(board)
    linked = _task(project, _LINKED_REPORT_TASK, "DONE", role="TESTER")
    (linked / "artifacts").mkdir()
    (linked / "artifacts" / "report.md").symlink_to(sentinel)
    normal = _task(project, _NORMAL_REPORT_TASK, "DONE", role="TESTER")
    _write(normal / "artifacts" / "report.md", _NORMAL_REPORT_TEXT)
    return board


@pytest.mark.parametrize("key", ["v9.9.9", _LINKED_REPORT_TASK])
def test_test_report_refuses_a_report_linked_outside_its_task_folder(report_board: pathlib.Path, key: str) -> None:
    with pytest.raises(OpsError) as caught:
        ops_read.get_test_report(OpsContext(kanban_root=report_board), _PROJECT, key)
    assert getattr(caught.value, "error", "") == "resolve_item.path_outside_tree", caught.value
    assert "resolve_item.path_outside_tree" in str(caught.value)
    assert _SENTINEL_TEXT not in str(caught.value)

    output = ops_read.render_test_report(OpsContext(kanban_root=report_board), _PROJECT, key)
    assert output.exit_code != 0, output
    assert output.stdout == ""
    assert _SENTINEL_TEXT not in output.stdout + output.stderr


def test_rest_test_report_route_does_not_return_the_linked_report(report_board: pathlib.Path) -> None:
    from tests.fixtures.route_cli_parity import build_api_client

    client = build_api_client(report_board)
    for key in ("v9.9.9", _LINKED_REPORT_TASK):
        response = client.get("/test-report", params={"project": _PROJECT, "key": key})
        assert response.status_code != 200, response.text
        assert _SENTINEL_TEXT not in response.text, response.text
        assert "resolve_item.path_outside_tree" in response.text, response.text


@pytest.mark.parametrize("key", ["v9.9.8", _NORMAL_REPORT_TASK])
def test_a_normal_test_report_reads_as_before(report_board: pathlib.Path, key: str) -> None:
    report = ops_read.get_test_report(OpsContext(kanban_root=report_board), _PROJECT, key)
    assert report.task == _NORMAL_REPORT_TASK
    assert report.text == _NORMAL_REPORT_TEXT
    assert report.exit_code == 0

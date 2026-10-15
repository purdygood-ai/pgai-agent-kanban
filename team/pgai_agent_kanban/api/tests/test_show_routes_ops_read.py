"""
test_show_routes_ops_read.py — GET /show and GET /show-queues call the
``ops.read`` function their CLI script reaches through ``pp_run_ops``
(``get_task_status``, ``get_queues``), in process, and start no subprocess.

What is proven here:

  1. Each route calls its function -- ``ops.read.get_task_status`` with the
     resolved project, the key and the document; ``ops.read.get_queues``
     for every project the pane reads -- while every way of starting a
     process (``subprocess.*``, ``os.system``, ``os.popen``, the adapter's
     and the read router's ``shell_out``) is patched to fail the test.
  2. The envelope carries show.sh's own refusals: a bad ``file`` (exit 1,
     before anything is resolved), an unregistered project
     (``project_resolve.not_found``, exit 1), a key matching nothing
     (exit 3 with show.sh's "key not found" line); and show-queues'
     ``show_queues.project_directory_missing`` refusal on a registry-less
     root and ``show_queues.no_project`` refusal when compact mode is
     given no project (non-200, no counter text) -- on a registered root
     and on a single-project root whose ``tasks/queues/`` holds entries.
  3. Against the real CLI scripts on the same board, the REST ``stdout`` and
     ``exit_code`` equal the script's stdout and exit code (the compact
     pane's wall-clock ``elapsed:`` line masked), and the no-project
     refusal is byte-identical on both (exit code, stdout, stderr).

Every board is a throwaway directory under ``tmp_path``; the API is rooted at
it (``ApiConfig.kanban_root``) and the CLI scripts are pointed at it through
``PGAI_AGENT_KANBAN_ROOT_PATH`` / ``KANBAN_ROOT`` with ``PGAI_PROJECT_NAME``
cleared.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from typing import Callable, Generator

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api import adapter as adapter_module
from pgai_agent_kanban.api import reads as reads_module
from pgai_agent_kanban.dashboard.queue_details import empty_queue_details, resolve_queue_details
from pgai_agent_kanban.ops import read as ops_read

# This file lives at team/pgai_agent_kanban/api/tests/; four levels up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_PROJECT = "show-proj"
_OTHER = "other-proj"
_UNBUILT = "unbuilt-proj"  # registered, no directory
_WORKING = "CODER-20260101-001-alpha"
_BACKLOG = "CODER-20260101-002-beta"
_ELAPSED_RE = re.compile(r"^  elapsed: .*$", re.MULTILINE)


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_board(root: pathlib.Path) -> pathlib.Path:
    _write(root / "projects.cfg",
           f"[project:{_PROJECT}]\npriority = 1\n[project:{_OTHER}]\npriority = 2\n"
           f"[project:{_UNBUILT}]\npriority = 3\n")
    _write(root / "kanban.cfg", "[providers]\nactive = codex\n")
    project = root / "projects" / _PROJECT
    for task_id, state in ((_WORKING, "WORKING"), (_BACKLOG, "BACKLOG")):
        _write(project / "tasks" / task_id / "status.md", f"## State\n{state}\n\n## Summary\nfixture\n")
        _write(project / "tasks" / task_id / "README.md", f"# {task_id}\n\n## Role\nCODER\n")
    _write(project / "tasks" / "queues" / "coder_backlog.md", f"- [A] {_WORKING}\n")
    _write(project / "bugs" / "BUG-0001-crash.md", "# BUG-0001\n\n## Status\nopen\n")
    (root / "projects" / _OTHER / "tasks" / "queues").mkdir(parents=True)
    return root


@pytest.fixture
def board(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = _build_board(tmp_path / "board")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    monkeypatch.setenv("TERM", "dumb")
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR", "PGAI_AGENT_PANE_WIDTH"):
        monkeypatch.delenv(name, raising=False)
    return root


def _client_for(root: pathlib.Path) -> TestClient:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


@pytest.fixture
def client(board: pathlib.Path) -> Generator[TestClient, None, None]:
    with _client_for(board) as c:
        yield c


class _ProcessStarted(AssertionError):
    """Raised by every patched process starter."""


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every way a route could start a process fail the test."""
    started: list[str] = []

    def _forbid(name: str) -> Callable[..., None]:
        def _starter(*args, **kwargs):
            started.append(name)
            raise _ProcessStarted(f"{name} was called: a module: route must start no process")

        return _starter

    for attr in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, attr, _forbid(f"subprocess.{attr}"))
    monkeypatch.setattr(os, "system", _forbid("os.system"))
    monkeypatch.setattr(os, "popen", _forbid("os.popen"))
    monkeypatch.setattr(adapter_module, "shell_out", _forbid("adapter.shell_out"))
    monkeypatch.setattr(reads_module, "shell_out", _forbid("reads.shell_out"))
    return started


def _spy(monkeypatch: pytest.MonkeyPatch, name: str) -> list[tuple]:
    """Wrap ``ops.read.<name>`` so calls are recorded and still performed."""
    calls: list[tuple] = []
    real = getattr(ops_read, name)

    def _recording(ctx, *args, **kwargs):
        calls.append((ctx, args, kwargs))
        return real(ctx, *args, **kwargs)

    monkeypatch.setattr(ops_read, name, _recording)
    return calls


def _cli(script: str, args: list[str], root: pathlib.Path, temp: pathlib.Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR")}
    env.update({"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "KANBAN_ROOT": str(root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp), "TERM": "dumb"})
    return subprocess.run(["bash", str(_SCRIPTS_DIR / script), *args], capture_output=True, text=True,
                          env=env, cwd=str(_TEAM_DIR), timeout=60, stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# 1. The routes call their ops.read function and start no process
# ---------------------------------------------------------------------------


def test_show_calls_get_task_status_with_the_resolved_project(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_task_status")

    resp = client.get("/show", params={"project": "show", "key": _WORKING})

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    ctx, args, _kwargs = calls[0]
    assert ctx.kanban_root == board.resolve()
    assert args == (_PROJECT, _WORKING, "status")
    body = resp.json()
    status = board / "projects" / _PROJECT / "tasks" / _WORKING / "status.md"
    assert body == {"exit_code": 0, "stdout": status.read_text(encoding="utf-8"), "stderr": "", "warnings": []}
    assert no_subprocess == []


def test_show_passes_the_readme_document_and_serves_intake_keys(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_task_status")

    readme = client.get("/show", params={"project": _PROJECT, "key": _BACKLOG, "file": "readme"}).json()
    bug = client.get("/show", params={"project": _PROJECT, "key": "BUG-0001"}).json()

    assert [args for _ctx, args, _kw in calls] == [(_PROJECT, _BACKLOG, "readme"), (_PROJECT, "BUG-0001", "status")]
    assert readme["stdout"] == (board / "projects" / _PROJECT / "tasks" / _BACKLOG / "README.md").read_text()
    assert bug["stdout"] == (board / "projects" / _PROJECT / "bugs" / "BUG-0001-crash.md").read_text()
    assert no_subprocess == []


def test_show_queues_compact_calls_get_queues_and_renders_the_pane(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_queues")

    resp = client.get("/show-queues", params={"project": "show"})

    assert resp.status_code == 200, resp.text
    assert [(ctx.kanban_root, args) for ctx, args, _kw in calls] == [(board.resolve(), (_PROJECT,))]
    lines = resp.json()["stdout"].splitlines()
    assert lines[0] == "Active provider: codex"
    assert lines[2] == "coder:  0/2  1W" + " " * 19 + "0%"
    assert "alpha" in lines
    assert no_subprocess == []


def test_show_queues_details_returns_the_get_queues_listing(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_queues")

    one = client.get("/show-queues", params={"project": _PROJECT, "details": "true"}).json()
    everyone = client.get("/show-queues", params={"details": "true"}).json()

    tasks = board / "projects" / _PROJECT / "tasks"
    expected = resolve_queue_details(tasks, tasks / "queues")
    assert one == {"project": _PROJECT, "details": expected, "warnings": []}
    assert everyone["project"] is None
    assert list(everyone["details"]) == [_PROJECT, _OTHER, _UNBUILT]
    assert everyone["details"][_PROJECT] == expected
    assert everyone["details"][_UNBUILT] == empty_queue_details()
    assert [args for _ctx, args, _kw in calls] == [(_PROJECT,), (_PROJECT,), (_OTHER,)]
    assert no_subprocess == []


# ---------------------------------------------------------------------------
# 2. Refusals reach the envelope as show.sh / show-queues.sh print them
# ---------------------------------------------------------------------------


def test_show_bad_file_is_refused_before_anything_is_resolved(
    client: TestClient, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_task_status")
    resp = client.get("/show", params={"project": "ghost", "key": _WORKING, "file": "bogus"})
    assert resp.status_code == 500
    assert resp.json()["exit_code"] == 1
    assert resp.json()["stderr"] == 'show.sh: --file value must be "status" or "readme"; got: bogus\n'
    assert calls == []


def test_show_unregistered_project_refuses_with_the_resolver_block(
    client: TestClient, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_task_status")
    body = client.get("/show", params={"project": "ghost", "key": _WORKING}).json()
    assert body["exit_code"] == 1
    assert "error:   project_resolve.not_found" in body["stderr"]
    assert calls == []


def test_show_unknown_key_exits_three_with_the_key_not_found_line(
    client: TestClient, no_subprocess: list[str]
) -> None:
    resp = client.get("/show", params={"project": _PROJECT, "key": "NOPE-1"})
    body = resp.json()
    assert resp.status_code == 404
    assert body["exit_code"] == 3
    assert body["stdout"] == ""
    assert body["stderr"].endswith("show.sh: key not found: NOPE-1\n")


def test_show_queues_registry_less_root_refuses_a_missing_project_directory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, no_subprocess: list[str]
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    with _client_for(bare) as bare_client:
        resp = bare_client.get("/show-queues", params={"project": "ghost"})
    body = resp.json()
    assert resp.status_code == 404
    assert body["exit_code"] == 1
    assert body["stdout"] == ""
    assert "error:   show_queues.project_directory_missing" in body["stderr"]


def test_show_queues_compact_with_no_project_refuses_and_renders_no_counters(
    client: TestClient, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_queues")
    resp = client.get("/show-queues")
    body = resp.json()
    assert resp.status_code == 409
    assert body["exit_code"] == 1
    assert body["stdout"] == ""
    assert body["stderr"].startswith("error:   show_queues.no_project\n")
    assert f"--project {_PROJECT}" in body["stderr"]
    assert [args for _ctx, args, _kw in calls] == [("",)]
    assert no_subprocess == []


def test_show_queues_no_project_on_a_single_project_root_refuses_the_same_way(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, no_subprocess: list[str]
) -> None:
    legacy = tmp_path / "legacy"
    _write(legacy / "tasks" / _WORKING / "status.md", "## State\nBACKLOG\n")
    _write(legacy / "tasks" / "queues" / "coder_backlog.md", f"- [ ] {_WORKING}\n")
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    with _client_for(legacy) as legacy_client:
        resp = legacy_client.get("/show-queues")
    body = resp.json()
    assert resp.status_code == 409
    assert body["exit_code"] == 1
    assert body["stdout"] == ""
    assert body["stderr"].startswith("error:   show_queues.no_project\n")


# ---------------------------------------------------------------------------
# 3. REST and CLI agree on the same board
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"project": _PROJECT, "key": _WORKING},
        {"project": "show", "key": "CODER-20260101-002", "file": "readme"},
        {"project": _PROJECT, "key": "NOPE-1"},
    ],
    ids=["status", "prefix-readme", "not-found"],
)
def test_show_rest_matches_the_cli(client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path,
                                   params: dict) -> None:
    argv = [arg for key, value in params.items() for arg in (f"--{key}", value)]
    cli = _cli("show.sh", argv, board, tmp_path / "cli_tmp")
    body = client.get("/show", params=params).json()
    assert (body["exit_code"], body["stdout"], body["stderr"]) == (cli.returncode, cli.stdout, cli.stderr)


@pytest.mark.parametrize("params", [{"project": _PROJECT}, {"project": _OTHER}], ids=["working", "idle"])
def test_show_queues_rest_matches_the_cli(client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path,
                                          params: dict) -> None:
    cli = _cli("dashboard/show-queues.sh", ["--project", params["project"]], board, tmp_path / "cli_tmp")
    body = client.get("/show-queues", params=params).json()
    assert cli.returncode == 0, cli.stderr
    assert body["exit_code"] == 0
    assert _ELAPSED_RE.sub("", body["stdout"]) == _ELAPSED_RE.sub("", cli.stdout)


def test_show_queues_no_project_refusal_is_byte_identical_on_rest_and_cli(
    client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    cli = _cli("dashboard/show-queues.sh", [], board, tmp_path / "cli_tmp")
    body = client.get("/show-queues").json()
    assert cli.returncode == 1
    assert (body["exit_code"], body["stdout"], body["stderr"]) == (cli.returncode, cli.stdout, cli.stderr)

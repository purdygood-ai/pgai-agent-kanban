"""
test_read_routes_ops_read.py — GET /status, /test-report,
/projects/{project}/tasks/{key}/readme and /rejected call the ``ops.read``
function their CLI script reaches through ``pp_run_ops`` (``get_status``,
``get_test_report``, ``get_task_readme``, ``list_rejected``), in process,
and start no subprocess.

None of the four is a row in ``operator_verbs.manifest``, so the route/CLI
parity helper is given a manifest-shaped record for each instead.

What is proven here:

  1. Each route calls its function with the request's project (and key)
     while every way of starting a process (``subprocess.*``, ``os.system``,
     ``os.popen``, the adapter's and the read router's ``shell_out``) is
     patched to fail the test -- on success and on the refusal paths.
  2. The route/CLI parity helper passes both halves for each route: the
     route reaches the function with process starts forbidden, and the
     script, run for real, reaches the same function through ``pp_run_ops``.
  3. Against the real scripts on the same board, each envelope's
     ``exit_code``, ``stdout`` and ``stderr`` equal the script's -- including
     an unknown project, an unknown key and an ambiguous key -- and the
     readme route's 404 ``detail`` is the script's stderr.
  4. ``no_color=true`` on /status renders without colour even when the
     server's ``TERM`` would colour it.
  5. /status's ``ship_posture`` field and kanban-status.sh's
     ``Ship posture:`` line show the same resolved value for a project set
     to ``always``, one with the key absent, and one that pushes to origin
     with ``always`` set but unconfirmed (the remote-lane guard reads it as
     ``matrix`` and both surfaces carry the resolver's warning on stderr).

Every board is a throwaway directory under ``tmp_path``; the API is rooted at
it (``ApiConfig.kanban_root``) and the scripts are pointed at it through
``PGAI_AGENT_KANBAN_ROOT_PATH`` / ``KANBAN_ROOT`` with ``PGAI_PROJECT_NAME``
cleared.
"""

from __future__ import annotations

import io
import os
import pathlib
import subprocess
import time
import types
from typing import Callable, Generator

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api import adapter as adapter_module
from pgai_agent_kanban.api import reads as reads_module
from pgai_agent_kanban.ops import read as ops_read
from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/pgai_agent_kanban/api/tests/; four levels up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_PROJECT = "reads-proj"
_TESTER = "TESTER-20260101-001-verify-1-0-0"
_AMBIG_A = "TESTER-20260102-002-a"
_AMBIG_B = "TESTER-20260102-002-b"
_CODER = "CODER-20260101-003-blocked"


def _write(path: pathlib.Path, text: str = "", mtime: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _build_board(root: pathlib.Path) -> pathlib.Path:
    """A board whose status summary does not change from one second to the next."""
    _write(root / "VERSION", "v7.0.0\n")
    _write(root / "projects.cfg", f"[project:{_PROJECT}]\npriority = 1\n")
    project = root / "projects" / _PROJECT
    opened = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 10 * 86400 - 600))
    _write(project / "release-state.md",
           f"## Active RC\nv1.0.0\n\n## RC Opened At\n{opened}\n\n## Last Released\nv0.9.0\n")
    for task_id, state, role in ((_TESTER, "DONE", "TESTER"), (_AMBIG_A, "DONE", "TESTER"),
                                 (_AMBIG_B, "DONE", "TESTER"), (_CODER, "BLOCKED", "CODER")):
        _write(project / "tasks" / task_id / "status.md", f"## State\n{state}\n")
        _write(project / "tasks" / task_id / "README.md", f"# {task_id}\n\n## Role\n{role}\n")
    for task_id in (_TESTER, _AMBIG_A, _AMBIG_B):
        _write(project / "tasks" / task_id / "artifacts" / "report.md", f"# Report {task_id}\n")
    _write(project / "bugs" / "BUG-0001-crash.md", "# BUG-0001\n\n## Status\nopen\n")
    _write(project / "bugs" / ".rejected" / "BUG-7-bad.md", "x", mtime=1767268800)
    _write(project / "rejected" / "held.md", "x")
    return root


@pytest.fixture
def board(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = _build_board(tmp_path / "board")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    monkeypatch.setenv("TERM", "dumb")
    for name in ("NO_COLOR", "PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.fixture
def client(board: pathlib.Path) -> Generator[TestClient, None, None]:
    with build_api_client(board) as c:
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
            raise _ProcessStarted(f"{name} was called: these routes must start no process")

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
        calls.append((ctx.kanban_root, args))
        return real(ctx, *args, **kwargs)

    monkeypatch.setattr(ops_read, name, _recording)
    return calls


def _script_env(root: pathlib.Path, tmp_path: pathlib.Path) -> dict:
    scratch = tmp_path / "cli_tmp"
    scratch.mkdir(exist_ok=True)
    return build_scrubbed_env(home_dir=tmp_path / "home", extra_env={
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
    })


def _script(script: str, args: list[str], root: pathlib.Path, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(_SCRIPTS_DIR / script), *args], capture_output=True, text=True,
                          env=_script_env(root, tmp_path), cwd=str(tmp_path), timeout=120,
                          stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# 1. Each route calls its ops.read function and starts no process
# ---------------------------------------------------------------------------


def test_status_calls_get_status(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_status")

    body = client.get("/status", params={"project": _PROJECT}).json()

    assert calls == [(board.resolve(), (_PROJECT,))]
    assert body["exit_code"] == 0 and body["stderr"] == ""
    assert body["stdout"].startswith("pgai-kanban v7.0.0    RC: v1.0.0 (10d)    Last: v0.9.0\n")
    assert body["ship_posture"] == "matrix"
    assert no_subprocess == []


def test_test_report_calls_get_test_report_on_success_and_refusal(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_test_report")

    found = client.get("/test-report", params={"project": _PROJECT, "key": "v1.0.0"})
    missing = client.get("/test-report", params={"project": _PROJECT, "key": "v9.9.9"})

    assert [args for _root, args in calls] == [(_PROJECT, "v1.0.0"), (_PROJECT, "v9.9.9")]
    assert found.status_code == 200 and found.json()["stdout"] == f"# Report {_TESTER}\n"
    assert missing.status_code == 404 and missing.json()["exit_code"] == 3
    assert no_subprocess == []


def test_readme_calls_get_task_readme_on_success_and_refusal(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "get_task_readme")

    found = client.get(f"/projects/{_PROJECT}/tasks/CODER-20260101-003/readme")
    ledger = client.get(f"/projects/{_PROJECT}/tasks/BUG-0001/readme")

    assert [args for _root, args in calls] == [(_PROJECT, "CODER-20260101-003"), (_PROJECT, "BUG-0001")]
    assert found.json() == {"exit_code": 0, "stdout": f"# {_CODER}\n\n## Role\nCODER\n", "stderr": "", "warnings": []}
    assert ledger.status_code == 404
    assert no_subprocess == []


def test_rejected_calls_list_rejected_with_and_without_a_project(
    client: TestClient, board: pathlib.Path, no_subprocess: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, "list_rejected")

    every = client.get("/rejected").json()
    unknown = client.get("/rejected", params={"project": "nosuch"})

    assert [args for _root, args in calls] == [(None,), ("nosuch",)]
    assert every["stdout"].startswith(f"project={_PROJECT} dir=bugs\n  BUG-7-bad.md  (quarantined ")
    assert unknown.status_code == 500 and unknown.json()["exit_code"] == 1
    assert no_subprocess == []


# ---------------------------------------------------------------------------
# 2. The parity helper: route in-process, script through pp_run_ops
# ---------------------------------------------------------------------------


def _row(name: str, function: str, route: str, script: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, impl=f"module:pgai_agent_kanban.ops.read.{function}",
                                 rest_method="GET", rest_route=route, script=f"scripts/{script}")


@pytest.mark.parametrize(
    ("row", "params", "cli_args", "exit_code"),
    [
        (_row("status", "get_status", "/status", "kanban-status.sh"),
         {"project": _PROJECT}, ["--project", _PROJECT], 0),
        (_row("test-report", "get_test_report", "/test-report", "show-test-report.sh"),
         {"project": _PROJECT, "key": _TESTER}, ["--project", _PROJECT, "--key", _TESTER], 0),
        (_row("readme", "get_task_readme", f"/projects/{_PROJECT}/tasks/{_CODER}/readme", "show-readme.sh"),
         None, ["--project", _PROJECT, "--key", _CODER], 0),
        (_row("rejected", "list_rejected", "/rejected", "list-rejected.sh"),
         {"project": _PROJECT}, ["--project", _PROJECT], 0),
    ],
    ids=["status", "test-report", "readme", "rejected"],
)
def test_route_and_script_reach_the_same_function(
    row: types.SimpleNamespace, params: dict | None, cli_args: list[str], exit_code: int,
    board: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    cli_board = _build_board(tmp_path / "cli-board")
    scratch = tmp_path / "parity-scratch"
    scratch.mkdir()
    with build_api_client(board) as api:
        result = assert_route_cli_parity(
            row, api, params=params, cli_args=cli_args,
            env=_script_env(cli_board, tmp_path), scratch_dir=scratch,
        )

    assert result.response.status_code == 200
    assert result.response.json()["exit_code"] == exit_code
    assert result.cli is not None and result.cli.completed.returncode == exit_code


# ---------------------------------------------------------------------------
# 3. Envelope equals the script's output, refusal paths included
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "params", "script", "args"),
    [
        ("/status", {"project": _PROJECT}, "kanban-status.sh", ["--project", _PROJECT]),
        ("/status", {"project": "nosuch"}, "kanban-status.sh", ["--project", "nosuch"]),
        ("/test-report", {"project": _PROJECT, "key": "v1.0.0"}, "show-test-report.sh",
         ["--project", _PROJECT, "--key", "v1.0.0"]),
        ("/test-report", {"project": _PROJECT, "key": "TESTER-20260102-002"}, "show-test-report.sh",
         ["--project", _PROJECT, "--key", "TESTER-20260102-002"]),
        ("/test-report", {"project": _PROJECT, "key": "CODER-20260101-003"}, "show-test-report.sh",
         ["--project", _PROJECT, "--key", "CODER-20260101-003"]),
        ("/test-report", {"project": _PROJECT, "key": "TESTER-99999999-999"}, "show-test-report.sh",
         ["--project", _PROJECT, "--key", "TESTER-99999999-999"]),
        ("/test-report", {"project": "nosuch", "key": _TESTER}, "show-test-report.sh",
         ["--project", "nosuch", "--key", _TESTER]),
        ("/rejected", None, "list-rejected.sh", []),
        ("/rejected", {"project": "nosuch"}, "list-rejected.sh", ["--project", "nosuch"]),
    ],
    ids=["status", "status-unknown-project", "report-version", "report-ambiguous", "report-not-tester",
         "report-unknown-key", "report-unknown-project", "rejected", "rejected-unknown-project"],
)
def test_envelope_equals_the_script_output(
    client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path,
    route: str, params: dict | None, script: str, args: list[str],
) -> None:
    body = client.get(route, params=params).json()
    cli = _script(script, args, board, tmp_path)

    assert (body["exit_code"], body["stdout"], body["stderr"]) == (cli.returncode, cli.stdout, cli.stderr)


@pytest.mark.parametrize(
    "key",
    [_CODER, "TESTER-20260102-002", "BUG-0001", "CODER-99999999-999"],
    ids=["task", "ambiguous", "ledger", "unknown-key"],
)
def test_readme_route_matches_the_script(
    client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path, key: str
) -> None:
    resp = client.get(f"/projects/{_PROJECT}/tasks/{key}/readme")
    cli = _script("show-readme.sh", ["--project", _PROJECT, "--key", key], board, tmp_path)

    if cli.returncode == 0:
        assert resp.status_code == 200
        assert resp.json()["stdout"] == cli.stdout
    else:
        assert resp.status_code == 404
        assert resp.json() == {"error": "task readme not found", "project": _PROJECT, "key": key,
                               "detail": cli.stderr.strip()}


# ---------------------------------------------------------------------------
# 4. no_color
# ---------------------------------------------------------------------------


def test_status_no_color_renders_plain_text_under_a_colour_terminal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TERM", "xterm")

    coloured = client.get("/status", params={"project": _PROJECT}).json()["stdout"]
    plain = client.get("/status", params={"project": _PROJECT, "no_color": "true"}).json()["stdout"]

    assert "\033[" in coloured
    assert "\033[" not in plain and plain.startswith("pgai-kanban v7.0.0")


# ---------------------------------------------------------------------------
# 5. Ship posture: the route field and the script line agree
# ---------------------------------------------------------------------------

_POSTURE_PROJECTS: dict[str, tuple[str, str]] = {
    # project -> (project.cfg text, the posture it resolves to)
    "posture-always": (
        "[project]\npush_to_remote = false\n\n[release]\nship_posture = always\n", "always"),
    "posture-absent": ("[project]\npush_to_remote = false\n", "matrix"),
    "posture-remote-unconfirmed": (
        "[project]\npush_to_remote = true\n\n[release]\nship_posture = always\n", "matrix"),
}


def _add_posture_projects(root: pathlib.Path) -> None:
    """Register the three posture projects on *root*, each with its project.cfg."""
    registry = root / "projects.cfg"
    entries = "".join(
        f"[project:{name}]\npriority = {index}\n" for index, name in enumerate(_POSTURE_PROJECTS, start=2)
    )
    registry.write_text(registry.read_text(encoding="utf-8") + entries, encoding="utf-8")
    for name, (cfg_text, _expected) in _POSTURE_PROJECTS.items():
        _write(root / "projects" / name / "project.cfg", cfg_text)
        _write(root / "projects" / name / "tasks" / f"CODER-20260101-001-{name}" / "status.md",
               "## State\nDONE\n")


@pytest.mark.parametrize("project", list(_POSTURE_PROJECTS), ids=list(_POSTURE_PROJECTS))
def test_status_route_and_script_show_the_same_ship_posture(
    client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path, project: str
) -> None:
    from pgai_agent_kanban.overwatch.resolution_matrix import resolve_ship_posture

    _add_posture_projects(board)
    expected = _POSTURE_PROJECTS[project][1]

    body = client.get("/status", params={"project": project, "no_color": "true"}).json()
    cli = _script("kanban-status.sh", ["--project", project, "--no-color"], board, tmp_path)

    assert resolve_ship_posture(project, str(board), stream=io.StringIO()) == expected
    assert body["ship_posture"] == expected
    assert cli.returncode == 0 and cli.stdout.endswith(f"\nShip posture: {expected}\n")
    assert (body["exit_code"], body["stdout"], body["stderr"]) == (cli.returncode, cli.stdout, cli.stderr)
    if project == "posture-remote-unconfirmed":
        assert "not confirmed for a push_to_remote = true lane" in cli.stderr
    else:
        assert cli.stderr == ""

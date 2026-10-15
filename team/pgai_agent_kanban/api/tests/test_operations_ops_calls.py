"""
test_operations_ops_calls.py — the action routes whose CLI script reaches
an ``ops.write`` function through ``pp_run_ops`` call that same function in
process, through the one ops-call adapter, and start no subprocess.

Routes and the function each calls (the manifest's ``impl`` column):

  POST /operations/halt           ops.write.halt
  POST /operations/unhalt         ops.write.unhalt
  POST /operations/halt-global    ops.write.halt_global
  POST /operations/unhalt-global  ops.write.unhalt_global
  POST /operations/halt-after     ops.write.halt_after
  POST /operations/defer          ops.write.defer
  POST /operations/undefer        ops.write.undefer
  POST /operations/set-version-ceiling  ops.write.set_version_ceiling
  POST /operations/switch-provider      ops.write.switch_provider
  POST /operations/intake         ops.write.deposit_intake
  POST /operations/close          ops.write.close_item
  POST /operations/wontdo         ops.write.wontdo_item
  POST /operations/delete         ops.write.delete_item
  POST /operations/reset          ops.write.reset_item

What is proven here:

  1. Each route calls its function with the arguments its CLI script would
     pass, while every way of starting a process (``subprocess.*``,
     ``os.system``, the adapter's ``shell_out``, the router's detached
     spawner) is patched to fail the test.
  2. On success each route's ``stdout`` is the narration its CLI script
     prints (compared against the real script on a twin board, board paths
     normalised), so the envelope a caller reads is unchanged.
  3. An ``OpsError`` raised by the real function, for one refusal per verb
     family (halt family, intake, the status family, reset), reaches the
     caller as the same seven-key dual-channel envelope at HTTP 500 the shell
     path produced, with the CLI's exit code and stderr.
  4. ``adapter.ops_call`` maps every ``OpsError`` subtype to the exit code
     the ops CLI uses, captures what the call prints, honours an exit-code
     override, and lets a non-``OpsError`` exception through.

Every board is a throwaway directory under ``tmp_path``; the API is rooted at
it (``ApiConfig.kanban_root``) and the CLI scripts are pointed at it through
``PGAI_AGENT_KANBAN_ROOT_PATH`` / ``KANBAN_ROOT`` with ``PGAI_PROJECT_NAME``
cleared.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import Callable, Generator, NamedTuple, Optional

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api import adapter as adapter_module
from pgai_agent_kanban.api.adapter import ops_call
from pgai_agent_kanban.api.routers import operations as operations_module
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.errors import (
    Ambiguous,
    IoError,
    MountPinned,
    NotFound,
    OpsError,
    Refused,
)

# This file lives at team/pgai_agent_kanban/api/tests/; four levels up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_PROJECT = "proj"
_BACKLOG_TASK = "CODER-20260101-001-alpha"
_WORKING_TASK = "CODER-20260101-002-beta"
_DONE_TASK = "CODER-20260101-003-gamma"
_INTAKE_NAME = "BUG-0099-ops-call-fixture.md"
_INTAKE_CONTENT = "# BUG-0099\n\n## Status\nopen\n\n## Symptom\nFixture payload.\n"
_ENVELOPE_REFUSAL_KEYS = {"exit_code", "stdout", "stderr", "error", "message", "remedy", "warnings"}


# ---------------------------------------------------------------------------
# Boards, clients, CLI runs
# ---------------------------------------------------------------------------


def _status_md(task_id: str, state: str) -> str:
    return (
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nnone\n\n"
        "## Artifacts\nnone\n\n## Blockers\nnone\n\n## Needs Human\nno\n"
    )


def _build_board(root: pathlib.Path) -> pathlib.Path:
    """A kanban root with one registered project, three tasks and the intake dirs."""
    root.mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    project = root / "projects" / _PROJECT
    queues = project / "tasks" / "queues"
    queues.mkdir(parents=True)
    tasks = {_BACKLOG_TASK: "BACKLOG", _WORKING_TASK: "WORKING", _DONE_TASK: "DONE"}
    markers = {"BACKLOG": " ", "WORKING": "A", "DONE": "x"}
    (queues / "coder_backlog.md").write_text(
        "# CODER Backlog\n\n" + "".join(f"- [{markers[s]}] {t}\n" for t, s in tasks.items()),
        encoding="utf-8",
    )
    for task_id, state in tasks.items():
        task_dir = project / "tasks" / task_id
        (task_dir / "artifacts").mkdir(parents=True)
        (task_dir / "logs").mkdir()
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n## Owner\nClaude\n\n## Role\nCODER\n", encoding="utf-8"
        )
        (task_dir / "status.md").write_text(_status_md(task_id, state), encoding="utf-8")
    for sub in ("bugs", "priority", "requirements"):
        (project / sub).mkdir()
    (root / "logs").mkdir()
    return root


@pytest.fixture
def board(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = _build_board(tmp_path / "rest_board")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    return root


@pytest.fixture
def client(board: pathlib.Path) -> Generator[TestClient, None, None]:
    """A TestClient whose API kanban_root is the REST board."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=board)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as c:
        yield c


def _cli_env(root: pathlib.Path, temp_dir: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        env.pop(name, None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_dir)
    return env


def _run_cli(script: str, args: list[str], root: pathlib.Path, temp_dir: pathlib.Path) -> subprocess.CompletedProcess:
    """Run the real CLI script against *root* (explicit cwd, closed stdin)."""
    return subprocess.run(
        ["bash", str(_SCRIPTS_DIR / script), *args],
        capture_output=True,
        text=True,
        env=_cli_env(root, temp_dir),
        cwd=str(_TEAM_DIR),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _normalise(text: str, root: pathlib.Path) -> str:
    """Replace a board's own path (as given and resolved) with a stable token."""
    for form in sorted({str(root.resolve()), str(root)}, key=len, reverse=True):
        text = text.replace(form, "<board>")
    return text


class _ProcessStarted(AssertionError):
    """Raised by every patched process starter."""


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every way a route could start a process fail the test.

    Returns the list the patched starters append their name to before
    raising, so a test can also assert it stayed empty.
    """
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
    monkeypatch.setattr(operations_module, "shell_out", _forbid("operations.shell_out"))
    monkeypatch.setattr(operations_module, "_spawn_detached", _forbid("operations._spawn_detached"))
    return started


# ---------------------------------------------------------------------------
# 1. Each route calls its ops.write function and starts no process
# ---------------------------------------------------------------------------


class _CallCase(NamedTuple):
    route: str
    function: str
    body: dict
    seed: Optional[Callable[[pathlib.Path], None]]
    expected_args: Callable[[pathlib.Path], tuple]
    expected_kwargs: dict


def _seed_project_halt(root: pathlib.Path) -> None:
    (root / "projects" / _PROJECT / "HALT").touch()


def _seed_root_halt(root: pathlib.Path) -> None:
    (root / "HALT").touch()


def _campaign_file(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT / "DEFER-LOW-BUGS"


def _seed_campaign(root: pathlib.Path) -> None:
    _campaign_file(root).write_text("until=v1.2.3\n", encoding="utf-8")


_CALL_CASES = [
    _CallCase("/operations/halt", "halt", {"project": _PROJECT}, None,
              lambda root: (_PROJECT,), {}),
    _CallCase("/operations/unhalt", "unhalt", {"project": _PROJECT}, _seed_project_halt,
              lambda root: (_PROJECT,), {}),
    _CallCase("/operations/halt-global", "halt_global", {}, None,
              lambda root: (), {}),
    _CallCase("/operations/unhalt-global", "unhalt_global", {}, _seed_root_halt,
              lambda root: (), {}),
    _CallCase("/operations/halt-after", "halt_after", {"project": _PROJECT, "until": "v1.2.3"}, None,
              lambda root: (_PROJECT, "rc:v1.2.3"), {}),
    _CallCase("/operations/defer", "defer", {"project": _PROJECT, "until": "1.2.3"}, None,
              lambda root: (_PROJECT, "1.2.3"), {}),
    _CallCase("/operations/undefer", "undefer", {"project": _PROJECT}, _seed_campaign,
              lambda root: (_PROJECT,), {}),
    _CallCase("/operations/set-version-ceiling", "set_version_ceiling",
              {"project": _PROJECT, "minor": 5, "show": True}, None,
              lambda root: (_PROJECT,),
              {"show": True, "minor": 5, "major": None, "no_minor": False, "no_major": False}),
    _CallCase("/operations/switch-provider", "switch_provider", {"provider": "claude"}, None,
              lambda root: ("claude",), {}),
    _CallCase("/operations/intake", "deposit_intake",
              {"project": _PROJECT, "filename": _INTAKE_NAME, "content": _INTAKE_CONTENT}, None,
              None, {}),  # the source path is checked by content below
    _CallCase("/operations/close", "close_item",
              {"project": _PROJECT, "key": _BACKLOG_TASK, "state": "wont-do", "note": "dup"}, None,
              lambda root: (_PROJECT, _BACKLOG_TASK), {"state": "wont-do", "note": "dup"}),
    _CallCase("/operations/wontdo", "wontdo_item", {"project": _PROJECT, "key": _BACKLOG_TASK}, None,
              lambda root: (_PROJECT, _BACKLOG_TASK), {}),
    _CallCase("/operations/delete", "delete_item",
              {"project": _PROJECT, "key": _BACKLOG_TASK, "force": True}, None,
              lambda root: (_PROJECT, _BACKLOG_TASK), {"force": True}),
    _CallCase("/operations/reset", "reset_item",
              {"project": _PROJECT, "key": _BACKLOG_TASK, "keep_artifacts": True}, None,
              lambda root: (_PROJECT, _BACKLOG_TASK), {"keep_artifacts": True, "force": False}),
]


@pytest.mark.parametrize("case", _CALL_CASES, ids=[c.function for c in _CALL_CASES])
def test_route_calls_its_ops_write_function_and_starts_no_process(
    case: _CallCase,
    client: TestClient,
    board: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    no_subprocess: list[str],
) -> None:
    if case.seed is not None:
        case.seed(board)
    calls: list[tuple[tuple, dict]] = []
    deposited = board / "projects" / _PROJECT / "bugs" / _INTAKE_NAME

    def _recorder(*args, **kwargs):
        calls.append((args, kwargs))
        if case.function == "deposit_intake":
            source = pathlib.Path(args[2])
            assert source.read_text(encoding="utf-8") == _INTAKE_CONTENT, "the deposited source is not the body content"
            assert source.name == _INTAKE_NAME
            return deposited
        if case.function in ("defer", "undefer"):
            until = "v1.2.3" if case.function == "defer" else ""
            return ops_write.CampaignChange(case.function, _PROJECT, _campaign_file(board), until, True)
        if case.function == "set_version_ceiling":
            cfg = board / "projects" / _PROJECT / "project.cfg"
            return ops_write.CeilingReport(_PROJECT, cfg, ("max_minor=5", "max_major=0 (default)"), True)
        if case.function == "switch_provider":
            return ops_write.ProviderSwitch("codex", "claude", "claude", board / "kanban.cfg", True)
        return None

    monkeypatch.setattr(ops_write, case.function, _recorder)

    resp = client.post(case.route, json=case.body)

    assert no_subprocess == [], f"{case.route} started a process: {no_subprocess}"
    assert resp.status_code == 200, resp.json()
    assert resp.json()["exit_code"] == 0, resp.json()
    assert len(calls) == 1, f"{case.route} called ops.write.{case.function} {len(calls)} time(s)"
    args, kwargs = calls[0]
    ctx = args[0]
    assert ctx.kanban_root == board.resolve(), "the call must act on the API's own kanban root"
    if case.expected_args is not None:
        assert args[1:] == case.expected_args(board), args
        assert kwargs == case.expected_kwargs, kwargs
    else:
        assert args[1] == _PROJECT
    if case.function == "deposit_intake":
        assert resp.json()["stdout"] == f"intake: deposited {deposited}\n"
    if case.function == "set_version_ceiling":
        assert resp.json()["stdout"] == "max_minor=5\nmax_major=0 (default)\n"


def test_every_converted_route_is_covered_by_a_call_case() -> None:
    from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

    module_write_routes = {
        verb.rest_route: verb.impl.rsplit(".", 1)[-1]
        for verb in load_manifest().values()
        if verb.impl.startswith("module:pgai_agent_kanban.ops.write.")
    }
    covered = {case.route: case.function for case in _CALL_CASES}
    assert covered == module_write_routes


# ---------------------------------------------------------------------------
# 2. Success narration is the CLI's own
# ---------------------------------------------------------------------------


class _NarrationCase(NamedTuple):
    route: str
    script: str
    body: dict
    cli_args: Callable[[pathlib.Path], list[str]]
    seed: Optional[Callable[[pathlib.Path], None]]


def _intake_source(root: pathlib.Path) -> pathlib.Path:
    source = root.parent / f"{root.name}_staging" / _INTAKE_NAME
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(_INTAKE_CONTENT, encoding="utf-8")
    return source


_NARRATION_CASES = [
    _NarrationCase("/operations/halt", "halt.sh", {"project": _PROJECT},
                   lambda root: ["--project", _PROJECT], None),
    _NarrationCase("/operations/halt", "halt.sh", {"project": _PROJECT},
                   lambda root: ["--project", _PROJECT], _seed_project_halt),
    _NarrationCase("/operations/unhalt", "unhalt.sh", {"project": _PROJECT},
                   lambda root: ["--project", _PROJECT], _seed_project_halt),
    _NarrationCase("/operations/unhalt", "unhalt.sh", {"project": _PROJECT},
                   lambda root: ["--project", _PROJECT], None),
    _NarrationCase("/operations/halt-global", "halt-global.sh", {}, lambda root: [], None),
    _NarrationCase("/operations/unhalt-global", "unhalt-global.sh", {}, lambda root: [], _seed_root_halt),
    _NarrationCase("/operations/halt-after", "halt-after.sh", {"project": _PROJECT, "until": "v9.8.7"},
                   lambda root: ["--project", _PROJECT, "--until", "v9.8.7"], None),
    _NarrationCase("/operations/halt-after", "halt-after.sh", {"project": _PROJECT, "key": "coder"},
                   lambda root: ["--project", _PROJECT, "--key", "coder"], None),
    _NarrationCase("/operations/defer", "defer.sh", {"project": _PROJECT, "until": "v1.2.3"},
                   lambda root: ["--project", _PROJECT, "--until", "v1.2.3"], None),
    _NarrationCase("/operations/defer", "defer.sh", {"project": _PROJECT, "until": "v1.2.3"},
                   lambda root: ["--project", _PROJECT, "--until", "v1.2.3"], _seed_campaign),
    _NarrationCase("/operations/undefer", "undefer.sh", {"project": _PROJECT},
                   lambda root: ["--project", _PROJECT], _seed_campaign),
    _NarrationCase("/operations/undefer", "undefer.sh", {"project": _PROJECT},
                   lambda root: ["--project", _PROJECT], None),
    _NarrationCase("/operations/intake", "intake.sh",
                   {"project": _PROJECT, "filename": _INTAKE_NAME, "content": _INTAKE_CONTENT},
                   lambda root: ["--project", _PROJECT, str(_intake_source(root))], None),
    _NarrationCase("/operations/close", "close.sh", {"project": _PROJECT, "key": _BACKLOG_TASK},
                   lambda root: ["--project", _PROJECT, "--key", _BACKLOG_TASK], None),
    _NarrationCase("/operations/wontdo", "wontdo.sh", {"project": _PROJECT, "key": _BACKLOG_TASK},
                   lambda root: ["--project", _PROJECT, "--key", _BACKLOG_TASK], None),
    _NarrationCase("/operations/delete", "delete.sh", {"project": _PROJECT, "key": _DONE_TASK},
                   lambda root: ["--project", _PROJECT, "--key", _DONE_TASK], None),
    _NarrationCase("/operations/reset", "reset.sh", {"project": _PROJECT, "key": _DONE_TASK},
                   lambda root: ["--project", _PROJECT, "--key", _DONE_TASK], None),
]


def _narration_id(case: _NarrationCase) -> str:
    seeded = "-seeded" if case.seed is not None else ""
    extra = "-" + "-".join(k for k in case.body if k not in ("project",)) if len(case.body) > 1 else ""
    return f"{case.route.rsplit('/', 1)[-1]}{extra}{seeded}"


@pytest.mark.parametrize("case", _NARRATION_CASES, ids=[_narration_id(c) for c in _NARRATION_CASES])
def test_success_stdout_and_effect_match_the_cli_script(
    case: _NarrationCase, client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    cli_board = _build_board(tmp_path / "cli_board")
    if case.seed is not None:
        case.seed(cli_board)
        case.seed(board)

    cli = _run_cli(case.script, case.cli_args(cli_board), cli_board, tmp_path / "cli_tmp")
    assert cli.returncode == 0, f"{case.script} failed:\n{cli.stdout}{cli.stderr}"

    resp = client.post(case.route, json=case.body)
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert set(body) == {"exit_code", "stdout", "stderr", "warnings"}, body
    assert body["exit_code"] == 0
    assert _normalise(body["stdout"], board) == _normalise(cli.stdout, cli_board)
    assert _normalise(body["stderr"], board) == _normalise(cli.stderr, cli_board)


# ---------------------------------------------------------------------------
# 3. An OpsError from the real function reaches the caller as the shell
#    path's refusal envelope -- one refusal per verb family
# ---------------------------------------------------------------------------


_CLI_REMEDY_PREFIX = "Fix the condition the message names, then re-run the CLI form: team/scripts/"

# An unroutable intake name is refused before the temp file is written, so
# its remedy re-sends the request under a routable name rather than naming a
# temp file the CLI form would need.
_INTAKE_RESEND_PREFIX = "Nothing was written. Re-send the request with a filename that routes, for example: curl "


class _RefusalCase(NamedTuple):
    family: str
    route: str
    script: str
    body: dict
    cli_args: Callable[[pathlib.Path], list[str]]
    seed: Optional[Callable[[pathlib.Path], None]]
    fallback_key: str
    remedy_prefix: str = _CLI_REMEDY_PREFIX
    #: The HTTP status the refusal maps to (adapter.http_status_for): 409 for a
    #: guard's Refused, 500 for a failure the ops layer does not classify.
    http_status: int = 500


def _remove_project_dir(root: pathlib.Path) -> None:
    import shutil

    shutil.rmtree(root / "projects" / _PROJECT)


_REFUSAL_CASES = [
    # halt family: the project is registered but its directory is gone, so
    # ops.write.halt raises OpsError and the wrapper adds its failure line.
    _RefusalCase("halt", "/operations/halt", "halt.sh", {"project": _PROJECT},
                 lambda root: ["--project", _PROJECT], _remove_project_dir, "halt.refused"),
    # intake: an unroutable file name is a Refused from deposit_intake (exit 2).
    _RefusalCase("intake", "/operations/intake", "intake.sh",
                 {"project": _PROJECT, "filename": "notes.md", "content": "x\n"},
                 lambda root: ["--project", _PROJECT, str(_unroutable_source(root))], None, "intake.refused",
                 _INTAKE_RESEND_PREFIX, 409),
    # status family: deleting a non-terminal task without force is the guard's Refused (exit 2).
    _RefusalCase("status", "/operations/delete", "delete.sh", {"project": _PROJECT, "key": _BACKLOG_TASK},
                 lambda root: ["--project", _PROJECT, "--key", _BACKLOG_TASK], None, "delete.refused",
                 http_status=409),
    # reset: a WORKING task is refused (exit 2).
    _RefusalCase("reset", "/operations/reset", "reset.sh", {"project": _PROJECT, "key": _WORKING_TASK},
                 lambda root: ["--project", _PROJECT, "--key", _WORKING_TASK], None, "reset.refused",
                 http_status=409),
]


def _unroutable_source(root: pathlib.Path) -> pathlib.Path:
    source = root.parent / f"{root.name}_staging" / "notes.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("x\n", encoding="utf-8")
    return source


@pytest.mark.parametrize("case", _REFUSAL_CASES, ids=[c.family for c in _REFUSAL_CASES])
def test_ops_error_reaches_the_caller_as_the_shell_paths_dual_channel_envelope(
    case: _RefusalCase, client: TestClient, board: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    cli_board = _build_board(tmp_path / "cli_board")
    if case.seed is not None:
        case.seed(cli_board)
        case.seed(board)

    cli = _run_cli(case.script, case.cli_args(cli_board), cli_board, tmp_path / "cli_tmp")
    assert cli.returncode != 0, f"{case.script} accepted the refusal case:\n{cli.stdout}"

    resp = client.post(case.route, json=case.body)
    body = resp.json()
    assert resp.status_code == case.http_status, body
    assert set(body) == _ENVELOPE_REFUSAL_KEYS, body
    assert body["exit_code"] == cli.returncode, (body, cli.stderr)
    assert _normalise(body["stderr"], board) == _normalise(cli.stderr, cli_board)
    assert body["stdout"] == cli.stdout
    assert body["error"] == case.fallback_key
    assert _normalise(body["message"], board) == _normalise(cli.stderr.strip(), cli_board)
    assert body["remedy"].startswith(case.remedy_prefix), body["remedy"]


@pytest.mark.parametrize(
    "route, script, body, cli_args, http_status",
    [
        pytest.param(
            "/operations/close", "close.sh",
            {"project": _PROJECT, "key": _BACKLOG_TASK, "state": "bogus"},
            ["--project", _PROJECT, "--key", _BACKLOG_TASK, "--state", "bogus"],
            422,
            id="close-invalid-state",
        ),
        pytest.param(
            "/operations/close", "close.sh",
            {"project": _PROJECT, "key": _BACKLOG_TASK, "state": "superseded"},
            ["--project", _PROJECT, "--key", _BACKLOG_TASK, "--state", "superseded"],
            422,
            id="close-retired-superseded-state",
        ),
        pytest.param(
            "/operations/halt-after", "halt-after.sh",
            {"project": _PROJECT, "key": "coder", "until": "v1.2.3"},
            ["--project", _PROJECT, "--key", "coder", "--until", "v1.2.3"],
            500,
            id="halt-after-until-with-role-scope",
        ),
        pytest.param(
            "/operations/halt-after", "halt-after.sh",
            {"project": _PROJECT, "until": "1.2"},
            ["--project", _PROJECT, "--until", "1.2"],
            422,
            id="halt-after-malformed-until",
        ),
        pytest.param(
            "/operations/defer", "defer.sh",
            {"project": "ghost", "until": "1.2"},
            ["--project", "ghost", "--until", "1.2"],
            422,
            id="defer-malformed-until-before-project",
        ),
        pytest.param(
            "/operations/undefer", "undefer.sh",
            {"project": "ghost"},
            ["--project", "ghost"],
            404,
            id="undefer-unknown-project",
        ),
        pytest.param(
            "/operations/wontdo", "wontdo.sh",
            {"project": _PROJECT, "key": "CODER-20260101-00"},
            ["--project", _PROJECT, "--key", "CODER-20260101-00"],
            404,
            id="wontdo-unknown-key",
        ),
    ],
)
def test_wrapper_level_refusal_matches_the_cli_script(
    route: str,
    script: str,
    body: dict,
    cli_args: list[str],
    http_status: int,
    client: TestClient,
    board: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A refusal the CLI wrapper adds around the ops call (its own pre-check
    or failure line) reaches an HTTP caller with the same exit code and
    stderr, at the HTTP status its error key or ops exception names."""
    cli_board = _build_board(tmp_path / "cli_board")
    cli = _run_cli(script, cli_args, cli_board, tmp_path / "cli_tmp")
    assert cli.returncode != 0, f"{script} accepted {cli_args}:\n{cli.stdout}"

    resp = client.post(route, json=body)
    payload = resp.json()
    assert resp.status_code == http_status, payload
    assert set(payload) == _ENVELOPE_REFUSAL_KEYS, payload
    assert payload["exit_code"] == cli.returncode
    assert _normalise(payload["stderr"], board) == _normalise(cli.stderr, cli_board)
    assert payload["error"] and payload["message"] and payload["remedy"], payload


# ---------------------------------------------------------------------------
# 4. The ops-call adapter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc, exit_code, http_status",
    [
        pytest.param(Ambiguous("two matches"), 2, 409, id="ambiguous"),
        pytest.param(NotFound("no match"), 3, 404, id="not-found"),
        pytest.param(Refused("guard said no"), 2, 409, id="refused"),
        pytest.param(MountPinned("mount in the way"), 4, 500, id="mount-pinned"),
        pytest.param(IoError("disk said no"), 4, 500, id="io-error"),
        pytest.param(OpsError("target already exists (no clobber): /x"), 3, 500, id="no-clobber"),
        pytest.param(OpsError("plain failure"), 1, 500, id="ops-error"),
    ],
)
def test_route_maps_each_ops_error_to_the_ops_cli_exit_code_and_its_http_status(
    exc: OpsError,
    exit_code: int,
    http_status: int,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    no_subprocess: list[str],
) -> None:
    def _raise(*args, **kwargs):
        print("narrated before failing", file=sys.stderr)
        raise exc

    monkeypatch.setattr(ops_write, "delete_item", _raise)
    resp = client.post("/operations/delete", json={"project": _PROJECT, "key": _BACKLOG_TASK})

    body = resp.json()
    assert resp.status_code == http_status, body
    assert set(body) == _ENVELOPE_REFUSAL_KEYS, body
    assert body["exit_code"] == exit_code
    assert body["stderr"] == f"narrated before failing\n{exc}\n"
    assert body["error"] == "delete.refused"
    assert body["message"] == f"narrated before failing\n{exc}"
    assert no_subprocess == []


def test_ops_call_records_output_and_suppresses_only_ops_errors() -> None:
    with ops_call() as clean:
        print("to stdout")
        print("to stderr", file=sys.stderr)
    assert (clean.exit_code, clean.stdout, clean.stderr, clean.error) == (0, "to stdout\n", "to stderr\n", None)
    assert clean.ok

    refusal = Refused("no")
    with ops_call() as refused:
        raise refusal
    assert (refused.exit_code, refused.stderr, refused.error) == (2, "no\n", refusal)
    assert not refused.ok

    with ops_call(exit_code=1) as overridden:
        raise NotFound("missing")
    assert overridden.exit_code == 1

    stdout_before, stderr_before = sys.stdout, sys.stderr
    with pytest.raises(ValueError):
        with ops_call():
            raise ValueError("a server fault is not a refusal")
    assert (sys.stdout, sys.stderr) == (stdout_before, stderr_before), (
        "the captured streams must be restored when a non-OpsError propagates"
    )

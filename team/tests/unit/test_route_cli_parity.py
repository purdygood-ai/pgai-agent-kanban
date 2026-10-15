"""
test_route_cli_parity.py — Unit tests for team/tests/fixtures/route_cli_parity.py.

The REST half is exercised against a small FastAPI app whose fake routes
call a fake module function in-process, start a process, swallow a
forbidden process start, or never call the function. The CLI half is
exercised against fixture scripts under a throwaway ``team/scripts/`` that
source the real ``pp_run_ops.sh``. The last test runs both halves against
the real ``halt`` row: the production route and halt.sh, each on its own
synthetic board.

Coverage:

  1. A fake route calling the function in-process passes; one that shells
     out fails; one that swallows the forbidden start still fails; one that
     never calls the function fails; process execution is restored after.
  2. A row whose impl is shell is refused.
  3. A script that bypasses pp_run_ops fails the source check and, when
     run, is never seen reaching the function; a pp_run_ops call on a path
     the run never takes passes the source check and fails the run; a
     script that calls pp_run_ops passes both.
  4. The real halt row passes both halves end to end, and each surface
     arms HALT on its own board.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board
from tests.fixtures.route_cli_parity import (
    assert_cli_reaches_function,
    assert_route_cli_parity,
    assert_route_reaches_function,
    build_api_client,
    parity_target,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"

_FAKE_MODULE = "route_cli_parity_fake_ops"
_HALT = "module:pgai_agent_kanban.ops.write.halt"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _row(route: str = "/fake/in-process", impl: str = f"module:{_FAKE_MODULE}.do_work",
         script: str = "scripts/fake.sh", name: str = "fake-verb") -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, impl=impl, rest_method="POST", rest_route=route, script=script)


@pytest.fixture
def fake_client(monkeypatch):
    """A TestClient over fake routes, and the fake module whose function they call."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    fake_ops = types.ModuleType(_FAKE_MODULE)

    def do_work(value: str) -> str:
        return f"worked:{value}"

    fake_ops.do_work = do_work
    monkeypatch.setitem(sys.modules, _FAKE_MODULE, fake_ops)

    app = FastAPI()

    @app.post("/fake/in-process")
    def in_process():
        return {"result": fake_ops.do_work("x")}

    @app.post("/fake/shells-out")
    def shells_out():
        fake_ops.do_work("x")
        subprocess.run(["true"], check=False)
        return {}

    @app.post("/fake/swallows")
    def swallows():
        try:
            subprocess.run(["true"], check=False)
        except Exception:  # noqa: BLE001 -- the swallow is what the test needs
            pass
        return {"result": fake_ops.do_work("x")}

    @app.post("/fake/no-call")
    def no_call():
        return {}

    return TestClient(app, raise_server_exceptions=True)


def _fake_team(tmp_path: Path, body: str) -> Path:
    team = tmp_path / "team"
    (team / "scripts").mkdir(parents=True)
    (team / "scripts" / "fake.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + f'source "{_PP_RUN_OPS}"\n' + textwrap.dedent(body),
        encoding="utf-8",
    )
    return team


def _project_root(tmp_path: Path) -> Path:
    """A project directory in the <kanban_root>/projects/<project>/ layout ops expects."""
    root = tmp_path / "board" / "projects" / "demo"
    root.mkdir(parents=True)
    return root


def _cli_env(tmp_path: Path, board: Path) -> tuple[dict, Path]:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(board),
            "KANBAN_ROOT": str(board),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        },
    )
    return env, scratch


# ---------------------------------------------------------------------------
# 1. REST half
# ---------------------------------------------------------------------------


def test_fake_route_calling_the_function_in_process_passes(fake_client):
    response = assert_route_reaches_function(fake_client, _row("/fake/in-process"))
    assert response.status_code == 200
    assert response.json() == {"result": "worked:x"}


def test_fake_route_that_shells_out_fails(fake_client):
    with pytest.raises(AssertionError, match=r"started a process.*\n  - subprocess\.Popen: true"):
        assert_route_reaches_function(fake_client, _row("/fake/shells-out"))


def test_fake_route_that_swallows_the_forbidden_start_still_fails(fake_client):
    with pytest.raises(AssertionError, match="started a process"):
        assert_route_reaches_function(fake_client, _row("/fake/swallows"))


def test_fake_route_not_calling_the_function_fails(fake_client):
    with pytest.raises(AssertionError, match=rf"never called {_FAKE_MODULE}\.do_work \(HTTP 200"):
        assert_route_reaches_function(fake_client, _row("/fake/no-call"))


def test_process_execution_and_the_function_are_restored_afterwards(fake_client):
    original = sys.modules[_FAKE_MODULE].do_work
    with pytest.raises(AssertionError):
        assert_route_reaches_function(fake_client, _row("/fake/shells-out"))
    assert sys.modules[_FAKE_MODULE].do_work is original
    assert subprocess.run(["true"], check=False).returncode == 0


# ---------------------------------------------------------------------------
# 2. Rows the helper does not apply to
# ---------------------------------------------------------------------------


def test_shell_row_is_refused():
    with pytest.raises(AssertionError, match="verb 'wake-now' has impl 'shell'"):
        parity_target("wake-now")


# ---------------------------------------------------------------------------
# 3. CLI half
# ---------------------------------------------------------------------------


def test_script_bypassing_pp_run_ops_fails_the_source_check_and_the_run(tmp_path):
    team = _fake_team(tmp_path, 'python3 -m pgai_agent_kanban.ops halt "$1"\n')
    target_root = _project_root(tmp_path)
    env, scratch = _cli_env(tmp_path, tmp_path / "board")
    with pytest.raises(AssertionError) as excinfo:
        assert_cli_reaches_function(
            _row(impl=_HALT), cli_args=[str(target_root)], env=env, scratch_dir=scratch, team_dir=team
        )
    message = str(excinfo.value)
    assert "has no 'pp_run_ops pgai_agent_kanban.ops.write halt' call" in message
    assert "never reached pgai_agent_kanban.ops.write.halt through pp_run_ops" in message
    assert not (target_root / "HALT").exists()


def test_pp_run_ops_call_the_run_never_takes_fails_the_run(tmp_path):
    team = _fake_team(
        tmp_path,
        """\
        if [[ "${NEVER_SET:-}" == "yes" ]]; then
            pp_run_ops pgai_agent_kanban.ops halt "$1"
        fi
        """,
    )
    env, scratch = _cli_env(tmp_path, tmp_path / "board")
    row = _row(impl=_HALT)
    assert assert_cli_reaches_function(row, team_dir=team) is None
    with pytest.raises(AssertionError, match=r"exited 0 and never reached pgai_agent_kanban\.ops\.write\.halt"):
        assert_cli_reaches_function(row, cli_args=[str(tmp_path)], env=env, scratch_dir=scratch, team_dir=team)


def test_script_calling_pp_run_ops_passes_and_the_call_is_recorded(tmp_path):
    team = _fake_team(tmp_path, 'pp_run_ops pgai_agent_kanban.ops halt "$1"\n')
    target_root = _project_root(tmp_path)
    env, scratch = _cli_env(tmp_path, tmp_path / "board")
    run = assert_cli_reaches_function(
        _row(impl=_HALT), cli_args=[str(target_root)], env=env, scratch_dir=scratch, team_dir=team
    )
    assert run is not None
    assert run.completed.returncode == 0, run.completed.stderr
    assert len(run.calls) == 1
    assert run.calls[0].startswith("pgai_agent_kanban.ops.write.halt pid=")
    assert (target_root / "HALT").is_file()


# ---------------------------------------------------------------------------
# 4. The real halt row
# ---------------------------------------------------------------------------


def test_halt_row_passes_both_halves_end_to_end(tmp_path):
    rest_board = tmp_path / "rest-board"
    rest_project = build_synthetic_board(rest_board)
    cli_board = tmp_path / "cli-board"
    cli_project = build_synthetic_board(cli_board)
    env, scratch = _cli_env(tmp_path, cli_board)

    result = assert_route_cli_parity(
        "halt",
        build_api_client(rest_board),
        json={"project": PROJECT_NAME},
        cli_args=["--project", PROJECT_NAME],
        env=env,
        scratch_dir=scratch,
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.write.halt"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None
    assert result.cli.completed.returncode == 0, result.cli.completed.stderr
    assert result.cli.calls
    assert (rest_project / "HALT").is_file()
    assert (cli_project / "HALT").is_file()

"""
test_api_refusal_status_temp_root_and_timeout.py
================================================
The operator API answers a refusal with the status that names it, resolves
its temp root and kanban root one way for every route, and never waits on a
hung script without bound.

  (1) Temp root: with PGAI_AGENT_KANBAN_TEMP_DIR unset and kanban.cfg
      ``[paths] tmp_root`` / ``tmp_subdir`` pointing elsewhere, the
      api-server log route reads, and the intake route plans its temp
      file, under that configured root -- the answer team/scripts/lib/
      temp.sh's pgai_temp_dir gives. The environment variable still wins
      when set.
  (2) Status codes: an unknown project and a missing key are 404 and an
      invalid field 422, on an ops-call action route (POST
      /operations/close) and on the show read (GET /show); a valid request
      is unchanged.
  (3) Timeout: a pane script that sleeps past [api]
      shell_out_timeout_seconds is stopped -- its whole process group --
      and the route answers the ``shell_out.timeout`` dual-channel refusal
      with HTTP 504 well inside the script's own sleep.
  (4) Kanban root: the metrics route reads the server's configured root even
      when PGAI_AGENT_KANBAN_ROOT_PATH names a different one.

Every root is a throwaway directory under tmp_path; the app runs in
process (fastapi TestClient), and the only scripts run are fixture scripts
written here.
"""

from __future__ import annotations

import pathlib
import time

import pytest

from pgai_agent_kanban.api.adapter import HTTP_GATEWAY_TIMEOUT, http_status_for, shell_out
from pgai_agent_kanban.api.config import kanban_temp_root
from pgai_agent_kanban.lib.error_contract import parse_error_contract

_PROJECT = "alpha"
_TASK = "CODER-20260101-001-present"


def _board(tmp_path: pathlib.Path, *, kanban_cfg: str = "") -> pathlib.Path:
    root = tmp_path / "board"
    task = root / "projects" / _PROJECT / "tasks" / _TASK
    task.mkdir(parents=True)
    (task / "status.md").write_text("# Status\n\n## State\nBACKLOG\n", encoding="utf-8")
    (task / "README.md").write_text("# Task\n", encoding="utf-8")
    (root / "projects" / _PROJECT / "tasks" / "queues").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    if kanban_cfg:
        (root / "kanban.cfg").write_text(kanban_cfg, encoding="utf-8")
    return root


def _client(root: pathlib.Path, **cfg: object):
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    config = ApiConfig(host="127.0.0.1", port=0, kanban_root=root, **cfg)  # type: ignore[arg-type]
    return TestClient(create_app(cfg=config), base_url="http://127.0.0.1", raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No inherited root or temp dir reaches the app; tests set what they need."""
    monkeypatch.delenv("PGAI_AGENT_KANBAN_TEMP_DIR", raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path / "elsewhere"))
    monkeypatch.delenv("KANBAN_ROOT", raising=False)


# ---------------------------------------------------------------------------
# (1) temp root
# ---------------------------------------------------------------------------


def test_routes_use_the_configured_paths_temp_root(tmp_path: pathlib.Path) -> None:
    configured = tmp_path / "configured-tmp"
    root = _board(tmp_path, kanban_cfg=f"[paths]\ntmp_root = {configured}\ntmp_subdir = kanban\n")
    temp = configured / "kanban"
    assert kanban_temp_root(root) == temp

    (temp / "api").mkdir(parents=True)
    (temp / "api" / "api-server.log").write_text("SENTINEL-CONFIGURED-API-LOG\n", encoding="utf-8")
    client = _client(root)

    log = client.get("/logs/api-server")
    assert log.status_code == 200, log.text
    assert "SENTINEL-CONFIGURED-API-LOG" in log.json()["stdout"], log.json()

    planned = client.post(
        "/operations/intake",
        json={"project": _PROJECT, "filename": "BUG-0001-x.md", "content": "x\n", "dry_run": True},
    )
    assert planned.status_code == 200, planned.text
    assert f"{temp}/intake/" in planned.text, f"the planned temp file must sit under {temp}/intake: {planned.text}"


def test_the_environment_variable_still_wins(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _board(tmp_path, kanban_cfg=f"[paths]\ntmp_root = {tmp_path / 'cfg'}\ntmp_subdir = k\n")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "from-env"))
    assert kanban_temp_root(root) == tmp_path / "from-env"


def test_no_config_reads_as_the_documented_last_resort(tmp_path: pathlib.Path) -> None:
    from pgai_agent_kanban.api import config

    assert kanban_temp_root(_board(tmp_path)) == pathlib.Path(config._FALLBACK_TEMP_ROOT)


# ---------------------------------------------------------------------------
# (2) status codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"project": "no-such-project", "key": _TASK}, 404),
        ({"project": _PROJECT, "key": "CODER-20260101-999-absent"}, 404),
        ({"project": "a/b", "key": _TASK}, 422),
    ],
    ids=["unknown-project", "missing-key", "invalid-field"],
)
def test_close_refusals_carry_their_own_status(tmp_path: pathlib.Path, body: dict, status: int) -> None:
    response = _client(_board(tmp_path)).post("/operations/close", json=body)

    assert response.status_code == status, response.text
    envelope = response.json()
    assert envelope.get("error") and envelope.get("remedy"), envelope


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"project": "no-such-project", "key": _TASK}, 404),
        ({"project": _PROJECT, "key": "CODER-20260101-999-absent"}, 404),
        ({"project": "../x", "key": _TASK}, 422),
        ({"project": _PROJECT, "key": _TASK}, 200),
    ],
    ids=["unknown-project", "missing-key", "invalid-field", "present"],
)
def test_show_answers_404_422_or_200(tmp_path: pathlib.Path, params: dict, status: int) -> None:
    response = _client(_board(tmp_path)).get("/show", params=params)
    assert response.status_code == status, response.text


def test_an_unexpected_failure_is_still_500() -> None:
    assert http_status_for(1) == 500
    assert http_status_for(1, error_key="close.refused") == 500
    assert http_status_for(0, error_key="x.not_found") == 200


# ---------------------------------------------------------------------------
# (3) timeout
# ---------------------------------------------------------------------------


def _sleeping_script(path: pathlib.Path, pid_file: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/bash\nsleep 120 &\necho $! > '{pid_file}'\nwait\n", encoding="utf-8")
    path.chmod(0o755)


def _gone(pid: int, within_s: float = 10.0) -> bool:
    """True once *pid* has exited (gone, or a zombie awaiting its reaper) within *within_s*."""
    deadline = time.monotonic() + within_s
    while True:
        try:
            state = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rpartition(")")[2].split()[0]
        except (FileNotFoundError, ProcessLookupError, IndexError):
            return True
        if state in ("Z", "X"):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def test_shell_out_past_its_timeout_returns_the_timeout_refusal(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "slow.sh"
    pid_file = tmp_path / "child.pid"
    _sleeping_script(script, pid_file)

    started = time.monotonic()
    result = shell_out(str(script), {"project": _PROJECT}, timeout_seconds=1)
    elapsed = time.monotonic() - started

    assert elapsed < 30, f"the call must end near its 1s bound, took {elapsed:.1f}s"
    assert result.exit_code == 124
    assert http_status_for(result.exit_code, status=result.status) == HTTP_GATEWAY_TIMEOUT
    triple = parse_error_contract(result.stderr)
    assert triple and triple["error"] == "shell_out.timeout", result.stderr
    assert "1s" in triple["message"] and str(script) in triple["remedy"], triple
    assert _gone(int(pid_file.read_text())), "the script's process group must be stopped"


def test_a_pane_route_past_the_timeout_answers_504(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)
    _sleeping_script(root / "scripts" / "dashboard" / "show-header.sh", tmp_path / "pane.pid")
    client = _client(root, shell_out_timeout_seconds=1)

    started = time.monotonic()
    response = client.get("/dashboard/header", params={"project": _PROJECT})
    elapsed = time.monotonic() - started

    assert response.status_code == 504, response.text
    assert "shell_out.timeout" in response.json()["stderr"], response.json()
    assert elapsed < 30, f"the route must answer near its 1s bound, took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# (4) one kanban root
# ---------------------------------------------------------------------------


def test_metrics_reads_the_servers_root_not_the_environment(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)
    metrics = root / "projects" / _PROJECT / "metrics"
    metrics.mkdir(parents=True)
    (metrics / "history.csv").write_text("rc,tasks_total\nv1.0.0,3\n", encoding="utf-8")

    response = _client(root).get("/metrics", params={"project": _PROJECT, "format": "json"})

    assert response.status_code == 200, response.text
    assert [row.get("version") for row in response.json()] == ["v1.0.0"], response.json()

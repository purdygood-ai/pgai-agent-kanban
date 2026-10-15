"""
test_route_field_confinement.py — every operator API route refuses a
project, key or selector field that is not one plain name, before it acts.

The board is a throwaway kanban root under ``tmp_path`` with one registered
project, and beside it -- OUTSIDE the root -- a sentinel file and a sentinel
directory holding a ``status.md``, both reachable from the project's task
directory by a ``../``-style key. Every refused request must:

  1. return HTTP 422 with the four-key dual-channel body
     (``error``/``message``/``remedy``/``warnings``) whose machine key is
     ``<verb>.<field>_invalid`` and whose message names the field;
  2. carry a remedy that is a curl line with no placeholder;
  3. leave every file under ``tmp_path`` -- the sentinels and the board --
     byte-for-byte unchanged, and start no process.

Valid project names and task keys still reach the route and behave as
before (a close and a halt-after succeed; a show reads the document).
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api import adapter as adapter_module
from pgai_agent_kanban.api.routers import operations as operations_module

_PROJECT = "proj"
_TASK = "CODER-20260101-001-alpha"
_SENTINEL_TEXT = "sentinel: must never be read, rewritten or deleted\n"
_REFUSAL_KEYS = {"error", "message", "remedy", "warnings"}

# From <root>/projects/proj/tasks/ four '..' segments reach the directory
# that holds both the kanban root and the sentinels.
_KEY_TO_SENTINEL_DIR = "../../../../sentinel_dir"
_KEY_TO_SENTINEL_FILE = "../../../../sentinel"
_PROJECT_ESCAPE = "../.."


# ---------------------------------------------------------------------------
# Board, sentinels, client
# ---------------------------------------------------------------------------


def _status_md(task_id: str, state: str) -> str:
    return (
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nnone\n\n"
        "## Artifacts\nnone\n\n## Blockers\nnone\n\n## Needs Human\nno\n"
    )


@pytest.fixture
def outer(tmp_path: pathlib.Path) -> pathlib.Path:
    """The directory holding the kanban root and, beside it, the sentinels."""
    base = tmp_path / "outer"
    base.mkdir()
    (base / "sentinel.md").write_text(_SENTINEL_TEXT, encoding="utf-8")
    sentinel_dir = base / "sentinel_dir"
    sentinel_dir.mkdir()
    (sentinel_dir / "status.md").write_text(_status_md("SENTINEL", "WORKING"), encoding="utf-8")
    (sentinel_dir / "keep.txt").write_text(_SENTINEL_TEXT, encoding="utf-8")
    return base


@pytest.fixture
def board(outer: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A kanban root inside *outer* with one registered project and one task."""
    root = outer / "kanban"
    project = root / "projects" / _PROJECT
    queues = project / "tasks" / "queues"
    queues.mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (queues / "coder_backlog.md").write_text(f"# CODER Backlog\n\n- [ ] {_TASK}\n", encoding="utf-8")
    task_dir = project / "tasks" / _TASK
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "logs").mkdir()
    (task_dir / "README.md").write_text(f"# Task: {_TASK}\n\n## Role\nCODER\n", encoding="utf-8")
    (task_dir / "status.md").write_text(_status_md(_TASK, "BACKLOG"), encoding="utf-8")
    for sub in ("bugs", "priority", "requirements"):
        (project / sub).mkdir()
    (root / "logs").mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    return root


@pytest.fixture
def no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if a route starts any process."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"a refused request started a process: {args!r}")

    monkeypatch.setattr(operations_module, "_spawn_detached", _refuse)
    monkeypatch.setattr(operations_module, "shell_out", _refuse)
    monkeypatch.setattr(adapter_module, "shell_out", _refuse)
    monkeypatch.setattr(subprocess, "Popen", _refuse)
    monkeypatch.setattr(subprocess, "run", _refuse)


@pytest.fixture
def client(board: pathlib.Path) -> Generator[TestClient, None, None]:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=board)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as test_client:
        yield test_client


def _snapshot(base: pathlib.Path) -> dict[str, bytes | None]:
    """Every path under *base*, relative, with file bytes (None for a directory)."""
    return {
        str(path.relative_to(base)): (None if path.is_dir() else path.read_bytes())
        for path in sorted(base.rglob("*"))
    }


def _assert_component_refusal(response, verb: str, field: str) -> None:
    """Assert *response* is the 422 dual-channel refusal of *field* on *verb*."""
    assert response.status_code == 422, (response.status_code, response.text)
    body = response.json()
    assert set(body) == _REFUSAL_KEYS, f"refusal body keys: {sorted(body)}"
    assert body["error"] == f"{verb.replace('-', '_')}.{field}_invalid"
    assert f"'{field}'" in body["message"]
    assert body["remedy"].startswith("curl -sS ")
    assert "<" not in body["remedy"], f"remedy carries a placeholder: {body['remedy']}"


# ---------------------------------------------------------------------------
# The destructive and reading routes the confinement exists for
# ---------------------------------------------------------------------------


_DESTRUCTIVE_CASES = [
    pytest.param(
        "remove-project",
        {"project": "..", "force": True, "confirm": "remove-project .."},
        "project",
        id="remove-project-dotdot-force",
    ),
    pytest.param(
        "remove-project",
        {"project": _PROJECT_ESCAPE, "force": True, "confirm": f"remove-project {_PROJECT_ESCAPE}"},
        "project",
        id="remove-project-escape-force",
    ),
    pytest.param("delete", {"project": _PROJECT, "key": _KEY_TO_SENTINEL_DIR, "force": True}, "key", id="delete-dir"),
    pytest.param("delete", {"project": _PROJECT, "key": _KEY_TO_SENTINEL_FILE, "force": True}, "key", id="delete-file"),
    pytest.param("close", {"project": _PROJECT, "key": _KEY_TO_SENTINEL_DIR}, "key", id="close"),
    pytest.param("wontdo", {"project": _PROJECT, "key": _KEY_TO_SENTINEL_DIR}, "key", id="wontdo"),
    pytest.param("reset", {"project": _PROJECT, "key": _KEY_TO_SENTINEL_DIR, "force": True}, "key", id="reset"),
    pytest.param("deep-verify", {"project": _PROJECT_ESCAPE}, "project", id="deep-verify-escape"),
    pytest.param("deep-verify", {"project": "--operator-approved"}, "project", id="deep-verify-flag"),
    pytest.param("cold-build-now", {"project": _PROJECT_ESCAPE}, "project", id="cold-build-now-escape"),
    pytest.param("cold-build-now", {"project": "--operator-approved"}, "project", id="cold-build-now-flag"),
]


@pytest.mark.parametrize("verb, body, field", _DESTRUCTIVE_CASES)
def test_action_route_refuses_escaping_field_and_leaves_sentinels(
    client: TestClient, outer: pathlib.Path, no_process: None, verb: str, body: dict, field: str
) -> None:
    before = _snapshot(outer)
    response = client.post(f"/operations/{verb}", json=body)
    _assert_component_refusal(response, verb, field)
    assert _snapshot(outer) == before, f"{verb} changed files although it refused"


@pytest.mark.parametrize(
    "path, verb, params",
    [
        pytest.param("/show", "show", {"project": _PROJECT, "key": _KEY_TO_SENTINEL_FILE}, id="show"),
        pytest.param(
            "/test-report", "test-report", {"project": _PROJECT, "key": _KEY_TO_SENTINEL_FILE}, id="test-report"
        ),
    ],
)
def test_read_route_refuses_traversal_key(
    client: TestClient, outer: pathlib.Path, no_process: None, path: str, verb: str, params: dict
) -> None:
    before = _snapshot(outer)
    response = client.get(path, params=params)
    _assert_component_refusal(response, verb, "key")
    assert _SENTINEL_TEXT not in response.text
    assert _snapshot(outer) == before


# ---------------------------------------------------------------------------
# Glob, flag and newline injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["*", "CODER-*", "CODER-2026010?-001-alpha", "[C]ODER", "-x", "--force"])
@pytest.mark.parametrize("verb", ["close", "delete"])
def test_glob_and_flag_shaped_keys_are_refused(
    client: TestClient, outer: pathlib.Path, no_process: None, verb: str, key: str
) -> None:
    before = _snapshot(outer)
    response = client.post(f"/operations/{verb}", json={"project": _PROJECT, "key": key})
    _assert_component_refusal(response, verb, "key")
    assert _snapshot(outer) == before


def test_halt_after_key_with_newline_writes_no_token(
    client: TestClient, board: pathlib.Path, outer: pathlib.Path, no_process: None
) -> None:
    before = _snapshot(outer)
    response = client.post("/operations/halt-after", json={"project": _PROJECT, "key": "rc\nrc:v9.9.9"})
    _assert_component_refusal(response, "halt-after", "key")
    assert not (board / "projects" / _PROJECT / "HALT-AFTER").exists()
    assert _snapshot(outer) == before


# ---------------------------------------------------------------------------
# Every route checks its fields (not just the destructive ones)
# ---------------------------------------------------------------------------


_EVERY_ACTION_ROUTE = [
    ("halt", {"project": _PROJECT_ESCAPE}, "project"),
    ("unhalt", {"project": _PROJECT_ESCAPE}, "project"),
    ("halt-after", {"project": _PROJECT_ESCAPE}, "project"),
    ("defer", {"project": _PROJECT_ESCAPE, "until": "v1.2.3"}, "project"),
    ("undefer", {"project": _PROJECT_ESCAPE}, "project"),
    ("reset", {"project": _PROJECT, "agent": "../coder"}, "agent"),
    ("reset", {"project": _PROJECT, "bug": "BUG-*"}, "bug"),
    ("reset", {"project": _PROJECT, "priority": "-p"}, "priority"),
    ("reset", {"project": _PROJECT, "requirement": "a/b"}, "requirement"),
    ("intake", {"project": _PROJECT_ESCAPE, "filename": "BUG-0001-x.md", "content": "# x"}, "project"),
    ("unwind-rc", {"project": _PROJECT, "key": "../v1.2.3", "confirm": True}, "key"),
    ("set-version-ceiling", {"project": _PROJECT_ESCAPE, "show": True}, "project"),
    ("create-project", {"project": _PROJECT_ESCAPE}, "project"),
    ("add-project", {"project": _PROJECT_ESCAPE}, "project"),
    ("remove-project", {"project": "."}, "project"),
    ("ship-rc", {"project": _PROJECT, "key": "v1.2.3\n", "confirm": "ship-rc proj v1.2.3\n"}, "key"),
    ("wake-now", {"agent": "--all"}, "agent"),
    ("cancel-rc", {"project": _PROJECT_ESCAPE, "key": "v1.2.3", "yes": True}, "project"),
    ("release-locks", {"release": "../locks/x"}, "release"),
    ("archive", {"project": _PROJECT, "restore": "../BUG-0001"}, "restore"),
]


@pytest.mark.parametrize(
    "verb, body, field",
    [pytest.param(v, b, f, id=f"{v}-{f}") for v, b, f in _EVERY_ACTION_ROUTE],
)
def test_every_action_route_refuses_an_unconfined_field(
    client: TestClient, outer: pathlib.Path, no_process: None, verb: str, body: dict, field: str
) -> None:
    before = _snapshot(outer)
    response = client.post(f"/operations/{verb}", json=body)
    _assert_component_refusal(response, verb, field)
    assert _snapshot(outer) == before


def test_dry_run_does_not_describe_a_refused_value(client: TestClient, no_process: None) -> None:
    response = client.post(
        "/operations/deep-verify", json={"project": "--operator-approved", "dry_run": True}
    )
    _assert_component_refusal(response, "deep-verify", "project")
    assert "would execute" not in response.text


_READ_ROUTES = [
    ("/test-report", "test-report", {"key": "v1.2.3"}),
    ("/metrics", "metrics", {}),
    ("/metrics", "metrics", {"format": "json"}),
    ("/costs", "costs", {}),
    ("/costs", "costs", {"format": "json"}),
    ("/agent-metrics", "agent-metrics", {}),
    ("/status", "status", {}),
    ("/rejected", "rejected", {}),
    ("/show", "show", {"key": _TASK}),
    ("/approvals", "approvals", {}),
    ("/auto-resolutions", "auto-resolutions", {}),
    ("/verdicts", "verdicts", {}),
    ("/show-queues", "show-queues", {}),
    ("/verify-rc-state", "verify-rc-state", {}),
    ("/release-state", "release-state", {}),
    ("/deep-verify-status", "deep-verify-status", {}),
    ("/dashboard/metrics", "dashboard-pane", {}),
]


@pytest.mark.parametrize(
    "path, verb, extra",
    [pytest.param(p, v, e, id=f"{v}{'-json' if e.get('format') else ''}") for p, v, e in _READ_ROUTES],
)
def test_read_routes_refuse_a_traversal_project(
    client: TestClient, outer: pathlib.Path, no_process: None, path: str, verb: str, extra: dict
) -> None:
    before = _snapshot(outer)
    response = client.get(path, params={"project": _PROJECT_ESCAPE, **extra})
    _assert_component_refusal(response, verb, "project")
    assert _snapshot(outer) == before


def test_costs_refuses_an_escaping_rc_selector(client: TestClient, no_process: None) -> None:
    response = client.get("/costs", params={"project": _PROJECT, "rc": "../../x", "format": "json"})
    _assert_component_refusal(response, "costs", "rc")


def test_read_remedy_resends_the_request_with_an_example_value(client: TestClient) -> None:
    response = client.get("/status", params={"project": "-x"})
    _assert_component_refusal(response, "status", "project")
    remedy = response.json()["remedy"]
    assert "/status?project=my-app" in remedy
    assert "-x" not in remedy


def test_task_readme_path_parameters_are_checked(client: TestClient, no_process: None) -> None:
    response = client.get(f"/projects/{_PROJECT}/tasks/CODER-*/readme")
    _assert_component_refusal(response, "task-readme", "key")
    assert f"/projects/{_PROJECT}/tasks/v1.2.3/readme" in response.json()["remedy"]


# ---------------------------------------------------------------------------
# Valid names still reach the route
# ---------------------------------------------------------------------------


def test_valid_key_still_closes_the_task(client: TestClient, board: pathlib.Path) -> None:
    response = client.post("/operations/close", json={"project": _PROJECT, "key": _TASK})
    assert response.status_code == 200, response.text
    status = (board / "projects" / _PROJECT / "tasks" / _TASK / "status.md").read_text(encoding="utf-8")
    assert "## State\nDONE" in status


def test_valid_halt_after_key_still_arms_the_signal(client: TestClient, board: pathlib.Path) -> None:
    response = client.post("/operations/halt-after", json={"project": _PROJECT, "key": "rc"})
    assert response.status_code == 200, response.text
    token = (board / "projects" / _PROJECT / "HALT-AFTER").read_text(encoding="utf-8")
    assert token.strip() == "rc"


def test_valid_show_still_reads_the_document(client: TestClient) -> None:
    response = client.get("/show", params={"project": _PROJECT, "key": _TASK})
    assert response.status_code == 200, response.text
    assert "## State\nBACKLOG" in response.json()["stdout"]

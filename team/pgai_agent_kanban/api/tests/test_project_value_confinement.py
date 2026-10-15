"""
test_project_value_confinement.py — POST /operations/create-project and
/add-project refuse, at HTTP 422 naming the field, every value the registry
verbs' ops functions refuse: a workflow type that is not one plain name, a
project.cfg value holding a newline or other control character, a relative
or ``..``-containing dev tree, and an add-project name a projects.cfg
section cannot hold.

The board is a throwaway kanban root under ``tmp_path``; beside it --
OUTSIDE the root -- sit a sentinel directory and a ready-looking workflow
plugin at the path a ``../../tmp/x`` workflow type names. Every refused
request must:

  1. return HTTP 422 with the four-key dual-channel body
     (``error``/``message``/``remedy``/``warnings``) whose machine key is
     ``<verb>.<field>_invalid`` and whose message names the field;
  2. carry a remedy that is a curl line with no placeholder;
  3. leave every file under ``tmp_path`` byte-for-byte unchanged, dry run
     or not, and start no process.

A valid create (the ``release`` workflow, absolute dev tree) and a valid
add still succeed.
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api import adapter as adapter_module
from pgai_agent_kanban.api.routers import operations as operations_module

_REFUSAL_KEYS = {"error", "message", "remedy", "warnings"}
_READY_PLUGIN_CFG = "[workflow]\nstatus = ready\n[capabilities]\ngit_mode = none\nagents = pm,coder\n"


@pytest.fixture
def outer(tmp_path: pathlib.Path) -> pathlib.Path:
    """The directory holding the kanban root, a sentinel directory and a planted plugin."""
    base = tmp_path / "outer"
    (base / "x").mkdir(parents=True)
    (base / "x" / "keep.txt").write_text("sentinel\n", encoding="utf-8")
    plugin = base / "tmp" / "x"
    plugin.mkdir(parents=True)
    (plugin / "workflow.cfg").write_text(_READY_PLUGIN_CFG, encoding="utf-8")
    (plugin / "workflow.sh").write_text("#!/bin/bash\necho planted\n", encoding="utf-8")
    return base


@pytest.fixture
def board(outer: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A kanban root inside *outer*: one registered project and one unregistered directory."""
    root = outer / "kanban"
    (root / "projects" / "proj" / "tasks" / "queues").mkdir(parents=True)
    (root / "projects" / "loose").mkdir()
    (root / "projects.cfg").write_text("[project:proj]\npriority = 1\n", encoding="utf-8")
    (root / "logs").mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.fixture
def client(board: pathlib.Path) -> Generator[TestClient, None, None]:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=board)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if a route starts any process."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"a refused request started a process: {args!r}")

    monkeypatch.setattr(operations_module, "shell_out", _refuse)
    monkeypatch.setattr(adapter_module, "shell_out", _refuse)
    monkeypatch.setattr(subprocess, "Popen", _refuse)
    monkeypatch.setattr(subprocess, "run", _refuse)


def _snapshot(base: pathlib.Path) -> dict[str, bytes | None]:
    """Every path under *base*, relative, with file bytes (None for a directory)."""
    return {
        str(path.relative_to(base)): (None if path.is_dir() else path.read_bytes())
        for path in sorted(base.rglob("*"))
        if "server_tmp" not in path.parts
    }


def _assert_value_refusal(response, verb: str, field: str) -> dict:
    """Assert *response* is the 422 dual-channel refusal of *field* on *verb*."""
    assert response.status_code == 422, (response.status_code, response.text)
    body = response.json()
    assert set(body) == _REFUSAL_KEYS, f"refusal body keys: {sorted(body)}"
    assert body["error"] == f"{verb}.{field}_invalid"
    assert f"'{field}'" in body["message"]
    assert body["remedy"].startswith("curl -sS ")
    assert "<" not in body["remedy"], f"remedy carries a placeholder: {body['remedy']}"
    return body


_CREATE_REFUSALS = [
    ("workflow_type", "../../tmp/x", "contains a path separator"),
    ("workflow_type", "..", "names the current or a parent directory"),
    ("workflow_type", "release\n", "control character"),
    ("dev_tree", "/srv/x\n[release]\nmax_patch = 999", "control character (U+000A)"),
    ("dev_tree", "relative/path", "is not an absolute path"),
    ("dev_tree", "/srv/../etc", "contains a '..' segment"),
    ("git_repo", "git@example.com:x.git\nbranch_prefix = evil", "control character (U+000A)"),
    ("git_remote", "origin\npush_to_remote = true", "control character (U+000A)"),
]


@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry-run"])
@pytest.mark.parametrize("field, value, reason", _CREATE_REFUSALS)
def test_create_project_refuses_the_value_at_422_naming_the_field(
    client, outer, no_process, dry_run, field, value, reason
) -> None:
    before = _snapshot(outer)

    response = client.post(
        "/operations/create-project", json={"project": "alpha", field: value, "dry_run": dry_run}
    )

    body = _assert_value_refusal(response, "create_project", field)
    assert reason in body["message"]
    assert _snapshot(outer) == before


@pytest.mark.parametrize("name", ["a.b", "a b", "my app"])
def test_add_project_refuses_an_unregistrable_name_at_422(client, outer, no_process, name) -> None:
    before = _snapshot(outer)

    response = client.post("/operations/add-project", json={"project": name})

    _assert_value_refusal(response, "add_project", "project")
    assert _snapshot(outer) == before


def test_add_project_refuses_a_traversal_name_at_422_and_changes_nothing(client, outer, no_process) -> None:
    before = _snapshot(outer)

    response = client.post("/operations/add-project", json={"project": "../x"})

    _assert_value_refusal(response, "add_project", "project")
    assert _snapshot(outer) == before


def test_a_valid_create_and_add_still_succeed(client, board) -> None:
    created = client.post(
        "/operations/create-project",
        json={"project": "alpha", "workflow_type": "release", "dev_tree": "/srv/develop/alpha"},
    )
    added = client.post("/operations/add-project", json={"project": "loose"})

    assert created.status_code == 200, created.text
    assert "dev_tree_path = /srv/develop/alpha\n" in (
        board / "projects" / "alpha" / "project.cfg"
    ).read_text(encoding="utf-8")
    assert added.status_code == 200, added.text
    registry = (board / "projects.cfg").read_text(encoding="utf-8")
    assert "[project:alpha]" in registry and "[project:loose]" in registry

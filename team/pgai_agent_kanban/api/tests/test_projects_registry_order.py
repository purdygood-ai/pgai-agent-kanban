"""
test_projects_registry_order.py
===============================
``ops.read.list_projects()`` and ``GET /projects`` return the same project
names in the same order — projects.cfg priority order, which here is neither
alphabetical nor file order — because both read through
``pgai_agent_kanban.lib.ini.projects_registry``.

The fixture kanban root lives under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import IoError
from pgai_agent_kanban.ops.read import list_projects

# Priority order: charlie(1), alpha(2), bravo(2, later in file), delta(no
# priority -> 999). Alphabetical and file order both differ from it.
_PROJECTS_CFG = """\
[project:bravo]
priority=2

[project:delta]
description=unprioritised

[project:alpha]
priority=2

[project:charlie]
priority=1
"""
_PRIORITY_ORDER = ["charlie", "bravo", "alpha", "delta"]


@pytest.fixture
def registry_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "projects.cfg").write_text(_PROJECTS_CFG, encoding="utf-8")
    # A project directory that is not registered must not be listed.
    (root / "projects" / "unregistered").mkdir(parents=True)
    for name in _PRIORITY_ORDER:
        (root / "projects" / name).mkdir()
    return root


@pytest.fixture
def registry_client(
    registry_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(registry_root))
    monkeypatch.setenv("KANBAN_ROOT", str(registry_root))
    app = create_app(cfg=ApiConfig(host="127.0.0.1", port=0, kanban_root=registry_root))
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_list_projects_and_get_projects_agree_in_priority_order(
    registry_root: pathlib.Path, registry_client: TestClient
) -> None:
    from_ops = list_projects(OpsContext(kanban_root=registry_root))
    response = registry_client.get("/projects")
    assert response.status_code == 200
    from_api = response.json()["projects"]
    assert from_ops == from_api == _PRIORITY_ORDER
    assert _PRIORITY_ORDER != sorted(_PRIORITY_ORDER)


def test_list_projects_without_projects_cfg_is_empty(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "orphan").mkdir(parents=True)
    assert list_projects(OpsContext(kanban_root=tmp_path)) == []


def test_list_projects_malformed_projects_cfg_raises_io_error(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects.cfg").write_text("[project:a]\n[project:a]\n", encoding="utf-8")
    with pytest.raises(IoError, match="projects.cfg"):
        list_projects(OpsContext(kanban_root=tmp_path))

"""
test_project_name_rules_route_and_ops.py
========================================
remove-project and create-project refuse a project name the same way in a
dry run, in a real call, and in the ops function the CLI reaches.

  - POST /operations/remove-project runs ``ops.projects.check_project_name``
    (what ``remove_project`` runs) before its confirm gate and dry run, so
    ``my.app`` is 422 ``remove_project.project_invalid`` with and without
    ``dry_run``.
  - POST /operations/create-project runs ``ops.projects.check_create_values``,
    which now carries create_project's own name rule (a letter, then
    letters, digits, '-' and '_'), so ``my.app`` is 422
    ``create_project.project_invalid`` with and without ``dry_run``, and
    ``create_project`` itself refuses it with the same key.
  - Valid names behave as before: their dry runs answer 200.

The API runs in process against a throwaway board under tmp_path; no
script is run (refusals happen first, and dry runs only describe).
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.ops import projects as ops_projects
from tests.fixtures.route_cli_parity import build_api_client

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REGISTERED = "alpha"


@pytest.fixture
def client(tmp_path: pathlib.Path):
    root = tmp_path / "board"
    (root / "projects" / _REGISTERED).mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_REGISTERED}]\npriority = 1\n", encoding="utf-8")
    (root / "scripts").symlink_to(_TEAM_DIR / "scripts")
    return build_api_client(root)


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real"])
@pytest.mark.parametrize(
    ("route", "body", "key"),
    [
        ("/operations/remove-project", {"project": "my.app"}, "remove_project.project_invalid"),
        ("/operations/create-project", {"project": "my.app"}, "create_project.project_invalid"),
        ("/operations/create-project", {"project": "1app"}, "create_project.project_invalid"),
    ],
    ids=["remove-dot", "create-dot", "create-leading-digit"],
)
def test_an_invalid_name_is_422_in_both_modes(client, route: str, body: dict, key: str, dry_run: bool) -> None:
    payload = dict(body, dry_run=dry_run)
    if route.endswith("remove-project") and not dry_run:
        payload.update(force=True, confirm=f"remove-project {body['project']}")
    response = client.post(route, json=payload)

    assert response.status_code == 422, response.text
    envelope = response.json()
    assert envelope["error"] == key, envelope
    assert envelope["remedy"] and "<" not in envelope["remedy"], envelope


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("/operations/remove-project", {"project": _REGISTERED, "dry_run": True}),
        ("/operations/create-project", {"project": "my-app", "dry_run": True}),
    ],
    ids=["remove-registered", "create-new"],
)
def test_a_valid_name_still_reaches_the_dry_run(client, route: str, body: dict) -> None:
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text


def test_create_project_refuses_the_name_with_the_routes_key() -> None:
    with pytest.raises(ops_projects.ProjectValueRefused) as caught:
        ops_projects.check_create_values(project="my.app")
    assert "create_project.project_invalid" in str(caught.value)
    assert caught.value.field == "project"
    ops_projects.check_create_values(project="my-app")  # a valid name passes

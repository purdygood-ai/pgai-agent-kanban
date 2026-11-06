"""
test_projects_reliability.py — GET /projects/{name}/reliability.

The route returns a registered project's metrics/reliability.json as stored
(plus ``warnings``) and never computes or writes it.

Covered:
  - A registered project with a fixture reliability.json: 200, and the body
    minus ``warnings`` equals the file's JSON object.
  - The body equals what ``reliability_view json`` (the reader
    ``metrics-report.sh --reliability --format json`` runs) prints.
  - An unregistered name: 404 ``{"error": "project not found", "name": ...}``,
    as GET /projects/{name} answers.
  - A registered project with no reliability.json: 404 ``reliability not
    computed`` with the refresh command as ``remedy``; no file is created.
  - A reliability.json that does not parse: 422 ``reliability unreadable``;
    the file is left byte-identical.
  - An unknown query parameter is surfaced in ``warnings`` and the read
    still answers 200.

Every kanban root is a throwaway board under pytest's tmp_path.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib

import pytest

from pgai_agent_kanban.metrics import reliability_view
from tests.fixtures.route_cli_parity import build_api_client

_WITH_FILE = "rel-project"
_WITHOUT_FILE = "rel-fresh-project"
_UNREADABLE = "rel-broken-project"

# A reliability.json in the schema pgai_agent_kanban.metrics.reliability
# writes: one human block resolved, one any-class block resolved since.
_RELIABILITY_DOC = {
    "clean": {
        "class": "any",
        "hours": 7.25,
        "last_block": {
            "cause": "chain",
            "class": "any",
            "escalated": False,
            "reason_code": "agent_unclassified",
            "rc": "v1.4.0",
            "resolution": {
                "class": "any",
                "reason_code": "requeue",
                "time": "2026-10-17T19:55:00+00:00",
            },
            "source": "task:CODER-20261017-001-example",
            "task": "CODER-20261017-001-example",
            "time": "2026-10-17T19:40:00+00:00",
        },
        "rcs": 2,
        "since": "2026-10-17T19:55:00+00:00",
        "status": "resolved",
    },
    "computed_at": "2026-10-18T03:10:00+00:00",
    "first_record_at": "2026-09-20T02:22:14+00:00",
    "hands_off": {
        "class": "human",
        "hours": 240.5,
        "last_block": {
            "cause": "chain",
            "class": "human",
            "escalated": True,
            "reason_code": "agent_unclassified",
            "rc": "v1.2.0",
            "resolution": {
                "class": "human",
                "reason_code": "reset",
                "time": "2026-10-08T03:31:00+00:00",
            },
            "source": "task:CM-20261008-001-example",
            "task": "CM-20261008-001-example",
            "time": "2026-10-08T01:00:00+00:00",
        },
        "rcs": 12,
        "since": "2026-10-08T03:31:00+00:00",
        "status": "resolved",
    },
    "project": _WITH_FILE,
    "schema_version": 1,
}


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _reliability_file(root: pathlib.Path, project: str) -> pathlib.Path:
    return root / "projects" / project / "metrics" / "reliability.json"


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A board registering three projects: one with a reliability.json,
    one with none, one with a file that does not parse."""
    root = tmp_path / "reliability_root"
    _write(
        root / "projects.cfg",
        "".join(
            f"[project:{name}]\npriority={i}\n\n"
            for i, name in enumerate((_WITH_FILE, _WITHOUT_FILE, _UNREADABLE), start=1)
        ),
    )
    for name in (_WITH_FILE, _WITHOUT_FILE, _UNREADABLE):
        (root / "projects" / name / "metrics").mkdir(parents=True, exist_ok=True)
    _write(
        _reliability_file(root, _WITH_FILE),
        json.dumps(_RELIABILITY_DOC, indent=2, sort_keys=True) + "\n",
    )
    _write(_reliability_file(root, _UNREADABLE), "{not json\n")
    return root


@pytest.fixture
def client(kanban_root: pathlib.Path):
    with build_api_client(kanban_root) as test_client:
        yield test_client


def test_registered_project_returns_the_file(client, kanban_root: pathlib.Path) -> None:
    path = _reliability_file(kanban_root, _WITH_FILE)
    before = path.read_bytes()

    resp = client.get(f"/projects/{_WITH_FILE}/reliability")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.pop("warnings") == []
    assert body == json.loads(before)
    assert path.read_bytes() == before, "the read route must not rewrite reliability.json"


def test_body_matches_the_cli_json_reader(client, kanban_root: pathlib.Path) -> None:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exit_code = reliability_view.main(["json", "--kanban-root", str(kanban_root), _WITH_FILE])
    assert exit_code == 0

    body = client.get(f"/projects/{_WITH_FILE}/reliability").json()
    body.pop("warnings")
    assert body == json.loads(out.getvalue())


def test_unregistered_project_returns_404_echoing_the_name(client) -> None:
    resp = client.get("/projects/does-not-exist/reliability")

    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"] == "project not found"
    assert body["name"] == "does-not-exist"
    assert body["warnings"] == []


def test_registered_project_without_file_is_reliability_not_computed(client, kanban_root: pathlib.Path) -> None:
    path = _reliability_file(kanban_root, _WITHOUT_FILE)

    resp = client.get(f"/projects/{_WITHOUT_FILE}/reliability")

    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"] == "reliability not computed"
    assert body["name"] == _WITHOUT_FILE
    assert str(path) in body["message"]
    assert body["remedy"] == (
        "python3 -m pgai_agent_kanban.metrics.reliability refresh"
        f" --project {_WITHOUT_FILE} --kanban-root {kanban_root}"
    )
    assert body["warnings"] == []
    assert not path.exists(), "the read route must not compute or write reliability.json"


def test_unreadable_file_is_422_and_left_alone(client, kanban_root: pathlib.Path) -> None:
    path = _reliability_file(kanban_root, _UNREADABLE)
    before = path.read_bytes()

    resp = client.get(f"/projects/{_UNREADABLE}/reliability")

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"] == "reliability unreadable"
    assert body["name"] == _UNREADABLE
    assert "--project " + _UNREADABLE in body["remedy"]
    assert path.read_bytes() == before


def test_unknown_query_parameter_is_a_warning(client) -> None:
    resp = client.get(f"/projects/{_WITH_FILE}/reliability?bob=1")

    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == ["unknown parameter: bob"]

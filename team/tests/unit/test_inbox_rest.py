"""
test_inbox_rest.py
==================
Tests for the operator inbox's REST twins, ``GET /projects/{name}/inbox``
and ``POST /operations/inbox-answer``, against the CLI twin
``team/scripts/inbox.sh`` on the same sandbox board:

  - GET returns the same open entries ``inbox.sh list`` prints under
    ``## Open`` (and the closed ones under ``done``), read through the one
    module both surfaces call.
  - POST records the answer; the next GET and the next ``inbox.sh list``
    both reflect it, and ``stdout`` is the entry text the CLI prints.
  - A refusal returns the dual-channel body -- the same error / message /
    remedy the CLI prints for the same call -- and writes nothing; a body
    with no ``project`` is refused at 422 before anything is read.
  - ``dry_run`` describes the equivalent CLI call and writes nothing.

Every test builds its own sandbox kanban root under pytest's ``tmp_path``
and hands it to the API as its kanban root (and to the CLI through a
rebuilt environment). Nothing here points either surface at the live
install.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPT = _TEAM_DIR / "scripts" / "inbox.sh"
_PROJECT = "demo-proj"
_ANSWER_ROUTE = "/operations/inbox-answer"
_LIST_ROUTE = f"/projects/{_PROJECT}/inbox"
_REFUSAL_KEYS = {"exit_code", "stdout", "stderr", "error", "message", "remedy", "warnings"}


# ---------------------------------------------------------------------------
# Sandbox and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root with one registered project directory."""
    sandbox = tmp_path / "root"
    (sandbox / "projects" / _PROJECT).mkdir(parents=True)
    (sandbox / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    return sandbox


@pytest.fixture
def client(root: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """A TestClient for the real app whose kanban root is the sandbox."""
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        monkeypatch.delenv(name, raising=False)
    return TestClient(create_app(cfg=ApiConfig(host="127.0.0.1", port=0, kanban_root=root)))


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _write(root: pathlib.Path, question: str) -> inbox.InboxEntry:
    """Write one open question through the module, the inbox's only writer."""
    return inbox.write_entry(
        _ctx(root),
        _PROJECT,
        source="rc:v1.2.3",
        question=question,
        meanwhile="Left the RC open; the queue kept running.",
        how_to_answer='Answer "cancel" or "keep".',
    )


def _inbox_bytes(root: pathlib.Path) -> bytes | None:
    path = inbox.inbox_path(_ctx(root), _PROJECT)
    return path.read_bytes() if path.exists() else None


def _cli(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run the real inbox.sh against the sandbox *root* under a rebuilt environment."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp_dir = tmp_path / "framework-tmp"
    temp_dir.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        },
    )
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _cli_open_ids(root: pathlib.Path, tmp_path: pathlib.Path) -> list[str]:
    """The entry IDs ``inbox.sh list`` prints under ``## Open``, in order."""
    result = _cli(root, tmp_path, "--project", _PROJECT, "list")
    assert result.returncode == 0, result.stderr
    open_part = result.stdout.partition("## Done")[0]
    return [line[4:].strip() for line in open_part.splitlines() if line.startswith("### ")]


def _triple(body: dict) -> dict:
    return {key: body[key] for key in ("error", "message", "remedy")}


# ---------------------------------------------------------------------------
# GET /projects/{name}/inbox
# ---------------------------------------------------------------------------


def test_get_returns_the_open_entries_the_cli_lists(root, client, tmp_path):
    first = _write(root, "Cancel the RC?")
    second = _write(root, "Rebundle the late fix?")
    third = _write(root, "Retire the old baseline?")
    inbox.close_entry(_ctx(root), _PROJECT, second.entry_id)

    response = client.get(_LIST_ROUTE)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"project", "open", "done", "warnings"}
    assert body["project"] == _PROJECT
    assert body["warnings"] == []
    open_ids = [entry["entry_id"] for entry in body["open"]]
    assert open_ids == [first.entry_id, third.entry_id]
    assert open_ids == _cli_open_ids(root, tmp_path)
    assert [entry["entry_id"] for entry in body["done"]] == [second.entry_id]
    assert body == {"project": _PROJECT, **inbox.read_inbox(_ctx(root), _PROJECT).to_dict(), "warnings": []}


def test_get_resolves_a_project_prefix_like_the_cli(root, client):
    entry = _write(root, "Cancel the RC?")
    body = client.get("/projects/demo/inbox").json()
    assert body["project"] == _PROJECT
    assert [e["entry_id"] for e in body["open"]] == [entry.entry_id]


def test_get_with_no_inbox_file_is_empty_and_creates_nothing(root, client):
    response = client.get(_LIST_ROUTE)
    assert response.status_code == 200, response.text
    assert response.json()["open"] == [] and response.json()["done"] == []
    assert _inbox_bytes(root) is None


def test_get_unregistered_project_is_refused_with_the_clis_triple(root, client, tmp_path):
    response = client.get("/projects/no-such-project/inbox")

    assert response.status_code == 404, response.text
    body = response.json()
    assert set(body) == {"project", "error", "message", "remedy", "warnings"}
    assert body["error"] == "project_resolve.not_found"
    cli = _cli(root, tmp_path, "--project", "no-such-project", "list")
    assert parse_error_contract(cli.stderr) == _triple(body)


def test_get_registered_project_without_directory_is_refused(root, client):
    with (root / "projects.cfg").open("a", encoding="utf-8") as fh:
        fh.write("[project:ghost-proj]\npriority = 2\nenabled = true\n")
    response = client.get("/projects/ghost-proj/inbox")
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "inbox.unknown_project"
    assert not (root / "projects" / "ghost-proj").exists()


# ---------------------------------------------------------------------------
# POST /operations/inbox-answer
# ---------------------------------------------------------------------------


def test_post_records_the_answer_and_the_next_get_and_cli_list_reflect_it(root, client, tmp_path):
    entry = _write(root, "Cancel the RC?")

    response = client.post(_ANSWER_ROUTE, json={"project": _PROJECT, "id": entry.entry_id, "answer": "keep"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"exit_code", "stdout", "stderr", "warnings"}
    assert body["exit_code"] == 0
    answered = inbox.list_open(_ctx(root), _PROJECT)[0]
    assert answered.answer == "keep" and answered.answered_at
    assert body["stdout"] == inbox.render_entry(answered) + "\n"

    listed = client.get(_LIST_ROUTE).json()["open"]
    assert [(e["entry_id"], e["answer"]) for e in listed] == [(entry.entry_id, "keep")]
    cli = _cli(root, tmp_path, "--project", _PROJECT, "list")
    assert "- **Answer:** keep" in cli.stdout.partition("## Done")[0]


def test_post_answer_is_what_the_cli_prints_for_the_same_entry(root, client, tmp_path):
    entry = _write(root, "Cancel the RC?")
    rest = client.post(_ANSWER_ROUTE, json={"project": _PROJECT, "id": entry.entry_id, "answer": "keep"}).json()
    cli = _cli(root, tmp_path, "--project", _PROJECT, "answer", "--id", entry.entry_id, "--answer", "keep")
    assert cli.returncode == 0, cli.stderr
    # The second answer replaces the first; only its timestamp differs.
    rest_lines = [line for line in rest["stdout"].splitlines() if "Answered at" not in line]
    cli_lines = [line for line in cli.stdout.splitlines() if "Answered at" not in line]
    assert rest_lines == cli_lines


@pytest.mark.parametrize(
    ("body_extra", "cli_args", "error", "status"),
    [
        (
            {"id": "INBOX-0099", "answer": "keep"},
            ("--id", "INBOX-0099", "--answer", "keep"),
            "inbox.unknown_entry",
            404,
        ),
        ({"id": "zz", "answer": "keep"}, ("--id", "zz", "--answer", "keep"), "inbox.bad_entry_id", 409),
        ({"id": "INBOX-0001", "answer": "  "}, ("--id", "INBOX-0001", "--answer", "  "), "inbox.empty_answer", 409),
        ({"id": "INBOX-0001"}, ("--id", "INBOX-0001"), "inbox.empty_answer", 409),
    ],
)
def test_post_refusal_carries_the_clis_triple_and_writes_nothing(
    root, client, tmp_path, body_extra, cli_args, error, status
):
    _write(root, "Cancel the RC?")
    before = _inbox_bytes(root)

    response = client.post(_ANSWER_ROUTE, json={"project": _PROJECT, **body_extra})

    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == _REFUSAL_KEYS
    assert body["error"] == error
    assert "<" not in body["remedy"]
    assert _inbox_bytes(root) == before, "a refused answer changed the inbox file"
    cli = _cli(root, tmp_path, "--project", _PROJECT, "answer", *cli_args)
    assert cli.returncode == body["exit_code"]
    assert parse_error_contract(cli.stderr) == _triple(body)


def test_post_on_a_closed_entry_is_refused(root, client):
    entry = _write(root, "Cancel the RC?")
    inbox.close_entry(_ctx(root), _PROJECT, entry.entry_id)
    before = _inbox_bytes(root)
    response = client.post(_ANSWER_ROUTE, json={"project": _PROJECT, "id": entry.entry_id, "answer": "keep"})
    assert response.json()["error"] == "inbox.entry_closed"
    assert _inbox_bytes(root) == before


def test_post_unregistered_project_is_refused_with_the_resolver_block(root, client):
    _write(root, "Cancel the RC?")
    before = _inbox_bytes(root)
    response = client.post(_ANSWER_ROUTE, json={"project": "no-such-project", "id": "1", "answer": "keep"})
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "project_resolve.not_found"
    assert response.json()["exit_code"] == 1
    assert _inbox_bytes(root) == before


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"id": "INBOX-0001", "answer": "keep"}, "inbox.project_required"),
        ({"project": "", "id": "INBOX-0001", "answer": "keep"}, "inbox.project_required"),
        ({"project": "../demo-proj", "id": "INBOX-0001", "answer": "keep"}, "inbox.project_invalid"),
    ],
)
def test_post_without_a_usable_project_is_refused_at_422_before_anything_is_read(root, client, body, error):
    _write(root, "Cancel the RC?")
    before = _inbox_bytes(root)
    response = client.post(_ANSWER_ROUTE, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"] == error
    assert {"error", "message", "remedy", "warnings"} <= set(response.json())
    assert _inbox_bytes(root) == before


def test_post_dry_run_describes_the_cli_call_and_writes_nothing(root, client):
    entry = _write(root, "Cancel the RC?")
    before = _inbox_bytes(root)
    response = client.post(
        _ANSWER_ROUTE, json={"project": _PROJECT, "id": entry.entry_id, "answer": "keep", "dry_run": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["stdout"] == (
        f"dry-run: would execute inbox.sh answer --project {_PROJECT} --id {entry.entry_id} --answer keep"
    )
    assert _inbox_bytes(root) == before


def test_post_unknown_body_field_is_reported_in_warnings(root, client):
    entry = _write(root, "Cancel the RC?")
    response = client.post(
        _ANSWER_ROUTE, json={"project": _PROJECT, "id": entry.entry_id, "answer": "keep", "anwser": "x"}
    )
    assert response.status_code == 200, response.text
    assert any("anwser" in warning for warning in response.json()["warnings"])

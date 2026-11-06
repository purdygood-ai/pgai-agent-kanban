"""
test_rest_fallback_remedy_installed_path.py
===========================================
A refused operator route whose script printed no remedy of its own answers
with a fallback remedy that names the installed script under the kanban root
the API serves -- ``<kanban root>/scripts/<script>`` -- shell-quoted, so the
command runs as printed from any directory. The same holds for the remedies
the halt-after, cancel-rc and release-locks routes build by hand.

Every kanban root is a throwaway directory under tmp_path whose path holds a
space, so an unquoted path would split into two words. The app runs in
process (fastapi TestClient); no script is started.
"""

from __future__ import annotations

import pathlib
import shlex

import pytest

from pgai_agent_kanban.api.adapter import ShellResult
from pgai_agent_kanban.ops import locks as ops_locks
from pgai_agent_kanban.ops.errors import IoError
from pgai_agent_kanban.overwatch.halt_provenance import halt_command, installed_script_command

_PROJECT = "demo"
_TASK = "CODER-20260101-001-absent"
_RERUN = "Fix the condition the message names, then re-run the CLI form: "


def _board(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root with a space in its path that registers *_PROJECT* but has no
    project directory, so the halt and close routes refuse with plain text."""
    root = tmp_path / "kanban root"
    root.mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    return root


def _client(root: pathlib.Path):
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    config = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
    return TestClient(create_app(cfg=config), base_url="http://127.0.0.1", raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No inherited kanban root reaches the app; each test serves its own board."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path / "kanban root"))
    for name in ("KANBAN_ROOT", "PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT"):
        monkeypatch.delenv(name, raising=False)


def _refusal(response) -> dict:
    """The refusal envelope, after checking it carries the dual-channel triple."""
    envelope = response.json()
    assert response.status_code >= 400, f"expected a refusal, got {response.status_code}: {envelope}"
    assert envelope.get("error") and envelope.get("message") and envelope.get("remedy"), envelope
    return envelope


def _command_part(remedy: str, lead: str = _RERUN) -> str:
    """The command a fallback remedy names: the text after its lead-in sentence."""
    assert remedy.startswith(lead), f"remedy does not start with {lead!r}: {remedy!r}"
    return remedy[len(lead) :]


def test_installed_script_command_names_the_script_under_the_root_shell_quoted(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban root"

    assert installed_script_command(root, "halt.sh", "--project", _PROJECT) == halt_command(root, _PROJECT)
    assert installed_script_command(root, "release-locks.sh") == shlex.quote(str(root / "scripts" / "release-locks.sh"))
    words = shlex.split(installed_script_command(root, "cm/cancel-rc.sh", "--key", "v1.2.3", "--note", "two words"))
    assert words == [str(root / "scripts" / "cm" / "cancel-rc.sh"), "--key", "v1.2.3", "--note", "two words"]


def test_halt_fallback_remedy_is_the_installed_halt_command(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)

    envelope = _refusal(_client(root).post("/operations/halt", json={"project": _PROJECT}))

    assert envelope["error"] == "halt.refused", envelope
    command = _command_part(envelope["remedy"])
    assert command == halt_command(root, _PROJECT)
    assert shlex.quote(str(root / "scripts" / "halt.sh")) in command
    assert shlex.split(command)[0] == str(root / "scripts" / "halt.sh")


def test_non_halt_fallback_remedy_names_the_installed_script(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)

    envelope = _refusal(_client(root).post("/operations/close", json={"project": _PROJECT, "key": _TASK}))

    assert envelope["error"] == "close.refused", envelope
    command = _command_part(envelope["remedy"])
    assert shlex.quote(str(root / "scripts" / "close.sh")) in command
    assert shlex.split(command) == [str(root / "scripts" / "close.sh"), "--project", _PROJECT, "--key", _TASK]


def test_fallback_remedy_names_an_absolute_path_for_a_relative_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _board(tmp_path)
    monkeypatch.chdir(tmp_path)

    envelope = _refusal(_client(pathlib.Path(root.name)).post("/operations/halt", json={"project": _PROJECT}))

    assert shlex.split(_command_part(envelope["remedy"]))[0] == str(root / "scripts" / "halt.sh")


def test_cancel_rc_fallback_remedy_names_the_installed_cm_script(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)

    response = _client(root).post("/operations/cancel-rc", json={"project": _PROJECT, "key": "v1.2.3", "yes": True})

    envelope = _refusal(response)
    assert envelope["error"] == "cancel_rc.refused", envelope
    lead = (
        "Fix the condition the message names, then re-run the CLI form, "
        "which prints the full plan and prompts before changing anything: "
    )
    words = shlex.split(_command_part(envelope["remedy"], lead))
    assert words == [str(root / "scripts" / "cm" / "cancel-rc.sh"), "--project", _PROJECT, "--key", "v1.2.3"]


def test_halt_after_fallback_remedy_names_the_installed_script(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pgai_agent_kanban.api.routers import operations

    root = _board(tmp_path)
    monkeypatch.setattr(
        operations, "_halt_after_result", lambda *_args: ShellResult(1, "", "halt-after.sh: refused in plain text\n")
    )

    response = _client(root).post("/operations/halt-after", json={"project": _PROJECT, "key": "rc", "until": "v1.2.3"})

    envelope = _refusal(response)
    assert envelope["error"] == "halt_after.refused", envelope
    words = shlex.split(_command_part(envelope["remedy"]))
    assert words == [
        str(root / "scripts" / "halt-after.sh"),
        "--project",
        _PROJECT,
        "--key",
        "rc",
        "--until",
        "v1.2.3",
    ]


def test_halt_after_scope_mismatch_remedy_names_the_installed_script(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path)

    response = _client(root).post(
        "/operations/halt-after", json={"project": _PROJECT, "key": "tester", "until": "v1.2.3"}
    )

    envelope = _refusal(response)
    assert envelope["error"] == "halt-after.until_scope_mismatch", envelope
    script = str(root / "scripts" / "halt-after.sh")
    scope_command = installed_script_command(root, "halt-after.sh", "--key", "tester")
    rc_command = installed_script_command(root, "halt-after.sh", "--project", "your-project", "--until", "vX.Y.Z")
    assert f"({scope_command})" in envelope["remedy"], envelope["remedy"]
    assert f"({rc_command})" in envelope["remedy"], envelope["remedy"]
    assert shlex.split(scope_command)[0] == script


def test_release_locks_fallback_remedy_names_the_installed_script(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _board(tmp_path)

    def _fail(*_args, **_kwargs):
        raise IoError("release-locks.sh: could not swap the lock file")

    monkeypatch.setattr(ops_locks, "release_lock", _fail)

    envelope = _refusal(_client(root).post("/operations/release-locks", json={"release": "repo-wake-demo"}))

    assert envelope["error"] == "release_locks.refused", envelope
    listing = installed_script_command(root, "release-locks.sh")
    rerun = installed_script_command(root, "release-locks.sh", "--release", "repo-wake-demo")
    assert envelope["remedy"] == (f"Run {listing} with no flags to list every lock by name, then re-run: {rerun}")
    assert shlex.split(rerun)[0] == str(root / "scripts" / "release-locks.sh")

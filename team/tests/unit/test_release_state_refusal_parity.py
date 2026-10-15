"""
test_release_state_refusal_parity.py
=====================================
The two renderers of a release-state refusal -- the operator CLI
(scripts/release-state.sh, exit 3, house-shape block on stderr) and its REST
twin (GET /release-state, HTTP 422 JSON body) -- print the same three
fields for the same refusal, and the remedy is never empty on either.

Both surfaces resolve through ``pgai_agent_kanban.ops.read.get_release_state``
and render the ``Refused`` they get back through one shared function
(``pgai_agent_kanban.lib.error_contract.dual_channel_from_text``) fed one
shared fallback key/remedy pair (``pgai_agent_kanban.ops.read``). This
module is the golden pair for that contract: the real script is run as a
subprocess against a throwaway board and its stderr parsed with the lib's
own reader; the real route is called through FastAPI's TestClient against
the same board; the three fields are compared byte for byte.

Covers, for the missing-file refusal and for a malformed-field refusal:
  - the CLI exits 3 and prints a house-shape block whose remedy is non-empty;
  - the REST twin answers 422 with the same machine key;
  - error, message and remedy are byte-equal across the two surfaces.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_RELEASE_STATE_SH = _TEAM_DIR / "scripts" / "release-state.sh"
_PROJECT = "gf"

_MALFORMED_RELEASE_STATE = (
    "## Active RC\nNOTAVERSION\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nnone\n"
)


def _build_board(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "board"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\ndescription=golden pair\nenabled=true\n",
        encoding="utf-8",
    )
    return root


def _cli_refusal(root: pathlib.Path) -> tuple[int, dict | None, str]:
    """Run the real release-state.sh against *root*; return (exit status,
    parsed house-shape triple or None, raw stderr)."""
    env = {k: v for k, v in os.environ.items() if k not in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT")}
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    result = subprocess.run(
        ["bash", str(_RELEASE_STATE_SH), "--project", _PROJECT],
        capture_output=True, text=True, env=env, cwd=str(_TEAM_DIR), timeout=60,
    )
    return result.returncode, parse_error_contract(result.stderr), result.stderr


def _rest_refusal(root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict]:
    """Call the real GET /release-state route against *root* through the
    TestClient; return (status code, JSON body)."""
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    client = TestClient(create_app(cfg=ApiConfig(kanban_root=root)), raise_server_exceptions=True)
    response = client.get("/release-state", params={"project": _PROJECT})
    return response.status_code, response.json()


@pytest.mark.parametrize(
    "case, release_state_text",
    [
        pytest.param("missing-file", None, id="missing-file"),
        pytest.param("malformed-field", _MALFORMED_RELEASE_STATE, id="malformed-field"),
    ],
)
def test_cli_and_rest_render_the_same_non_empty_remedy(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    release_state_text: str | None,
) -> None:
    root = _build_board(tmp_path)
    if release_state_text is not None:
        (root / "projects" / _PROJECT / "release-state.md").write_text(
            release_state_text, encoding="utf-8"
        )

    cli_status, cli, cli_stderr = _cli_refusal(root)
    rest_status, rest = _rest_refusal(root, monkeypatch)

    assert cli_status == 3, (
        f"[{case}] release-state.sh must exit 3 on a validation refusal; got {cli_status}\n{cli_stderr}"
    )
    assert cli is not None, (
        f"[{case}] release-state.sh must print the house-shape error/message/remedy block "
        f"on stderr; got:\n{cli_stderr!r}"
    )
    assert cli["remedy"].strip(), (
        f"[{case}] the CLI's remedy: line must not be empty.\n{cli_stderr}"
    )
    assert rest_status == 422, f"[{case}] GET /release-state must answer 422; got {rest_status}: {rest}"
    assert rest["error"] == "release-state.invalid-field", rest

    for field in ("error", "message", "remedy"):
        assert cli[field] == rest[field], (
            f"[{case}] the CLI and REST twins must render the same {field!r} for the same "
            f"refusal.\n  CLI : {cli[field]!r}\n  REST: {rest[field]!r}"
        )
